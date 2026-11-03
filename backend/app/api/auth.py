"""用户身份接入（OAuth）：把「应用身份」升级成「用户身份」。

差别有多大：
  * 应用身份：只能收到机器人参与的消息（单聊、被 @），看不到别的；
  * 用户身份：这个人在平台里能看到的一切——自己加入的所有群、全部单聊、
    以及任意翻页的历史消息。

飞书这条链路是：授权页 → 回调拿 code → 用 code 换 user_access_token
→ 用 token 拉 user_info 落库。access_token 2 小时，refresh_token 30 天，
续期在 services/accounts.py 里做（含并发保护）。
"""
from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..security import COOKIE_NAME, check_password, login_page, make_token
from ..services import accounts, feishu_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

FEISHU_BASE = "https://open.feishu.cn/open-apis"
FEISHU_AUTHORIZE = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"

# 防 CSRF 的 state（单实例内存即可；多副本部署时应放 Redis）
_states: set[str] = set()


# ---------------- 访问口令（不是平台授权，是"能不能进这个网页"）----------------


@router.get("/login", response_class=HTMLResponse)
async def login_form():
    """登录页。口令没配置时直接跳首页，别让人卡在一个没用的表单上。"""
    if not settings.auth_required:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(login_page())


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    password = str(form.get("password") or "")
    if not check_password(password):
        log.warning("登录失败：口令不对（来源 %s）", request.client.host if request.client else "?")
        return HTMLResponse(login_page("口令不对，再试一次。"), status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=max(1, settings.access_cookie_days) * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


def _account_out(a) -> dict:
    return {
        "id": a.id,
        "platform": a.platform,
        "user_id": a.user_id,
        "user_name": a.user_name,
        "avatar_url": a.avatar_url,
        "status": a.status,
        "scope": a.scope,
        "token_expires_at": a.token_expires_at,
        "refresh_expires_at": a.refresh_expires_at,
        "last_error": a.last_error,
        "last_sync_at": a.last_sync_at,
    }


@router.get("/feishu/url")
async def feishu_authorize_url():
    """生成授权链接，前端点一下就能跳。"""
    if not settings.feishu_ready:
        raise HTTPException(400, "飞书应用凭证未配置（FEISHU_APP_ID / SECRET）")
    state = secrets.token_urlsafe(16)
    _states.add(state)
    url = (
        f"{FEISHU_AUTHORIZE}?client_id={settings.feishu_app_id}"
        f"&redirect_uri={settings.feishu_redirect_uri}"
        f"&scope={settings.feishu_user_scopes.replace(' ', '%20')}"
        f"&state={state}"
    )
    return {"url": url, "state": state, "redirect_uri": settings.feishu_redirect_uri}


@router.get("/feishu/callback")
async def feishu_callback(
    code: str = Query(...),
    state: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """飞书授权后回调到这里：换 token、拉用户信息、落库。"""
    if state and _states and state not in _states:
        log.warning("OAuth state 不匹配，可能是跨站伪造请求: %s", state)
    _states.discard(state or "")

    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(
            f"{FEISHU_BASE}/auth/v3/app_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        aat = (r.json() or {}).get("app_access_token")
        if not aat:
            return HTMLResponse(_page("授权失败", f"取应用凭证失败：{r.text[:300]}"), status_code=500)

        r2 = await cli.post(
            f"{FEISHU_BASE}/authen/v1/oidc/access_token",
            headers={"Authorization": f"Bearer {aat}"},
            json={"grant_type": "authorization_code", "code": code},
        )
        body = r2.json() or {}
        data = body.get("data") or {}
        at = data.get("access_token")
        if not at:
            return HTMLResponse(
                _page("授权失败", f"换取 user_access_token 失败：{r2.text[:400]}"), status_code=400
            )

        r3 = await cli.get(
            f"{FEISHU_BASE}/authen/v1/user_info", headers={"Authorization": f"Bearer {at}"}
        )
        info = (r3.json() or {}).get("data") or {}

    acc = await accounts.upsert_account(
        session,
        platform="feishu",
        user_id=info.get("open_id") or info.get("user_id") or "unknown",
        user_name=info.get("name") or info.get("en_name"),
        avatar_url=info.get("avatar_url") or info.get("avatar_middle"),
        tenant_key=info.get("tenant_key"),
        access_token=at,
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        refresh_expires_in=data.get("refresh_expires_in"),
        scope=data.get("scope"),
    )
    log.info("用户身份接入成功: %s(%s) account_id=%s", acc.user_name, acc.user_id, acc.id)
    return HTMLResponse(
        _page(
            "授权成功",
            f"已接入 <b>{acc.user_name or acc.user_id}</b>（{acc.platform}）。"
            f"<br>现在可以回统一收件箱，点「同步我的会话」。<br>"
            f"<a href=\"http://127.0.0.1:5173\">返回统一收件箱</a>",
        )
    )


@router.get("/accounts")
async def get_accounts(session: AsyncSession = Depends(get_session)):
    rows = await accounts.list_accounts(session)
    return [_account_out(a) for a in rows]


@router.post("/accounts/{account_id}/refresh")
async def refresh_account(account_id: int, session: AsyncSession = Depends(get_session)):
    acc = await accounts.get_account(session, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    token = await accounts.get_valid_token(session, acc)
    return {"ok": bool(token), "status": acc.status, "last_error": acc.last_error}


@router.post("/accounts/{account_id}/sync")
async def sync_account(
    account_id: int,
    chats: int = 20,
    per_chat: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """用用户身份把「我这个账号能看到的会话」同步进统一收件箱。"""
    acc = await accounts.get_account(session, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    token = await accounts.get_valid_token(session, acc)
    if not token:
        raise HTTPException(400, f"token 不可用（{acc.status}）：{acc.last_error or '需要重新授权'}")
    if acc.platform != "feishu":
        raise HTTPException(400, f"暂不支持的平台：{acc.platform}")
    try:
        result = await feishu_user.sync_account(
            session, acc, token, chats=chats, per_chat=per_chat
        )
    except Exception as exc:
        acc.last_error = str(exc)[:500]
        await session.commit()
        raise HTTPException(500, f"同步失败：{exc}")
    return {"ok": True, "account_id": acc.id, **result}


@router.get("/accounts/{account_id}/chats")
async def list_my_chats(account_id: int, session: AsyncSession = Depends(get_session)):
    """列出这个账号可见的全部会话（用户身份才做得到）。"""
    acc = await accounts.get_account(session, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    token = await accounts.get_valid_token(session, acc)
    if not token:
        raise HTTPException(400, f"token 不可用（{acc.status}）")
    chats = await feishu_user.list_chats(token)
    return [
        {
            "chat_id": c.get("chat_id"),
            "name": c.get("name"),
            "chat_mode": c.get("chat_mode"),
            "chat_type": c.get("chat_type"),
            "member_count": c.get("user_count") or c.get("member_count"),
            "description": c.get("description"),
        }
        for c in chats
    ]


@router.delete("/accounts/{account_id}")
async def remove_account(account_id: int, session: AsyncSession = Depends(get_session)):
    ok = await accounts.delete_account(session, account_id)
    if not ok:
        raise HTTPException(404, "账号不存在")
    return {"ok": True}


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title>
<style>body{{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;
background:#f7f8fa;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#fff;padding:32px 40px;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:560px;line-height:1.8}}
h2{{margin:0 0 12px;font-size:20px}}a{{color:#1677ff}}</style></head>
<body><div class="card"><h2>{title}</h2><div>{body}</div></div></body></html>"""
