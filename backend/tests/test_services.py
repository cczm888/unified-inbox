"""服务层单测：降级路径 + 出站 payload 结构。

这几条是评审最容易质疑的地方（"没有 API key / Redis 挂了怎么办"），
用测试把它们变成可复现的证据：pytest -q
"""
from __future__ import annotations

import asyncio

import pytest

from app.adapters.base import dumps, ms_to_dt
from app.config import settings
from app.services import llm, outbox, ratelimit


# ---------------- 出站 payload ----------------

def test_payload_text_carries_meta():
    payload = outbox.payload_text("你好", {"session_webhook": "https://x"})
    assert payload["kind"] == "text"
    assert payload["text"] == "你好"
    assert payload["meta"]["session_webhook"] == "https://x"


def test_payload_status_kind_differs_from_text():
    assert outbox.payload_status("已处理")["kind"] == "status"
    assert outbox.payload_file("/tmp/a.pdf")["kind"] == "file"


def test_dumps_keeps_chinese_readable():
    assert "你好" in dumps({"t": "你好"})


def test_ms_to_dt_handles_second_and_millisecond():
    seconds = ms_to_dt(1762000000)        # 10 位
    millis = ms_to_dt(1762000000000)      # 13 位
    assert seconds is not None and millis is not None
    assert abs((seconds - millis).total_seconds()) < 2


# ---------------- AI 降级 ----------------

def test_rule_priority_marks_urgent_words():
    assert llm._rule_priority("这个今天必须给我") == "urgent"
    assert llm._rule_priority("线上支付故障，尽快处理") == "urgent"
    assert llm._rule_priority("周末一起吃饭吗") == "normal"


@pytest.mark.asyncio
async def test_summarize_falls_back_to_rule_without_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    result = await llm.summarize("报价单帮我看下，今天要，截止明天上午")
    assert result["source"] == "rule"
    assert result["priority"] == "urgent"
    assert result["summary"]


@pytest.mark.asyncio
async def test_summarize_falls_back_when_llm_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-fake")

    async def boom(_prompt: str) -> str:
        raise RuntimeError("模拟大模型超时")

    monkeypatch.setattr(llm, "_llm_chat", boom)
    result = await llm.summarize("随便一条消息")
    assert result["source"] == "rule"          # 不抛异常，界面不白屏


@pytest.mark.asyncio
async def test_draft_reply_has_template_fallback(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    result = await llm.draft_reply("帮我看下报价", "张三")
    assert result["source"] == "rule"
    assert "张三" in result["draft"]


# ---------------- 限流降级 ----------------

@pytest.mark.asyncio
async def test_ratelimit_degrades_open_when_redis_down(monkeypatch):
    def no_redis():
        raise RuntimeError("Redis 不可用")

    monkeypatch.setattr(ratelimit, "get_redis", no_redis)
    allowed = await asyncio.wait_for(ratelimit.try_acquire("feishu", 900), timeout=5)
    assert allowed is True   # 降级放行，绝不因为限流组件故障阻断发送
