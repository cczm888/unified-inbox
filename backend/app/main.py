"""应用入口：装配适配器、启动 outbox worker、暴露 REST + SSE。

启动顺序（每一环失败都不拖垮全局）：
  1. 建表
  2. 按凭证装配适配器
  3. 并行启动各平台入站（长连接/长轮询）
  4. 起 outbox 投递 worker
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .adapters import build_registry, registry
from .api import auth, inbox, platforms, stats, todos
from .config import settings
from .db import SessionLocal, close_all, init_db
from .security import COOKIE_NAME, is_authenticated, login_page
from .services import accounts, inbound, outbox
from .services import platforms as platform_svc

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=== %s 启动 (%s) ===", settings.app_name, settings.env)

    await init_db()
    await outbox.recover_stale()
    # 1) .env 里的凭证先兜底装配；2) 剔除界面上被停用过的；3) 再热启动界面上接入的（DB 优先）
    build_registry(inbound.handle_inbound)
    platform_svc.set_inbound(inbound.handle_inbound)
    try:
        async with SessionLocal() as session:
            await platform_svc.apply_disabled(session)
    except Exception:
        log.exception("读取停用列表失败（.env 里的平台会照常启动）")
    await registry.start_all(inbound.handle_inbound)
    try:
        async with SessionLocal() as session:
            await platform_svc.load_saved(session)
    except Exception:
        log.exception("从数据库恢复平台接入失败（不影响 .env 里的平台）")

    stop_event = asyncio.Event()
    workers = [
        asyncio.create_task(outbox.worker_loop(stop_event, i), name=f"outbox-{i}")
        for i in range(max(1, settings.outbox_workers))
    ]
    # 用户身份账号的 token 巡检：access_token 只有 2 小时，掉了整条链路就静默失效
    refresher = asyncio.create_task(_token_watch(stop_event), name="token-watch")
    app.state.stop_event = stop_event

    log.info("已启用平台: %s", ", ".join(registry.platforms()) or "无（请检查 .env）")
    try:
        yield
    finally:
        log.info("=== 正在关闭 ===")
        stop_event.set()
        refresher.cancel()
        for w in workers:
            w.cancel()
        await asyncio.gather(refresher, *workers, return_exceptions=True)
        await registry.stop_all()
        await close_all()


async def _token_watch(stop_event: asyncio.Event) -> None:
    """每 10 分钟检查一次快过期的账号 token，提前续期。"""
    while not stop_event.is_set():
        try:
            async with SessionLocal() as session:
                n = await accounts.refresh_all_expiring(session, within_minutes=30)
                if n:
                    log.info("token 巡检：已续期 %d 个账号", n)
        except Exception as exc:
            log.warning("token 巡检异常: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=600)
        except asyncio.TimeoutError:
            continue


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

# 免登录路径：健康检查（不含敏感数据）+ 登录页本身
_PUBLIC_PATHS = {"/healthz", "/api/v1/auth/login", "/favicon.ico"}


@app.middleware("http")
async def access_guard(request: Request, call_next):
    """访问口令校验。

    系统里是一个人所有平台的原始消息，监听 0.0.0.0 或开隧道后就是公开的。
    只要 ACCESS_PASSWORD 非空，除登录页与健康检查外一律要求带 cookie。
    """
    if not settings.auth_required:
        return await call_next(request)

    path = request.url.path
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    if is_authenticated(request.cookies.get(COOKIE_NAME)):
        return await call_next(request)

    # 浏览器直接敲地址 → 给登录页；前端 fetch → 给 401 让 SPA 跳去登录
    if "text/html" in request.headers.get("accept", "") and not path.startswith("/api/"):
        return HTMLResponse(login_page(), status_code=401)
    return JSONResponse(
        {"detail": "未登录：请访问 /api/v1/auth/login 输入访问口令", "need_login": True},
        status_code=401,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 比赛演示环境放开；生产按域名收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inbox.router)
app.include_router(todos.router)
app.include_router(platforms.router)
app.include_router(stats.router)
app.include_router(auth.router)


@app.get("/healthz", tags=["infra"])
async def healthz():
    return {
        "ok": True,
        "platforms": [
            {"platform": a.platform, "ready": a.ready, "error": a.last_error} for a in registry.all()
        ],
        "outbox_workers": settings.outbox_workers,
    }
