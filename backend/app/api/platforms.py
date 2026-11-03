"""平台状态、自检、以及"界面上直接接入新平台"的 API。

初赛"平台 API 连通性测试截图"从 /platforms 和 /platforms/{p}/test 出；
"零重启接入新平台"从 /platforms/connect 出（这是答辩里最容易讲出差异化的点）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters import registry
from ..config import settings
from ..db import get_session
from ..schemas import ConnectIn, TestSendIn
from ..services import platforms as platform_svc
from ..services import ratelimit

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["platforms"])


@router.get("/platforms")
async def list_platforms():
    out = []
    for adapter in registry.all():
        bucket = await ratelimit.stats(adapter.platform)
        out.append(
            {
                "platform": adapter.platform,
                "name": adapter.display_name,
                "ready": adapter.ready,
                "last_error": adapter.last_error,
                "supports_file": adapter.supports_file,
                "rpm": adapter.rpm,
                "rate_limit_tokens": round(bucket.get("tokens", 0), 1) if bucket else None,
            }
        )
    return {
        "platforms": out,
        "configured": {
            "feishu": settings.feishu_ready,
            "dingtalk": settings.dingtalk_ready,
            "telegram": settings.telegram_ready,
            "email": settings.email_ready,
        },
        "llm": settings.llm_ready,
        "connectable": list(platform_svc.SUPPORTED),
    }


@router.get("/platforms/saved")
async def list_saved(session: AsyncSession = Depends(get_session)):
    """界面上保存过的平台凭证（不返回任何密钥明文）。"""
    return {"items": await platform_svc.list_saved(session)}


@router.post("/platforms/connect")
async def connect_platform(body: ConnectIn, session: AsyncSession = Depends(get_session)):
    """在网页上填凭证即可接入新平台，无需改 .env、无需重启。"""
    result = await platform_svc.connect(
        session,
        body.platform,
        app_id=body.app_id.strip(),
        secret=body.secret.strip(),
        label=(body.label or "").strip() or None,
        extra=body.extra or None,
    )
    if not result.get("ok"):
        # 连接失败也算正常业务结果（凭证错 / 平台不通），用 400 把原因原样带回去
        raise HTTPException(400, result.get("error") or "接入失败")
    return result


@router.delete("/platforms/{platform}/connect")
async def disconnect_platform(
    platform: str,
    purge: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """停用平台。

    默认软停用（保留加密凭证，之后可 /enable 重新拉起）；`?purge=true` 才是真删。
    """
    return await platform_svc.disconnect(session, platform, purge=purge)


@router.post("/platforms/{platform}/enable")
async def enable_platform(platform: str, session: AsyncSession = Depends(get_session)):
    """重新启用之前停用的平台（凭证还在，不用重新填）。"""
    result = await platform_svc.enable(session, platform)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "启用失败")
    return result


@router.post("/platforms/{platform}/test")
async def test_platform(platform: str, body: TestSendIn):
    """往指定会话发一条测试消息：可复现的连通性证据。"""
    adapter = registry.get(platform)
    if adapter is None:
        raise HTTPException(404, f"平台 {platform} 未启用（检查 .env 或界面接入状态）")
    if not adapter.ready:
        raise HTTPException(503, f"平台 {platform} 未就绪: {adapter.last_error}")
    result = await adapter.send_text(body.chat_id, body.text)
    return {
        "ok": result.ok,
        "platform": platform,
        "platform_msg_id": result.platform_msg_id,
        "error": result.error,
    }
