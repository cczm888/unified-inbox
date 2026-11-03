"""待办 API。

待办状态可以回流到原平台（评审关注「状态流转」），但**默认不回执**：
改自己的待办进度不该往对话里塞「[统一收件箱] 待办…状态更新为…」这种机器人回声。
需要让对方知道进度时，请求里带 notify=true。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Feedback, Message, Todo
from ..schemas import TodoOut, TodoStatusIn
from ..services import outbox

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["todo"])

LABEL = {"pending": "待处理", "in_progress": "处理中", "done": "已完成", "cancelled": "已取消"}


@router.get("/todos", response_model=list[TodoOut])
async def list_todos(status: str | None = None, platform: str | None = None, session: AsyncSession = Depends(get_session)):
    stmt = select(Todo).order_by(Todo.created_at.desc())
    if status:
        stmt = stmt.where(Todo.status == status)
    if platform:
        stmt = stmt.where(Todo.platform == platform)
    return (await session.execute(stmt.limit(200))).scalars().all()


@router.patch("/todos/{todo_id}")
async def update_todo(
    todo_id: int,
    body: TodoStatusIn,
    notify: bool = False,
    session: AsyncSession = Depends(get_session),
):
    todo = await session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(404, "待办不存在")

    old = todo.status
    todo.status = body.status

    # 记一条反馈，供"数据反馈、持续优化"展示
    if old != body.status:
        session.add(Feedback(message_id=todo.source_message_id, kind="todo_status", old_value=old, new_value=body.status))

    queued = None
    if notify and todo.platform and todo.chat_id and old != body.status:
        note = f"[统一收件箱] 待办「{todo.title[:40]}」状态更新为：{LABEL.get(body.status, body.status)}"
        meta = {}
        if todo.source_message_id:
            src = await session.get(Message, todo.source_message_id)
            if src is not None:
                raw = src.raw or {}
                if src.platform == "dingtalk":
                    meta = {"session_webhook": raw.get("sessionWebhook")}
        queued = await outbox.enqueue(
            session,
            platform=todo.platform,
            target_chat=todo.chat_id,
            payload=outbox.payload_status(note, meta),
            parent_message_id=todo.source_message_id,
        )
    await session.commit()
    return {"ok": True, "status": body.status, "previous": old, "reflowed": bool(queued)}

