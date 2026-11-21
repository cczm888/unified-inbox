"""Telegram 适配器的协议级测试：不需要网络，也不需要真 token。

为什么这么测：这台机器所在的网络直连 api.telegram.org 是被拦掉的
（IPv4 / IPv6 / 直连 IP 三种方式都拿不到响应），既没有真 token 也验不了真链路。
所以用一个假 HTTP 客户端顶掉传输层，把 Bot API 的四条关键路径
（getMe / getUpdates / sendMessage / sendDocument）走一遍**真实代码路径**：

  - getMe 成功 → 启动长轮询；token 无效 → 立刻抛错，而不是安静转圈
  - getUpdates 返回一条私聊文本 → 归一化成 UnifiedMessage，字段齐全
  - 入库抛错 → offset 不前移，这条 update 下次重推（靠唯一约束去重）
  - sendMessage 成功取 message_id；失败把 description 带回 error

它证明的是「协议实现与错误处理是对的」，不能替代真机联调。
真机只需要填 TELEGRAM_BOT_TOKEN，再跑 python scripts/check-platform.py。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.adapters.telegram import TelegramAdapter

TOKEN = "123456:TEST-TOKEN"

PRIVATE_UPDATE = {
    "update_id": 77,
    "message": {
        "message_id": 501,
        "date": 1762000000,
        "text": "你好，收件箱",
        "chat": {"id": 9001, "type": "private", "username": "tester"},
        "from": {"id": 9001, "first_name": "Test", "last_name": "User", "username": "tester"},
    },
}


class FakeClient:
    """假 HTTP 客户端：每次请求都 await 一次，保证事件循环能被其它协程切走。

    直接用 httpx.MockTransport 时请求会在当前协程里同步完成、不产生让出点，
    轮询循环会把事件循环吃满（测试里表现为卡死）。
    """

    def __init__(self, routes: dict[str, dict]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    async def _reply(self, method: str, payload: dict) -> httpx.Response:
        await asyncio.sleep(0)
        self.calls.append((method, payload))
        if method in self.routes:
            return httpx.Response(200, json=self.routes[method])
        return httpx.Response(404, json={"ok": False, "description": f"no route {method}"})

    async def get(self, url: str, params: dict | None = None, **_kw):
        return await self._reply(url.rsplit("/", 1)[-1], dict(params or {}))

    async def post(self, url: str, json: dict | None = None, data: dict | None = None,
                   files: dict | None = None, **_kw):  # noqa: A002
        return await self._reply(url.rsplit("/", 1)[-1], dict(json or data or {}))

    async def aclose(self) -> None:
        return None


def make_adapter(routes: dict[str, dict], inbound) -> tuple[TelegramAdapter, FakeClient]:
    adapter = TelegramAdapter(TOKEN, inbound)
    fake = FakeClient(routes)
    adapter._http = fake
    return adapter, fake


async def _noop(*_args, **_kwargs):
    return None


def test_start_and_poll_yields_unified_message():
    async def scenario():
        got: list = []

        async def inbound(platform, raw):
            got.append((platform, raw))

        adapter, fake = make_adapter(
            {
                "getMe": {"ok": True, "result": {"id": 1, "username": "inbox_bot"}},
                "getUpdates": {"ok": True, "result": [PRIVATE_UPDATE]},
            },
            inbound,
        )

        await adapter._start()
        assert adapter.username == "inbox_bot"

        for _ in range(60):  # 最多等 0.6 秒
            if got:
                break
            await asyncio.sleep(0.01)
        await adapter._stop()

        assert got, "长轮询没有把消息交给 on_inbound"
        platform, raw = got[0]
        assert platform == "telegram"
        assert raw["message_id"] == 501

        msg = adapter.to_unified(raw)
        assert msg is not None
        assert msg.platform == "telegram"
        assert msg.platform_msg_id == "9001:501"
        assert msg.chat_id == "9001"
        assert msg.chat_type == "p2p"
        assert msg.sender_name == "Test User"
        assert msg.text == "你好，收件箱"
        assert msg.msg_type == "text"

        assert [c[0] for c in fake.calls][:2] == ["getMe", "getUpdates"]

    asyncio.run(scenario())


def test_offset_does_not_advance_when_inbound_fails():
    """入库抛错时 offset 必须停住：这条 update 下次会重推，不能吞掉。"""

    async def scenario():
        attempts = {"n": 0}

        async def inbound(platform, raw):
            attempts["n"] += 1
            raise RuntimeError("db write failed")

        adapter, _ = make_adapter(
            {
                "getMe": {"ok": True, "result": {"id": 1, "username": "b"}},
                "getUpdates": {"ok": True, "result": [PRIVATE_UPDATE]},
            },
            inbound,
        )
        await adapter._start()
        # 首次失败后适配器按 1 秒退避重试，这里最多等 3 秒
        for _ in range(300):
            if attempts["n"] >= 2:
                break
            await asyncio.sleep(0.01)
        await adapter._stop()

        assert attempts["n"] >= 2, "入库失败后没有重试"
        assert adapter._offset is None, "offset 被前移了，这条消息会被永久跳过"

    asyncio.run(scenario())


def test_start_rejects_bad_token():
    async def scenario():
        adapter, _ = make_adapter(
            {"getMe": {"ok": False, "error_code": 401, "description": "Unauthorized"}},
            _noop,
        )
        with pytest.raises(RuntimeError) as ei:
            await adapter._start()
        assert "getMe" in str(ei.value)
        await adapter._stop()

    asyncio.run(scenario())


def test_send_message_success_and_failure():
    async def scenario():
        adapter, fake = make_adapter(
            {"sendMessage": {"ok": True, "result": {"message_id": 4242}}}, _noop
        )
        result = await adapter.send_text("9001", "回复内容")
        assert result.ok is True
        assert result.platform_msg_id == "4242"
        method, payload = fake.calls[-1]
        assert method == "sendMessage"
        assert payload["chat_id"] == "9001"
        assert payload["text"] == "回复内容"
        await adapter._stop()

        adapter2, _ = make_adapter(
            {"sendMessage": {"ok": False, "description": "chat not found"}}, _noop
        )
        bad = await adapter2.send_text("9001", "x")
        assert bad.ok is False
        assert "chat not found" in (bad.error or "")
        await adapter2._stop()

    asyncio.run(scenario())


def test_send_document_returns_message_id(tmp_path):
    async def scenario():
        f = tmp_path / "report.txt"
        f.write_text("hello", encoding="utf-8")
        adapter, fake = make_adapter(
            {"sendDocument": {"ok": True, "result": {"message_id": 7}}}, _noop
        )
        result = await adapter.send_file("9001", str(f))
        assert result.ok is True
        assert result.platform_msg_id == "7"
        method, payload = fake.calls[-1]
        assert method == "sendDocument"
        assert payload["chat_id"] == "9001"  # multipart 里的表单字段
        await adapter._stop()

    asyncio.run(scenario())
