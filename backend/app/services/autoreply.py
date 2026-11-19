"""自动回复：入站消息命中规则后，自动通过 Outbox 回一条。

设计取舍
--------
1. **只回一次**：同一条入站消息只触发一条自动回复。靠 Outbox.parent_message_id
   指向入站消息这个关系判断，避免重连/重投递导致连回三条。
2. **走 Outbox**：不直接调平台发送接口。这样限流、重试、失败退避、状态回流
   全部复用已有链路，自动回复和人工回复在数据上没有任何区别。
3. **匹配朴素**：关键词命中（大小写不敏感）+ 平台过滤，priority 升序取第一条。
   规则要能在答辩现场被解释清楚，所以不用正则、不用语义匹配。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import SessionLocal
from ..models import AutoRule, Outbox
from . import outbox

log = logging.getLogger(__name__)

# 这三个状态说明"已经安排回过了"，不必再回
_ALIVE = ("queued", "sending", "sent")


def _matches(rule: AutoRule, platform: str, text: str) -> bool:
    if not rule.enabled:
        return False
    if rule.platform and rule.platform != platform:
        return False
    kw = (rule.keyword or "").strip()
    if not kw:
        return True  # 没写关键词 = 该平台的兜底规则
    return kw.lower() in (text or "").lower()


async def maybe_auto_reply(platform: str, chat_id: str, message_id: int, text: str) -> int | None:
    """命中规则就入队一条自动回复，返回 outbox id；没命中返回 None。"""
    if not (text or "").strip():
        return None  # 纯附件消息不自动回复，容易驴唇不对马嘴

    async with SessionLocal() as session:
        rules = (
            await session.execute(
                select(AutoRule).where(AutoRule.enabled.is_(True)).order_by(AutoRule.priority.asc(), AutoRule.id.asc())
            )
        ).scalars().all()
        if not rules:
            return None

        rule = next((r for r in rules if _matches(r, platform, text)), None)
        if rule is None:
            return None

        already = (
            await session.execute(
                select(Outbox.id)
                .where(Outbox.parent_message_id == message_id, Outbox.status.in_(_ALIVE))
                .limit(1)
            )
        ).scalar_one_or_none()
        if already is not None:
            return None

        job_id = await outbox.enqueue(
            session,
            platform=platform,
            target_chat=chat_id,
            payload=outbox.payload_text(rule.reply_text, {"auto_rule_id": rule.id, "auto": True}),
            parent_message_id=message_id,
        )

        rule.hit_count = (rule.hit_count or 0) + 1
        rule.last_hit_at = datetime.now(timezone.utc)
        await session.commit()

    log.info("[%s] 自动回复命中规则 #%s，outbox #%s", platform, rule.id, job_id)
    return job_id
