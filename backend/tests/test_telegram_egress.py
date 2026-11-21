"""Telegram 适配器的「出口」配置测试：不联网，只验配置有没有接进去。

背景：这台机器直连 api.telegram.org 时，TCP 三次握手能成、TLS 握手阶段被 RST
（curl 报 `schannel: failed to receive handshake` / `Recv failure: Connection was reset`）。
换 IP、换 SNI、上 IPv6 全都一样，是按 IP 拦的，本地绕不过去。

于是给适配器留两个出口：
  - TELEGRAM_PROXY     本地代理（http:// 或 socks5://）
  - TELEGRAM_API_BASE  自建反代（deploy/telegram-relay.js 部署到 Cloudflare Workers）
                      配套 TELEGRAM_RELAY_KEY，反代侧校验，避免变成开放代理

这几条测试守的就是「配置真的进了 httpx 客户端」——否则改了环境变量却毫无效果，
排查起来会以为是被墙，其实是没接上。
"""

from __future__ import annotations

from app.adapters.telegram import TelegramAdapter


def _adapter(**kw) -> TelegramAdapter:
    async def cb(_platform, _raw):  # pragma: no cover - 不会真的被调用
        return None

    return TelegramAdapter("123:TOKEN", cb, **kw)


def test_default_points_at_official_api():
    a = _adapter()
    assert a.base == "https://api.telegram.org/bot123:TOKEN"
    assert a.file_base == "https://api.telegram.org/file/bot123:TOKEN"


def test_api_base_override_strips_trailing_slash():
    a = _adapter(api_base="https://tg-relay.example.workers.dev/")
    assert a.base == "https://tg-relay.example.workers.dev/bot123:TOKEN"
    assert a.file_base == "https://tg-relay.example.workers.dev/file/bot123:TOKEN"


def test_relay_key_goes_into_default_headers():
    a = _adapter(api_base="https://tg-relay.example.workers.dev", relay_key="deadbeef")
    assert a._http.headers.get("x-relay-key") == "deadbeef"


def test_no_relay_key_means_no_extra_header():
    a = _adapter()
    assert "x-relay-key" not in a._http.headers


def test_proxy_is_mounted_into_client():
    a = _adapter(proxy="http://127.0.0.1:7890")
    # httpx 把代理挂成 mounts；有 mount 就说明代理真的生效了
    assert a._http._mounts, "代理没有注入到 httpx 客户端"
