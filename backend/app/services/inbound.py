"""入站处理管道：事件留档 -> 归一化 -> 幂等去重 -> 落库 -> 实时推送。

这就是闭环里的"消息接收"环节，所有平台共用一条管道，
平台差异全部被适配器的 to_unified() 吸收掉了。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..adapters import UnifiedMessage, registry
from ..db import SessionLocal
from ..models import EventLog, Message

log = logging.getLogger(__name__)


async def handle_inbound(platform: str, raw: dict) -> int | None:
    """返回新消息 id；重复事件或忽略事件返回 None。"""
    adapter = registry.get(platform)
    if adapter is None:
        log.warning("收到未知平台事件: %s", platform)
        return None

    unified: UnifiedMessage | None = adapter.to_unified(raw)
    if unified is None:
        return None

    # 可选增强：把 open_id / staffId 换成人类可读的名字（失败不影响入库）
    try:
        await adapter.resolve_sender(unified)
    except Exception:
        log.warning("[%s] 解析发送者信息失败，按原始 ID 入库", platform, exc_info=True)

    event_type = (raw.get("header") or {}).get("event_type") or f"{platform}.message"

    async with SessionLocal() as session:
        await _log_event(session, platform, event_type, raw)

        row = unified.as_row()
        # 幂等去重：靠 (platform, platform_msg_id) 唯一约束 + on conflict do nothing。
        # Postgres 与 SQLite 的方言 insert 语法不同，这里按实际连接方言选择。
        inserter = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        stmt = (
            inserter(Message)
            .values(**row)
            .on_conflict_do_nothing(index_elements=["platform", "platform_msg_id"])
            .returning(Message.id)
        )
        new_id = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()

    if new_id is not None:
        log.info("[%s] 新消息入库 id=%s chat=%s", platform, new_id, unified.chat_id)
    return new_id


async def _log_event(session, platform: str, event_type: str, raw: dict) -> None:
    session.add(EventLog(platform=platform, event_type=event_type, raw=raw or {}))


async def backfill(platform: str, chat_id: str, limit: int = 50) -> dict:
    """拉会话历史并入库。

    实时事件只能收到「订阅之后」的消息，接入之前的老对话是空的。
    这里用各平台的历史接口补一次，走的是和实时事件完全相同的入库管道
    （同样的幂等去重、同样的字段映射），所以回填完看不出差别。
    """
    adapter = registry.get(platform)
    if adapter is None:
        return {"ok": False, "error": f"平台 {platform} 当前未接入", "fetched": 0, "inserted": 0}

    items = await adapter.fetch_history(chat_id, limit=limit)
    if not items:
        return {"ok": True, "fetched": 0, "inserted": 0, "note": "该平台不支持拉历史，或这个会话里没有消息"}

    inserted = 0
    async with SessionLocal() as session:
        inserter = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        for unified in items:
            try:
                await adapter.resolve_sender(unified)
            except Exception:
                log.warning("[%s] 回填时解析发送者失败", platform, exc_info=True)
            stmt = (
                inserter(Message)
                .values(**unified.as_row())
                .on_conflict_do_nothing(index_elements=["platform", "platform_msg_id"])
                .returning(Message.id)
            )
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                inserted += 1
        await session.commit()

    log.info("[%s] 回填 chat=%s 拉到 %s 条，新增 %s 条", platform, chat_id, len(items), inserted)
    return {"ok": True, "fetched": len(items), "inserted": inserted}


async def list_since(since: datetime, limit: int = 200) -> list[dict]:
    """给 SSE 用：拉取某时间点之后的新消息（轮询式实时推送，够用且不会出错）。"""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Message).where(Message.created_at > since).order_by(Message.created_at.asc()).limit(limit)
            )
        ).scalars().all()
    return [
        {
            "id": m.id,
            "platform": m.platform,
            "chat_id": m.chat_id,
            "sender_name": m.sender_name,
            "text": m.text_body,
            "created_at": (m.created_at or datetime.now(timezone.utc)).isoformat(),
        }
        for m in rows
    ]
