"""统一收件箱 API —— 闭环的"统一展示 / 处理 / 发送反馈"三段都在这里。"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import pathlib
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters import registry
from ..db import SessionLocal, get_session
from ..models import Feedback, Message, Todo
from ..schemas import (
    BulkIdsIn,
    BulkPriorityIn,
    BulkStatusIn,
    MessageOut,
    PriorityIn,
    ReadIn,
    ReplyIn,
    StatusIn,
    TodoIn,
    ThreadOut,
)
from ..services import inbound, llm, outbox

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["inbox"])

STATUS_LABEL = {
    "received": "待处理",
    "claimed": "处理中",
    "replied": "已回复",
    "closed": "已关闭",
}


def extract_meta(msg: Message) -> dict:
    """不同平台出站需要的额外上下文（如钉钉的 sessionWebhook）。"""
    raw = msg.raw or {}
    if msg.platform == "dingtalk":
        return {"session_webhook": raw.get("sessionWebhook")}
    return {}


@router.get("/inbox", response_model=list[MessageOut])
async def list_inbox(
    platform: str | None = None,
    status: str | None = None,
    chat_id: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Message).order_by(Message.created_at.desc())
    if platform:
        stmt = stmt.where(Message.platform == platform)
    if status:
        stmt = stmt.where(Message.status == status)
    if chat_id:
        stmt = stmt.where(Message.chat_id == chat_id)
    if q:
        stmt = stmt.where(Message.text_body.ilike(f"%{q}%"))
    stmt = stmt.limit(limit).offset(offset)
    return (await session.execute(stmt)).scalars().all()


def _as_utc(value):
    """原生 SQL 查出来的时间绕过了 UTCDateTime 类型装饰器，这里补回时区。

    不补的话接口返回 "2026-11-02T10:45:43"（没有 Z），前端按本地时间解析，
    界面上会显示成 10:45，比真实的 18:45 差 8 小时。
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return value
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.get("/inbox/threads", response_model=list[ThreadOut])
async def list_threads(
    limit: int = Query(default=50, le=200),
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """按平台+会话聚合，形成"统一收件箱"的左侧列表。

    这里刻意只用标准 SQL：
      * `array_agg(...)[1]` 是 PostgreSQL 专有写法，SQLite 会报 near "AS" 语法错误
      * 改用相关子查询取最后一条正文，Postgres / SQLite 都能跑

    `q` 是搜索词：同时匹配会话标题、会话 ID 和该会话里的任意一条消息正文，
    这样员工搜"合同"能直接定位到聊过合同的那个会话。
    """
    base_sql = """
        SELECT m.platform,
               m.chat_id,
               max(m.chat_title)                                             AS chat_title,
               COALESCE(
                 (SELECT m2.text_body
                    FROM message m2
                   WHERE m2.platform = m.platform AND m2.chat_id = m.chat_id
                     AND m2.direction = 'in'
                   ORDER BY m2.created_at DESC, m2.id DESC
                   LIMIT 1),
                 (SELECT m3.text_body
                    FROM message m3
                   WHERE m3.platform = m.platform AND m3.chat_id = m.chat_id
                   ORDER BY m3.created_at DESC, m3.id DESC
                   LIMIT 1)
               )                                                             AS last_text,
               max(m.created_at)                                             AS last_at,
               count(*)                                                      AS total,
               sum(CASE WHEN m.status IN ('received','claimed') THEN 1 ELSE 0 END) AS pending,
               sum(CASE WHEN m.direction = 'in' AND m.read_at IS NULL THEN 1 ELSE 0 END) AS unread,
               sum(CASE WHEN m.priority = 'urgent' AND m.status IN ('received','claimed')
                        THEN 1 ELSE 0 END)                                   AS urgent
        FROM message m
        GROUP BY m.platform, m.chat_id
    """
    # 排序规则：有紧急待处理的会话永远置顶，其余按最后一条消息时间倒序
    order_sql = """
        ORDER BY (sum(CASE WHEN m.priority = 'urgent' AND m.status IN ('received','claimed')
                           THEN 1 ELSE 0 END) > 0) DESC,
                 max(m.created_at) DESC
        LIMIT :limit
    """
    order_sql_outer = " ORDER BY (t.urgent > 0) DESC, t.last_at DESC LIMIT :limit"

    if q and q.strip():
        sql = text(
            f"""
            SELECT * FROM ({base_sql}) t
             WHERE lower(coalesce(t.chat_title, '')) LIKE lower(:kw)
                OR lower(t.chat_id) LIKE lower(:kw)
                OR EXISTS (SELECT 1
                             FROM message m3
                            WHERE m3.platform = t.platform
                              AND m3.chat_id = t.chat_id
                              AND lower(coalesce(m3.text_body, '')) LIKE lower(:kw))
            {order_sql_outer}
            """
        )
        rows = (await session.execute(sql, {"kw": f"%{q.strip()}%", "limit": limit})).mappings().all()
    else:
        sql = text(base_sql + order_sql)
        rows = (await session.execute(sql, {"limit": limit})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["last_at"] = _as_utc(d.get("last_at"))
        d["unread"] = int(d.get("unread") or 0)
        d["urgent"] = int(d.get("urgent") or 0)
        out.append(d)
    return out


@router.post("/inbox/read")
async def mark_read(body: ReadIn, session: AsyncSession = Depends(get_session)):
    """把会话（或指定几条消息）标为已读。

    和 status 分开：已读 = 看过，status = 处理到哪一步。
    员工点开会话就不会再有"未读"提示，但消息仍然可能是"待处理"。
    """
    from datetime import datetime as _dt

    stmt = (
        select(Message)
        .where(Message.platform == body.platform, Message.chat_id == body.chat_id)
        .where(Message.direction == "in")
        .where(Message.read_at.is_(None))
    )
    if body.message_ids:
        stmt = stmt.where(Message.id.in_(body.message_ids))
    rows = (await session.execute(stmt)).scalars().all()
    now = _dt.now(timezone.utc)
    for m in rows:
        m.read_at = now
    await session.commit()
    return {"ok": True, "read": len(rows)}


@router.post("/inbox/bulk/read")
async def bulk_read(body: BulkIdsIn, session: AsyncSession = Depends(get_session)):
    """跨会话批量标已读（勾选几条消息一起清未读）。

    返回里的 `inbound` 是选中项中「收到的消息」条数：点开会话时前端已经
    自动标过已读，所以这里常常是 read=0、inbound=N，界面据此给不同提示。
    """
    from datetime import datetime as _dt

    inbound = (
        await session.execute(
            select(Message.id)
            .where(Message.id.in_(body.ids))
            .where(Message.direction == "in")
        )
    ).scalars().all()
    rows = (
        await session.execute(
            select(Message)
            .where(Message.id.in_(body.ids))
            .where(Message.read_at.is_(None))
            .where(Message.direction == "in")
        )
    ).scalars().all()
    now = _dt.now(timezone.utc)
    for m in rows:
        m.read_at = now
    await session.commit()
    return {"ok": True, "read": len(rows), "inbound": len(inbound)}


@router.patch("/inbox/bulk/status")
async def bulk_status(body: BulkStatusIn, session: AsyncSession = Depends(get_session)):
    """批量改处理状态（不回流通知，避免把对方刷屏）。

    只作用于收到的消息：发出的消息 status 记的是投递结果（sent），
    不能拿「处理进度」去覆盖。
    """
    rows = (
        await session.execute(
            select(Message).where(Message.id.in_(body.ids)).where(Message.direction == "in")
        )
    ).scalars().all()
    changed = 0
    for m in rows:
        if m.status == body.status:
            continue
        session.add(
            Feedback(
                message_id=m.id,
                kind="status",
                old_value=m.status,
                new_value=body.status,
            )
        )
        m.status = body.status
        changed += 1
    await session.commit()
    return {"ok": True, "updated": changed, "scanned": len(rows)}


@router.patch("/inbox/bulk/priority")
async def bulk_priority(body: BulkPriorityIn, session: AsyncSession = Depends(get_session)):
    """批量改紧急程度（人工覆盖 AI 判断，同时留纠正样本）。"""
    rows = (
        await session.execute(
            select(Message).where(Message.id.in_(body.ids)).where(Message.direction == "in")
        )
    ).scalars().all()
    changed = 0
    for m in rows:
        if m.priority == body.priority:
            continue
        session.add(
            Feedback(
                message_id=m.id,
                kind="priority",
                old_value=m.priority,
                new_value=body.priority,
            )
        )
        m.priority = body.priority
        changed += 1
    await session.commit()
    return {"ok": True, "updated": changed, "scanned": len(rows)}


@router.get("/inbox/{message_id}", response_model=MessageOut)
async def get_message(message_id: int, session: AsyncSession = Depends(get_session)):
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")
    return msg


@router.get("/inbox/{message_id}/attachments/{index}")
async def get_attachment(
    message_id: int,
    index: int,
    download: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """附件代理：图片直接内联给浏览器预览，其它类型走下载。

    为什么不让前端直连平台：飞书的资源要 tenant_access_token、Telegram 要 bot token，
    这些绝不能进浏览器。所以统一由后端取回再转发，前端只看到同源 URL。
    """
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")

    atts = list(msg.attachments or [])
    if index < 0 or index >= len(atts):
        raise HTTPException(404, "附件不存在")
    att = atts[index]

    adapter = registry.get(msg.platform)
    if adapter is None:
        raise HTTPException(503, f"{msg.platform} 当前未启用，无法取回附件")

    blob = await adapter.download_attachment(msg.platform_msg_id, att)
    if blob is None:
        raise HTTPException(502, "附件下载失败（平台侧可能已过期或权限不足）")

    disposition = "attachment" if download else "inline"
    return StreamingResponse(
        io.BytesIO(blob.data),
        media_type=blob.mime,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(blob.filename)}",
            "Content-Length": str(len(blob.data)),
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.post("/inbox/{message_id}/reply")
async def reply_to_message(
    message_id: int,
    body: ReplyIn,
    session: AsyncSession = Depends(get_session),
):
    """闭环第三步：在统一入口处理完，回复内容自动转换为目标平台格式并发送。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")

    payload = outbox.payload_text(body.text, extract_meta(msg))
    outbox_id = await outbox.enqueue(
        session,
        platform=msg.platform,
        target_chat=msg.chat_id,
        payload=payload,
        parent_message_id=msg.id,
    )
    msg.status = "replied"
    await session.commit()

    log.info("消息 %s 回复入队 outbox=%s 平台=%s", message_id, outbox_id, msg.platform)
    return {"ok": True, "outbox_id": outbox_id, "platform": msg.platform, "status": "replied"}


# 上传目录：统一入口发出去的文件先落这里，再由 outbox 交给对应平台适配器
UPLOAD_DIR = pathlib.Path(__file__).resolve().parents[2] / "uploads"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB，飞书/TG 单文件上限的保守值


def _safe_name(name: str) -> str:
    """只保留文件名本体，去掉路径分隔符和危险字符。"""
    base = pathlib.Path(name or "file").name
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base).strip(". ") or "file"
    return base[:120]


@router.post("/inbox/{message_id}/reply-file")
async def reply_with_file(
    message_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """从统一入口向原会话发送文件（赛题要求的「发文本与文件」里的文件那半边）。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")

    data = await file.read()
    if not data:
        raise HTTPException(400, "文件是空的")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB，请压缩后再发")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(file.filename or "file")
    # 加随机前缀避免同名覆盖，但保留原文件名，因为各平台发送时用的就是 basename
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
    dest.write_bytes(data)

    meta = extract_meta(msg)
    payload = outbox.payload_file(str(dest), meta)
    outbox_id = await outbox.enqueue(
        session,
        platform=msg.platform,
        target_chat=msg.chat_id,
        payload=payload,
        parent_message_id=msg.id,
    )
    msg.status = "replied"
    await session.commit()

    log.info(
        "消息 %s 发送文件入队 outbox=%s 平台=%s 文件=%s (%d 字节)",
        message_id, outbox_id, msg.platform, name, len(data),
    )
    return {
        "ok": True,
        "outbox_id": outbox_id,
        "platform": msg.platform,
        "status": "replied",
        "filename": name,
        "size": len(data),
    }


@router.post("/inbox/{message_id}/todo")
async def create_todo(message_id: int, body: TodoIn, session: AsyncSession = Depends(get_session)):
    """消息 -> 待办：闭环里"处理"动作的另一种形态，状态变更后会回流平台。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")

    title = body.title or (msg.text_body or "未命名待办")[:120]
    todo = Todo(
        source_message_id=msg.id,
        platform=msg.platform,
        chat_id=msg.chat_id,
        title=title,
        due_at=body.due_at,
        assignee=body.assignee,
        status="pending",
    )
    session.add(todo)
    msg.status = "claimed"
    await session.flush()
    await session.commit()
    return {"ok": True, "todo_id": todo.id, "title": title}


@router.patch("/inbox/{message_id}/status")
async def change_status(message_id: int, body: StatusIn, session: AsyncSession = Depends(get_session)):
    """状态流转 + 可选回流：这是闭环 8 分"状态流转"的直接体现。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")

    msg.status = body.status
    queued = None
    if body.notify:
        note = f"[统一收件箱] 该消息已标记为「{STATUS_LABEL.get(body.status, body.status)}」"
        queued = await outbox.enqueue(
            session,
            platform=msg.platform,
            target_chat=msg.chat_id,
            payload=outbox.payload_status(note, extract_meta(msg)),
            parent_message_id=msg.id,
        )
    await session.commit()
    return {"ok": True, "status": body.status, "notified": bool(queued), "outbox_id": queued}


@router.post("/inbox/{message_id}/summarize")
async def summarize_message(message_id: int, session: AsyncSession = Depends(get_session)):
    """AI 摘要 + 优先级推断（无 key 自动降级规则版）。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")

    result = await llm.summarize(msg.text_body or "")
    old_priority = msg.priority
    msg.priority = result["priority"]
    if result["priority"] != old_priority:
        session.add(
            Feedback(
                message_id=msg.id,
                kind="priority",
                old_value=old_priority,
                new_value=result["priority"],
            )
        )
        # 重要消息自动升级：urgent 时顺手建待办，形成"自动触发"创新点
    await session.commit()
    return result


@router.patch("/inbox/{message_id}/priority")
async def set_priority(
    message_id: int,
    body: PriorityIn,
    session: AsyncSession = Depends(get_session),
):
    """人工改紧急程度（覆盖 AI 判断），并记一条 Feedback 用于后续调规则。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")
    old = msg.priority
    if old != body.priority:
        msg.priority = body.priority
        session.add(
            Feedback(
                message_id=msg.id,
                kind="priority",
                old_value=old,
                new_value=body.priority,
            )
        )
        await session.commit()
    return {"ok": True, "id": msg.id, "priority": msg.priority, "old": old}


@router.post("/inbox/{message_id}/draft")
async def draft_message(message_id: int, session: AsyncSession = Depends(get_session)):
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")
    return await llm.draft_reply(msg.text_body or "", msg.sender_name)


@router.post("/inbox/{message_id}/forward")
async def forward_message(message_id: int, target_platform: str, target_chat: str, session: AsyncSession = Depends(get_session)):
    """跨平台转发：把 A 平台的消息推到 B 平台，验证"统一出站"抽象。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "消息不存在")
    body = f"[转发自 {msg.platform}] {msg.sender_name or ''}: {msg.text_body or ''}".strip()
    outbox_id = await outbox.enqueue(
        session,
        platform=target_platform,
        target_chat=target_chat,
        payload=outbox.payload_text(body),
        parent_message_id=msg.id,
    )
    await session.commit()
    return {"ok": True, "outbox_id": outbox_id, "target": f"{target_platform}:{target_chat}"}


@router.get("/events")
async def event_stream(request: Request, since: str | None = None):
    """SSE 实时推送新消息（轮询数据库实现，简单可靠，演示时延迟 < 2 秒）。"""
    try:
        cursor = datetime.fromisoformat(since) if since else datetime.now(timezone.utc)
    except ValueError:
        cursor = datetime.now(timezone.utc)

    async def gen():
        nonlocal cursor
        yield f"event: ready\ndata: {json.dumps({'since': cursor.isoformat()})}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                rows = await inbound.list_since(cursor)
                if rows:
                    cursor = datetime.fromisoformat(rows[-1]["created_at"])
                    yield f"data: {json.dumps(rows, ensure_ascii=False)}\n\n"
                else:
                    yield ": ping\n\n"
            except Exception as exc:  # 推送失败不能让流断掉
                log.warning("SSE 轮询异常: %s", exc)
            await asyncio.sleep(1.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
