"""账号（用户身份）的生命周期：落库、取用、自动续期。

关键点：
  * access_token 只有 2 小时，refresh_token 30 天且**每次刷新都会换新**；
    刷新必须原子，否则并发刷新会让旧 refresh_token 失效、账号直接掉线。
  * token 永远加密落库，只有调用平台 API 的那一刻才解密到内存。
  * 过期不抛错，返回 None 让上层提示"需要重新授权"——演示现场最怕崩溃式失败。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import UserAccount
from .crypto import decrypt, encrypt

log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"

# 同一个账号的刷新串行化，避免两个协程同时拿旧 refresh_token 去换
_refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _lock(platform: str, user_id: str) -> asyncio.Lock:
    key = (platform, user_id)
    if key not in _refresh_locks:
        _refresh_locks[key] = asyncio.Lock()
    return _refresh_locks[key]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_account(
    session: AsyncSession,
    *,
    platform: str,
    user_id: str,
    user_name: str | None = None,
    avatar_url: str | None = None,
    tenant_key: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    expires_in: int | None = None,
    refresh_expires_in: int | None = None,
    scope: str | None = None,
) -> UserAccount:
    """新建或更新一个用户账号（同一 platform+user_id 视为同一人）。"""
    acc = (
        await session.execute(
            select(UserAccount).where(
                UserAccount.platform == platform, UserAccount.user_id == user_id
            )
        )
    ).scalar_one_or_none()

    now = _utcnow()
    if acc is None:
        acc = UserAccount(platform=platform, user_id=user_id)
        session.add(acc)

    if user_name:
        acc.user_name = user_name
    if avatar_url:
        acc.avatar_url = avatar_url
    if tenant_key:
        acc.tenant_key = tenant_key
    if access_token:
        acc.access_token_enc = encrypt(access_token)
    if refresh_token:
        acc.refresh_token_enc = encrypt(refresh_token)
    if expires_in:
        acc.token_expires_at = now + timedelta(seconds=expires_in)
    if refresh_expires_in:
        acc.refresh_expires_at = now + timedelta(seconds=refresh_expires_in)
    if scope:
        acc.scope = scope
    acc.status = "active"
    acc.last_error = None
    await session.commit()
    await session.refresh(acc)
    return acc


async def list_accounts(session: AsyncSession, platform: str | None = None) -> list[UserAccount]:
    stmt = select(UserAccount).order_by(UserAccount.id)
    if platform:
        stmt = stmt.where(UserAccount.platform == platform)
    return list((await session.execute(stmt)).scalars().all())


async def get_account(session: AsyncSession, account_id: int) -> UserAccount | None:
    return (
        await session.execute(select(UserAccount).where(UserAccount.id == account_id))
    ).scalar_one_or_none()


async def delete_account(session: AsyncSession, account_id: int) -> bool:
    acc = await get_account(session, account_id)
    if not acc:
        return False
    await session.delete(acc)
    await session.commit()
    return True


async def _refresh_feishu(session: AsyncSession, acc: UserAccount) -> str | None:
    """用 refresh_token 换新的 user_access_token（飞书 OIDC 接口）。"""
    rt = decrypt(acc.refresh_token_enc)
    if not rt:
        acc.status = "revoked"
        acc.last_error = "refresh_token 无法解密，需要重新授权"
        await session.commit()
        return None

    async with httpx.AsyncClient(timeout=15) as cli:
        # 刷新接口要的不是 app_id/secret，而是 app_access_token
        r = await cli.post(
            f"{FEISHU_BASE}/auth/v3/app_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        aat = (r.json() or {}).get("app_access_token")
        if not aat:
            acc.last_error = f"取 app_access_token 失败: {r.text[:200]}"
            await session.commit()
            return None

        r2 = await cli.post(
            f"{FEISHU_BASE}/authen/v1/oidc/refresh_access_token",
            headers={"Authorization": f"Bearer {aat}"},
            json={"grant_type": "refresh_token", "refresh_token": rt},
        )
        data = (r2.json() or {}).get("data") or {}
        new_at = data.get("access_token")
        if not new_at:
            acc.status = "revoked"
            acc.last_error = f"刷新失败: {r2.text[:300]}"
            await session.commit()
            log.warning("飞书 token 刷新失败 account=%s: %s", acc.id, r2.text[:200])
            return None

        now = _utcnow()
        acc.access_token_enc = encrypt(new_at)
        if data.get("refresh_token"):
            acc.refresh_token_enc = encrypt(data["refresh_token"])
        if data.get("expires_in"):
            acc.token_expires_at = now + timedelta(seconds=int(data["expires_in"]))
        if data.get("refresh_expires_in"):
            acc.refresh_expires_at = now + timedelta(seconds=int(data["refresh_expires_in"]))
        acc.status = "active"
        acc.last_error = None
        await session.commit()
        log.info("飞书 token 已刷新 account=%s 新过期时间=%s", acc.id, acc.token_expires_at)
        return new_at


async def get_valid_token(session: AsyncSession, acc: UserAccount) -> str | None:
    """拿一个此刻可用的 access_token：没过期直接用，快过期就先刷新。"""
    at = decrypt(acc.access_token_enc)
    now = _utcnow()
    # 提前 3 分钟刷新，避免"刚好在请求那一刻过期"
    if at and acc.token_expires_at and acc.token_expires_at - timedelta(minutes=3) > now:
        return at

    async with _lock(acc.platform, acc.user_id):
        await session.refresh(acc)   # 等锁期间可能已被别的协程刷新过
        at = decrypt(acc.access_token_enc)
        if at and acc.token_expires_at and acc.token_expires_at - timedelta(minutes=3) > now:
            return at
        if acc.platform == "feishu":
            return await _refresh_feishu(session, acc)
    return at


async def refresh_all_expiring(session: AsyncSession, within_minutes: int = 30) -> int:
    """后台巡检：把快过期的账号统一续上。返回刷新成功数量。"""
    deadline = _utcnow() + timedelta(minutes=within_minutes)
    rows = (
        await session.execute(
            select(UserAccount).where(
                UserAccount.status == "active",
                UserAccount.refresh_token_enc.is_not(None),
            )
        )
    ).scalars().all()
    done = 0
    for acc in rows:
        if acc.token_expires_at and acc.token_expires_at > deadline:
            continue
        if acc.refresh_expires_at and acc.refresh_expires_at < _utcnow():
            acc.status = "expired"
            acc.last_error = "refresh_token 已过期，需要重新授权"
            await session.commit()
            continue
        token = await get_valid_token(session, acc)
        if token:
            done += 1
    return done
