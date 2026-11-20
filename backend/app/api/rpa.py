"""RPA 通道：没有官方接口的平台，靠浏览器自动化收发。

背景：官方宣讲会明确允许「无 API 时用 RPA 模拟鼠标键盘操作」。
飞书网页版就是这种场景——应用身份只能收「发给机器人的消息」，
要发给人、发给任意联系人，只能以用户身份操作网页。

分工：
  * 后端不做浏览器操作，只把待发任务挂成 status='rpa'；
  * 采集器（scripts/rpa-send.mjs）轮询 pending，操作网页版，再回来 ack。

这样后端保持无状态、可容器化；换平台只要换采集器。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import outbox

router = APIRouter(prefix="/api/v1/rpa", tags=["rpa"])


class AckIn(BaseModel):
    outbox_id: int
    ok: bool
    platform_msg_id: str | None = None
    error: str | None = None


@router.get("/pending")
async def pending(limit: int = 10):
    """采集器取待发任务。返回的任务保持 rpa 状态，取走后不会重复发。"""
    items = await outbox.list_rpa_pending(limit=max(1, min(limit, 50)))
    return {"items": items, "count": len(items)}


@router.post("/ack")
async def ack(body: AckIn):
    """采集器回报发送结果。成功会照常落出站消息、把父消息标 replied。"""
    found = await outbox.ack_rpa(
        body.outbox_id,
        ok=body.ok,
        platform_msg_id=body.platform_msg_id,
        error=body.error,
    )
    if not found:
        raise HTTPException(404, f"outbox {body.outbox_id} 不在 rpa 状态（可能已被处理）")
    return {"ok": True, "outbox_id": body.outbox_id, "result": "sent" if body.ok else "failed"}
