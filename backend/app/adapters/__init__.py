"""适配器注册表：按环境变量决定启用哪些平台。

业务代码只通过 registry.get(platform) 取适配器，
新增平台 = 加一个适配器文件 + 在这里注册一行，其他代码零改动。
"""
from __future__ import annotations

import logging

from ..config import settings
from .base import BaseAdapter, InboundCallback, SendResult, UnifiedMessage
from .dingtalk import DingTalkAdapter
from .feishu import FeishuAdapter
from .mail import MailAdapter
from .telegram import TelegramAdapter

log = logging.getLogger(__name__)

# 常见邮箱服务商的 IMAP/SMTP 主机名，用户只填邮箱地址即可自动匹配
MAIL_HOSTS: dict[str, tuple[str, str]] = {
    "qq.com": ("imap.qq.com", "smtp.qq.com"),
    "foxmail.com": ("imap.qq.com", "smtp.qq.com"),
    "vip.qq.com": ("imap.qq.com", "smtp.qq.com"),
    "163.com": ("imap.163.com", "smtp.163.com"),
    "126.com": ("imap.126.com", "smtp.126.com"),
    "yeah.net": ("imap.yeah.net", "smtp.yeah.net"),
    "sina.com": ("imap.sina.com", "smtp.sina.com"),
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com"),
    "live.com": ("outlook.office365.com", "smtp.office365.com"),
}


def _guess_mail_hosts(address: str) -> tuple[str, str]:
    domain = (address.split("@")[-1] or "").lower()
    if domain in MAIL_HOSTS:
        return MAIL_HOSTS[domain]
    # 企业邮箱常见规律：mail.<domain> 或 imap.<domain>
    return f"imap.{domain}", f"smtp.{domain}"

__all__ = [
    "BaseAdapter",
    "InboundCallback",
    "SendResult",
    "UnifiedMessage",
    "registry",
]


class AdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> None:
        self._items[adapter.platform] = adapter

    def get(self, platform: str) -> BaseAdapter | None:
        return self._items.get(platform)

    def all(self) -> list[BaseAdapter]:
        return list(self._items.values())

    def platforms(self) -> list[str]:
        return list(self._items.keys())

    async def start_all(self, on_inbound: InboundCallback) -> None:
        # 每个平台独立启动，单点失败不影响其他平台（这本身就是"高可用"的体现）
        for adapter in self._items.values():
            await adapter.start()

    async def stop_all(self) -> None:
        for adapter in self._items.values():
            await adapter.stop()


registry = AdapterRegistry()


def build_registry(on_inbound: InboundCallback) -> AdapterRegistry:
    """根据配置装配适配器。没配置凭证的平台直接不注册，前端也就不会显示空壳。"""
    registry._items.clear()

    if settings.feishu_ready:
        registry.register(FeishuAdapter(settings.feishu_app_id, settings.feishu_app_secret, on_inbound))
    else:
        log.warning("飞书未配置（FEISHU_APP_ID / FEISHU_APP_SECRET 为空），跳过")

    if settings.dingtalk_ready:
        registry.register(
            DingTalkAdapter(
                settings.dingtalk_client_id,
                settings.dingtalk_client_secret,
                settings.dingtalk_robot_code,
                on_inbound,
            )
        )
    else:
        log.warning("钉钉未配置（DINGTALK_CLIENT_ID / SECRET 为空），跳过")

    if settings.telegram_ready:
        registry.register(TelegramAdapter(settings.telegram_bot_token, on_inbound))
    else:
        log.warning("Telegram 未配置（TELEGRAM_BOT_TOKEN 为空），跳过")

    if settings.email_ready:
        guess_imap, guess_smtp = _guess_mail_hosts(settings.email_address)
        imap_host = settings.email_imap_host or guess_imap
        smtp_host = settings.email_smtp_host or guess_smtp
        log.info("邮箱接入：%s via %s / %s", settings.email_address, imap_host, smtp_host)
        registry.register(
            MailAdapter(
                settings.email_address,
                settings.email_password,
                imap_host,
                smtp_host,
                on_inbound,
                imap_port=settings.email_imap_port,
                smtp_port=settings.email_smtp_port,
                poll_seconds=settings.email_poll_seconds,
                mark_seen=settings.email_mark_seen,
                use_ssl_smtp=settings.email_smtp_port != 587,
            )
        )
    else:
        log.warning("邮箱未配置（EMAIL_ADDRESS / EMAIL_PASSWORD 为空），跳过")

    return registry
