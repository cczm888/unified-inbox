"""出站投递 worker（Outbox 模式）。

为什么不在 HTTP 请求里直接调平台 SDK：
  * 平台 429/超时不会把用户的回复请求拖死；
  * 天然获得重试、指数退避、幂等；
  * 多进程部署时用 FOR UPDATE SKIP LOCKED 抢任务，不会重复发送。

状态机：queued -> sending -> sent / failed(重排) -> dead(超过最大次数)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..adapters import registry
from ..config import settings
from ..db import SessionLocal
from ..models import Message
from . import ratelimit

log = logging.getLogger(__name__)

def _claim_sql(dialect: str = "postgresql"):
    """抢占一批待发任务。

    Postgres 用 now() + FOR UPDATE SKIP LOCKED（多 worker / 多副本并发安全）；
    SQLite（本地零依赖演示）没有 SKIP LOCKED 也没有 now()，退化为 CURRENT_TIMESTAMP，
    单进程演示场景不存在真正的并发抢锁问题。
    """
    now_expr = "CURRENT_TIMESTAMP" if dialect == "sqlite" else "now()"
    lock_clause = "" if dialect == "sqlite" else "FOR UPDATE SKIP LOCKED"
    return text(
        f"""
    UPDATE outbox SET status = 'sending', attempts = attempts + 1
    WHERE id IN (
        SELECT id FROM outbox
        WHERE status = 'queued' AND next_retry_at <= {now_expr}
        ORDER BY id
        LIMIT :batch
        {lock_clause}
    )
    RETURNING id, platform, target_chat, payload, attempts, parent_message_id;
    """
    )


async def enqueue(
    session,
    *,
    platform: str,
    target_chat: str,
    payload: dict,
    parent_message_id: int | None = None,
) -> int:
    """业务侧统一调这个把消息塞进队列（回复、状态回流、转发都走它）。"""
    from ..models import Outbox

    row = Outbox(
        platform=platform,
        target_chat=target_chat,
        payload=payload,
        parent_message_id=parent_message_id,
        status="queued",
        next_retry_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row.id


async def recover_stale() -> int:
    """进程重启后回收"半路死掉"的任务。

    一个任务被 claim 后状态就是 sending，如果此时进程被 kill（或 worker 崩在
    dispatch 中途），这条任务就永远卡在 sending，再也没人管它。启动时统一放回
    queued 即可。

    注意：这一策略假定"启动时刻本进程不可能是别的实例的唯一持有者"，
    单机 / 单容器部署成立；如果将来要多副本共用一个库，需要改成基于
    heartbeat_at 的时间窗回收。
    """
    async with SessionLocal() as session:
        res = await session.execute(
            text("UPDATE outbox SET status='queued' WHERE status='sending'")
        )
        await session.commit()
        n = res.rowcount or 0
    if n:
        log.warning("回收了 %s 条卡在 sending 的投递任务", n)
    return n


async def worker_loop(stop_event: asyncio.Event, worker_id: int) -> None:
    log.info("outbox worker-%s started", worker_id)
    while not stop_event.is_set():
        try:
            claimed = await _claim(settings.outbox_workers * 4)
            if not claimed:
                await asyncio.sleep(0.6)
                continue
            for row in claimed:
                if stop_event.is_set():
                    break
                try:
                    await _dispatch(row)
                except Exception as exc:  # 单条异常不能拖垮整个 worker，更不能让任务卡在 sending
                    log.exception("outbox id=%s 派发异常", row["id"])
                    await _mark_failed(row, f"dispatch exception: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("outbox worker-%s 异常", worker_id)
            await asyncio.sleep(2)
    log.info("outbox worker-%s stopped", worker_id)


async def _claim(batch: int) -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(_claim_sql(session.get_bind().dialect.name), {"batch": batch})
        rows = []
        for r in result.fetchall():
            d = dict(r._mapping)
            # 这条是原生 SQL 的 RETURNING，绕过了 ORM 的 JSON 类型反序列化：
            #   Postgres(asyncpg) 会直接返回 dict
            #   SQLite 返回的是 JSON 字符串，不解析的话后面 payload.get() 会 AttributeError
            p = d.get("payload")
            if isinstance(p, (str, bytes)):
                try:
                    p = json.loads(p)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    p = {}
            d["payload"] = p or {}
            rows.append(d)
        await session.commit()
    return rows


async def _dispatch(row: dict) -> None:
    platform = row["platform"]
    adapter = registry.get(platform)
    payload = row["payload"] or {}

    if adapter is None:
        await _mark_failed(row, f"平台 {platform} 未启用")
        return

    # 限流：拿不到令牌就重排，绝不硬发
    if not await ratelimit.acquire(platform, adapter.rpm):
        log.info("outbox id=%s 被限流，稍后重试", row["id"])
        await _requeue(row, "rate_limited", delay=max(1.0, 60.0 / max(adapter.rpm, 1) * 2))
        return

    kind = payload.get("kind", "text")
    if kind == "text":
        result = await adapter.send_text(row["target_chat"], payload.get("text", ""), payload.get("meta"))
    elif kind == "file":
        result = await adapter.send_file(row["target_chat"], payload.get("path", ""), payload.get("meta"))
    elif kind == "status":
        result = await adapter.announce_status(row["target_chat"], payload.get("text", ""), payload.get("meta"))
    else:
        await _mark_failed(row, f"未知 payload.kind={kind}")
        return

    if result.ok:
        await _mark_sent(row, result.platform_msg_id)
    else:
        await _mark_failed(row, result.error or "unknown error")


async def _mark_sent(row: dict, platform_msg_id: str | None) -> None:
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE outbox SET status='sent', result_msg_id=:mid, last_error=NULL WHERE id=:id"),
            {"id": row["id"], "mid": platform_msg_id},
        )
        # 出站消息也落一份，形成完整会话流（收件箱里能看到"我回了什么"）
        payload = row["payload"] or {}
        if payload.get("kind") in ("text", "status", "file"):
            body = payload.get("text")
            attachments = []
            if payload.get("kind") == "file":
                path = payload.get("path") or ""
                # 只显示文件名：路径是服务器内部细节，不该出现在对话里
                name = os.path.basename(path) or "文件"
                # 去掉上传时加的 8 位随机前缀
                name = re.sub(r"^[0-9a-f]{8}_", "", name)
                body = f"[文件] {name}"
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                # 记一份 attachments：这样自己发出的文件在网页里也是一个可预览/下载的卡片，
                # 和对方发来的文件长得一样（原来只有一行 "[文件] xxx" 的纯文本）。
                attachments = [{"kind": "file", "name": name, "size": size, "path": path}]
            session.add(
                Message(
                    platform=row["platform"],
                    # 有平台真实消息 ID 就用它：拉历史时飞书返回的是 om_xxx，
                    # 如果这里存 outbox:N，去重键对不上，同一条消息会被回填成两条。
                    platform_msg_id=platform_msg_id or f"outbox:{row['id']}",
                    chat_id=row["target_chat"],
                    msg_type="file" if payload.get("kind") == "file" else "text",
                    text_body="" if attachments else (body or ""),
                    attachments=attachments or None,
                    direction="out",
                    status="sent",
                    raw={"outbox_id": row["id"]},
                )
            )
        await session.commit()
    log.info("outbox id=%s 发送成功 msg_id=%s", row["id"], platform_msg_id)


async def _requeue(row: dict, reason: str, delay: float) -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                "UPDATE outbox SET status='queued', next_retry_at=:nrt, last_error=:err WHERE id=:id"
            ),
            {
                "id": row["id"],
                "nrt": datetime.now(timezone.utc) + timedelta(seconds=delay),
                "err": reason,
            },
        )
        await session.commit()


async def _mark_failed(row: dict, error: str) -> None:
    attempts = int(row.get("attempts") or 0)
    if attempts >= settings.outbox_max_attempts:
        async with SessionLocal() as session:
            await session.execute(
                text("UPDATE outbox SET status='dead', last_error=:err WHERE id=:id"),
                {"id": row["id"], "err": (error or "")[:2000]},
            )
            await session.commit()
        log.error("outbox id=%s 超过最大重试次数，置为 dead: %s", row["id"], error)
        return

    # 指数退避 + 抖动：1s,2s,4s,8s...，抖动避免多 worker 同时重试打爆平台
    delay = min(2 ** attempts, 60) + random.uniform(0, 1)
    log.warning("outbox id=%s 第 %s 次失败(%ss 后重试): %s", row["id"], attempts, round(delay, 1), error)
    await _requeue(row, error, delay)


def payload_text(text: str, meta: dict | None = None) -> dict:
    return {"kind": "text", "text": text, "meta": meta or {}}


def payload_file(path: str, meta: dict | None = None) -> dict:
    return {"kind": "file", "path": path, "meta": meta or {}}


def payload_status(text: str, meta: dict | None = None) -> dict:
    return {"kind": "status", "text": text, "meta": meta or {}}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
