"""运行时可插拔的平台接入。

设计目标：**在网页上填凭证就能接一个新平台，不需要改 .env、不需要重启进程。**

三个阶段：
  1. 启动时：build_registry() 先用 .env 里的凭证装配一遍（兜底，保证老部署行为不变）；
  2. 启动时：load_saved() 把数据库里"界面上保存过"的平台凭证读出来，覆盖 / 追加到注册表；
  3. 运行时：connect() 加密存库 + 现场实例化适配器 + 热启动；disconnect() 停掉并删除。

凭证安全：密钥走 services/crypto.py 加密后落库（platform_account.app_secret_enc），
明文只在请求处理的那几秒里存在于内存。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters import AdapterRegistry, registry
from ..adapters.base import InboundCallback
from ..adapters.mail import MailAdapter
from ..config import settings
from ..models import PlatformAccount
from . import crypto

log = logging.getLogger(__name__)

# 目前支持在界面上填写凭证的平台（白名单，防止任意 platform 字符串乱入）
SUPPORTED = ("email", "telegram", "feishu", "dingtalk")

_on_inbound: InboundCallback | None = None


def set_inbound(cb: InboundCallback) -> None:
    global _on_inbound
    _on_inbound = cb


def _guess_mail_hosts(address: str) -> tuple[str, str]:
    from ..adapters import MAIL_HOSTS

    domain = (address.split("@")[-1] or "").lower()
    if domain in MAIL_HOSTS:
        return MAIL_HOSTS[domain]
    return f"imap.{domain}", f"smtp.{domain}"


def build_adapter(platform: str, app_id: str, secret: str, extra: dict | None, cb: InboundCallback):
    """按平台构造适配器实例；不支持或参数不足时返回 None。"""
    extra = extra or {}
    if platform == "email":
        guess_imap, guess_smtp = _guess_mail_hosts(app_id)
        smtp_port = int(extra.get("smtp_port") or 465)
        return MailAdapter(
            app_id,
            secret,
            extra.get("imap_host") or guess_imap,
            extra.get("smtp_host") or guess_smtp,
            cb,
            imap_port=int(extra.get("imap_port") or 993),
            smtp_port=smtp_port,
            poll_seconds=int(extra.get("poll_seconds") or 30),
            mark_seen=bool(extra.get("mark_seen", True)),
            use_ssl_smtp=smtp_port != 587,
        )
    if platform == "telegram":
        from ..adapters.telegram import TelegramAdapter

        return TelegramAdapter(secret, cb)
    if platform == "feishu":
        from ..adapters.feishu import FeishuAdapter

        return FeishuAdapter(app_id, secret, cb)
    if platform == "dingtalk":
        from ..adapters.dingtalk import DingTalkAdapter

        return DingTalkAdapter(app_id, secret, extra.get("robot_code") or "", cb)
    return None


async def connect(
    session: AsyncSession,
    platform: str,
    *,
    app_id: str,
    secret: str,
    label: str | None = None,
    extra: dict | None = None,
) -> dict:
    """保存凭证并立刻启动适配器（热插拔，不用重启）。"""
    if platform not in SUPPORTED:
        return {"ok": False, "error": f"不支持的平台：{platform}"}
    if not app_id or not secret:
        return {"ok": False, "error": "账号和密钥都必须填写"}
    if _on_inbound is None:
        return {"ok": False, "error": "服务尚未就绪，请稍后重试"}

    try:
        adapter = build_adapter(platform, app_id, secret, extra, _on_inbound)
    except Exception as exc:
        return {"ok": False, "error": f"构造适配器失败：{type(exc).__name__}: {exc}"}
    if adapter is None:
        return {"ok": False, "error": f"平台 {platform} 暂不支持界面接入"}

    # 1) 落库（覆盖同平台同账号的旧记录）
    existing = (
        await session.execute(
            select(PlatformAccount).where(
                PlatformAccount.platform == platform, PlatformAccount.app_id == app_id
            )
        )
    ).scalar_one_or_none()
    enc = crypto.encrypt(secret)
    if existing:
        existing.app_secret_enc = enc
        existing.label = label or existing.label or app_id
        existing.extra = extra or existing.extra
        existing.status = "active"
    else:
        session.add(
            PlatformAccount(
                platform=platform,
                app_id=app_id,
                app_secret_enc=enc,
                label=label or app_id,
                extra=extra or {},
                status="active",
            )
        )
    await session.commit()

    # 2) 停掉同平台的旧适配器（同一平台同时只保留一个实例）
    old = registry.get(platform)
    if old is not None:
        try:
            await old.stop()
        except Exception:
            log.warning("[%s] 旧适配器停止失败", platform)

    # 3) 注册并启动
    registry.register(adapter)
    await adapter.start()

    # 4) 起不来的话把错误落在库上，界面能看到原因（也避免下次启动白白重连）
    if not adapter.ready:
        try:
            row = (
                await session.execute(
                    select(PlatformAccount).where(
                        PlatformAccount.platform == platform,
                        PlatformAccount.app_id == app_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.status = "error"
                row.last_error = (adapter.last_error or "启动失败")[:500]
                await session.commit()
        except Exception:
            log.warning("[%s] 记录接入错误失败", platform)

    log.info("[%s] 界面接入完成 ready=%s err=%s", platform, adapter.ready, adapter.last_error)
    return {
        "ok": adapter.ready,
        "platform": platform,
        "label": label or app_id,
        "ready": adapter.ready,
        "error": adapter.last_error,
    }


async def disconnect(session: AsyncSession, platform: str, *, purge: bool = False) -> dict:
    """停用某个平台。

    默认是**软停用**：只把 status 标成 disabled，凭证密文留在库里。
    原因是之前这里是硬删除 —— 用户在界面上点一下「断开」，加密后的密钥就
    跟着那一行一起没了，只能重新去平台后台拿一遍授权码，体验很差。
    真要清干净，传 purge=True。
    """
    adapter = registry.get(platform)
    if adapter is not None:
        try:
            await adapter.stop()
        except Exception:
            log.warning("[%s] 停止适配器失败", platform)
        registry._items.pop(platform, None)

    row = (
        await session.execute(select(PlatformAccount).where(PlatformAccount.platform == platform))
    ).scalars().first()

    if purge:
        await session.execute(delete(PlatformAccount).where(PlatformAccount.platform == platform))
        await session.commit()
        return {"ok": True, "platform": platform, "purged": True, "found": row is not None}

    if row is None:
        await session.commit()
        return {"ok": True, "platform": platform, "purged": False, "found": False}

    row.status = "disabled"
    row.last_error = None
    await session.commit()
    return {"ok": True, "platform": platform, "purged": False, "found": True}


async def enable(session: AsyncSession, platform: str) -> dict:
    """把之前停用的平台重新拉起来（凭证还在，不用重新填）。"""
    if _on_inbound is None:
        return {"ok": False, "platform": platform, "error": "服务未就绪"}
    row = (
        await session.execute(select(PlatformAccount).where(PlatformAccount.platform == platform))
    ).scalars().first()
    if row is None:
        return {"ok": False, "platform": platform, "error": "没有这个平台的接入记录，请重新填写凭证"}
    secret = crypto.decrypt(row.app_secret_enc or "")
    if not secret:
        return {"ok": False, "platform": platform, "error": "库里的凭证解不开（SECRET_KEY 变过？），请重新填写"}

    adapter = build_adapter(row.platform, row.app_id or "", secret, row.extra or {}, _on_inbound)
    if adapter is None:
        return {"ok": False, "platform": platform, "error": "不支持的平台"}

    old = registry.get(row.platform)
    if old is not None:
        try:
            await old.stop()
        except Exception:
            pass
        registry._items.pop(row.platform, None)

    registry.register(adapter)
    try:
        await adapter.start()
    except Exception as exc:
        log.exception("[%s] 重新启用失败", platform)
        row.status = "error"
        row.last_error = str(exc)[:500]
        await session.commit()
        return {"ok": False, "platform": platform, "error": str(exc)}

    if adapter.ready:
        row.status = "active"
        row.last_error = None
        await session.commit()
        return {"ok": True, "platform": platform, "ready": True, "error": None}

    row.status = "error"
    row.last_error = (adapter.last_error or "启动失败")[:500]
    await session.commit()
    return {"ok": False, "platform": platform, "error": adapter.last_error or "启动失败"}


async def list_saved(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(select(PlatformAccount).order_by(PlatformAccount.id))).scalars().all()
    return [
        {
            "id": r.id,
            "platform": r.platform,
            "label": r.label or r.app_id,
            "app_id": r.app_id,
            "status": r.status,
            "extra": r.extra or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "live": (registry.get(r.platform) is not None and registry.get(r.platform).ready),
        }
        for r in rows
    ]


async def load_saved(session: AsyncSession) -> int:
    """启动时把库里保存过的平台全部拉起（DB 优先，覆盖 .env 的同平台实例）。"""
    if _on_inbound is None:
        log.warning("load_saved 跳过：on_inbound 未设置")
        return 0
    rows = (
        await session.execute(select(PlatformAccount).where(PlatformAccount.status == "active"))
    ).scalars().all()
    started = 0
    for row in rows:
        secret = crypto.decrypt(row.app_secret_enc or "")
        if not secret:
            log.warning("[%s] 库里凭证无法解密，跳过（可能需要重新填写）", row.platform)
            continue
        try:
            adapter = build_adapter(row.platform, row.app_id or "", secret, row.extra or {}, _on_inbound)
            if adapter is None:
                continue
            old = registry.get(row.platform)
            if old is not None:
                await old.stop()
            registry.register(adapter)
            await adapter.start()
            if adapter.ready:
                started += 1
            else:
                # 启动失败时把原因写回库，界面能直接看到该重新填凭证
                row.status = "error"
                row.last_error = (adapter.last_error or "启动失败")[:500]
                await session.commit()
        except Exception as exc:
            log.exception("[%s] 恢复保存的平台失败", row.platform)
            row.status = "error"
            row.last_error = str(exc)[:500]
            await session.commit()
    if started:
        log.info("已从数据库恢复 %d 个平台接入", started)
    return started


def registry_view() -> AdapterRegistry:
    return registry


def __getattr__(name: str) -> Any:  # pragma: no cover - 便于测试打桩
    raise AttributeError(name)
