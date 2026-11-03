"""通用数据接入（ingest）。

为什么需要这个接口
------------------
官方 API 覆盖不了的场景有三类：

1. **平台没有开放 API**（飞书网页版里你能看到的会话，远多于机器人参与的会话）
2. **官方 API 不提供某类数据**（例如飞书 `get_as_user` 系列 scope 不向个人租户开放）
3. **历史数据已经在别处**（导出的 DOCX / CSV / 邮件归档）

这三类都不该靠「往适配器里塞爬虫」解决 —— 爬虫要浏览器登录态、选择器会随前端改版失效、
跑在后端进程里既不稳也不安全。正确做法是：**采集器在外面跑，把结构化结果 POST 进来**。

于是本接口只做一件事：把外部采集到的消息，按统一格式落进 `message` 表，
和官方适配器走同一条入库管道、同一套去重规则。上层（收件箱、待办、看板、搜索）
完全不需要知道这条消息来自 API 还是来自浏览器。

定位声明
--------
本接口进来的数据 **不计入赛题「官方开放 API 或 Webhook 接入的平台数量」**。
它是补充通道，不是主链路。文档与演示必须如实标注来源。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Message
from ..schemas import IngestIn

router = APIRouter(prefix="/api/v1", tags=["ingest"])
log = logging.getLogger(__name__)


@router.post("/ingest")
async def ingest(body: IngestIn, session: AsyncSession = Depends(get_session)) -> dict:
    """批量接收外部采集的消息。

    幂等：同 `(platform, platform_msg_id)` 重复推送不会产生新行，
    所以采集器可以放心地反复跑（重扫一个会话、补一次历史都不会脏数据）。
    """
    dialect = session.get_bind().dialect.name
    inserter = sqlite_insert if dialect == "sqlite" else pg_insert

    rows = []
    now = datetime.now(timezone.utc)
    for it in body.items:
        text = (it.text or "").strip()
        rows.append(
            {
                "platform": body.platform,
                "platform_msg_id": it.platform_msg_id,
                "chat_id": it.chat_id,
                "chat_type": "external",
                "chat_title": it.chat_name,
                "sender_id": it.sender_id,
                "sender_name": it.sender_name,
                "msg_type": "text",
                "text_body": text,
                "attachments": None,
                "direction": it.direction,
                "status": "sent" if it.direction == "out" else "received",
                "priority": "normal",
                "created_at": it.created_at or now,
                "raw": {"source": "ingest", "meta": body.meta or {}},
            }
        )

    inserted = 0
    if rows:
        stmt = (
            inserter(Message)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["platform", "platform_msg_id"])
            .returning(Message.id)
        )
        result = await session.execute(stmt)
        inserted = len(result.fetchall())
        await session.commit()

    log.info("ingest %s: %d received, %d inserted", body.platform, len(rows), inserted)
    return {
        "ok": True,
        "platform": body.platform,
        "received": len(rows),
        "inserted": inserted,
        "skipped": len(rows) - inserted,
    }
