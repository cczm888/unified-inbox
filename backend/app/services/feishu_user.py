"""用「用户身份」同步飞书数据：列会话 + 拉历史消息。

这是用户身份相对应用身份的核心增量：
  * 应用身份：只有机器人被 @ 的那几条，历史一条都看不到；
  * 用户身份：这个人加入的**所有群 + 所有单聊**都能列出来，
    每个会话还能往前翻页拉历史（`page_token`），一直到群创建那天。

同步策略（重要的工程细节）：**不预拉全量**。一个人的飞书可能有几百个群、
每个群几千条消息，全量拉要几万次请求，第一次打开应用就会卡死。
所以：
  1. 冷启动只同步「最近活跃的 N 个会话 × 每个最近 M 条」；
  2. 点开某个会话时再按需翻历史（懒加载）；
  3. 实时靠事件订阅补，定时任务做兜底。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Message, UserAccount

log = logging.getLogger(__name__)
FEISHU_BASE = "https://open.feishu.cn/open-apis"


def _ms(value) -> datetime:
    """飞书时间戳是毫秒字符串/整数。"""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if ms < 10_000_000_000:      # 秒级
        ms *= 1000
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _parse_body(msg_type: str, content: str) -> tuple[str | None, list]:
    """把飞书 body.content 拆成 (文本, 附件列表)。"""
    try:
        c = json.loads(content or "{}")
    except json.JSONDecodeError:
        return content, []
    if msg_type == "text":
        return c.get("text"), []
    if msg_type == "image":
        return "[图片]", [{"kind": "image", "key": c.get("image_key"), "name": "图片"}]
    if msg_type in ("file", "media", "audio"):
        return f"[文件] {c.get('file_name') or ''}".strip(), [
            {"kind": "file", "key": c.get("file_key"), "name": c.get("file_name") or "附件"}
        ]
    if msg_type == "post":
        # 富文本：把所有 text 段拼起来
        texts = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("tag") == "text" and node.get("text"):
                    texts.append(node["text"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(c)
        return "".join(texts) or "[富文本]", []
    if msg_type == "merge_forward":
        return "[合并转发]", []
    return f"[{msg_type}]", []


async def list_chats(token: str, page_size: int = 100) -> list[dict]:
    """这个人加入的全部会话（群 + 单聊）。用户身份才拿得到完整列表。"""
    out: list[dict] = []
    token_page = None
    async with httpx.AsyncClient(timeout=20) as cli:
        while True:
            params = {"page_size": page_size, "user_id_type": "open_id"}
            if token_page:
                params["page_token"] = token_page
            r = await cli.get(
                f"{FEISHU_BASE}/im/v1/chats",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            body = r.json() or {}
            if body.get("code") not in (0, None):
                raise RuntimeError(f"列会话失败 code={body.get('code')} msg={body.get('msg')}")
            data = body.get("data") or {}
            out.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            token_page = data.get("page_token")
            if not token_page:
                break
    return out


async def fetch_messages(
    token: str, chat_id: str, page_size: int = 50, page_token: str | None = None
) -> dict:
    """拉某个会话的历史消息（倒序，最新在前）。"""
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "page_size": min(page_size, 50),
        "sort_type": "ByCreateTimeDesc",
    }
    if page_token:
        params["page_token"] = page_token
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.get(
            f"{FEISHU_BASE}/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        body = r.json() or {}
        if body.get("code") not in (0, None):
            raise RuntimeError(f"拉消息失败 code={body.get('code')} msg={body.get('msg')}")
        return body.get("data") or {}


async def store_messages(
    session: AsyncSession,
    *,
    account: UserAccount,
    items: list[dict],
    chat_titles: dict[str, str] | None = None,
) -> int:
    """把飞书原始消息写进统一 message 表（幂等，重复同步不会产生重复行）。"""
    if not items:
        return 0
    dialect = session.get_bind().dialect.name
    inserter = sqlite_insert if dialect == "sqlite" else pg_insert
    titles = chat_titles or {}
    rows = []
    for it in items:
        if it.get("deleted"):
            continue
        sender = it.get("sender") or {}
        sender_id = sender.get("id")
        chat_id = it.get("chat_id") or ""
        text, atts = _parse_body(it.get("msg_type") or "text", (it.get("body") or {}).get("content"))
        if sender_id == account.user_id:
            direction = "out"
        else:
            direction = "in"
        rows.append(
            {
                "account_id": account.id,
                "platform": "feishu",
                "platform_msg_id": it.get("message_id") or "",
                "chat_id": chat_id,
                "chat_type": "p2p" if (it.get("chat_id") or "").startswith("oc_") and not titles.get(chat_id) else "group",
                "chat_title": titles.get(chat_id),
                "sender_id": sender_id,
                "sender_name": sender.get("name"),
                "msg_type": it.get("msg_type") or "text",
                "text_body": text,
                "attachments": atts,
                "direction": direction,
                "status": "received" if direction == "in" else "replied",
                "priority": "normal",
                "created_at": _ms(it.get("create_time")),
                "raw": it,
            }
        )
    if not rows:
        return 0
    stmt = inserter(Message).values(rows).on_conflict_do_nothing(
        index_elements=["platform", "platform_msg_id"]
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


async def sync_account(
    session: AsyncSession, account: UserAccount, token: str, *, chats: int = 20, per_chat: int = 50
) -> dict:
    """同步一个账号：列出会话，按最近活跃取前 N 个，各拉 M 条历史。"""
    all_chats = await list_chats(token)
    log.info("账号 %s 可见会话 %d 个", account.id, len(all_chats))
    titles = {c.get("chat_id"): c.get("name") for c in all_chats if c.get("chat_id")}
    # 单聊没有 name，用对方的 p2p 标识兜底
    for c in all_chats:
        if not titles.get(c.get("chat_id")):
            titles[c.get("chat_id")] = f"单聊 {c.get('chat_id')}"

    stored = 0
    used = 0
    for c in all_chats[:chats]:
        cid = c.get("chat_id")
        if not cid:
            continue
        try:
            data = await fetch_messages(token, cid, page_size=per_chat)
            stored += await store_messages(
                session, account=account, items=data.get("items") or [], chat_titles=titles
            )
            used += 1
        except Exception as exc:
            log.warning("同步会话 %s 失败: %s", cid, exc)

    account.last_sync_at = datetime.now(timezone.utc)
    await session.commit()
    return {"chats_visible": len(all_chats), "chats_synced": used, "messages_upserted": stored}
