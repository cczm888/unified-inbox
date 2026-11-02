"""统计看板 API —— 支撑"闭环 8 分：状态流转与数据反馈"的可视化证据。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    by_platform = (
        await session.execute(
            text(
                """
                SELECT platform,
                       count(*)                                        AS total,
                       count(*) FILTER (WHERE direction='in')          AS inbound,
                       count(*) FILTER (WHERE status='replied')        AS replied,
                       count(*) FILTER (WHERE status IN ('received','claimed')) AS pending
                FROM message GROUP BY platform ORDER BY total DESC
                """
            )
        )
    ).mappings().all()

    by_status = (
        await session.execute(text("SELECT status, count(*) AS c FROM message GROUP BY status"))
    ).mappings().all()

    outbox_state = (
        await session.execute(text("SELECT status, count(*) AS c FROM outbox GROUP BY status"))
    ).mappings().all()

    todo_state = (
        await session.execute(text("SELECT status, count(*) AS c FROM todo GROUP BY status"))
    ).mappings().all()

    # 平均处理时长：从收到消息到发出反馈的秒数。
    # Postgres 用 extract(epoch ...)，SQLite 用 julianday 差值 ×86400。
    if session.get_bind().dialect.name == "sqlite":
        diff_expr = "(julianday(o.created_at) - julianday(m.created_at)) * 86400"
    else:
        diff_expr = "extract(epoch FROM (o.created_at - m.created_at))"
    avg_handle = (
        await session.execute(
            text(
                f"""
                SELECT coalesce(avg({diff_expr}), 0) AS avg_seconds
                FROM outbox o JOIN message m ON m.id = o.parent_message_id
                WHERE o.status = 'sent'
                """
            )
        )
    ).scalar_one()

    return {
        "by_platform": [dict(r) for r in by_platform],
        "by_status": {r["status"]: r["c"] for r in by_status},
        "outbox": {r["status"]: r["c"] for r in outbox_state},
        "todos": {r["status"]: r["c"] for r in todo_state},
        "avg_handle_seconds": round(float(avg_handle or 0), 1),
    }


@router.get("/recent-events")
async def recent_events(limit: int = 20, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            text(
                """
                SELECT platform, event_type, received_at
                FROM event_log ORDER BY received_at DESC LIMIT :limit
                """
            ),
            {"limit": min(limit, 200)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
