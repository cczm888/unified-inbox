"""统一消息格式转换单测 —— 初赛「初步验证证据」里最容易复现的一项。

运行：pytest -q     （不需要数据库、不需要任何平台凭证）
"""
from __future__ import annotations

import pytest

from app.adapters.dingtalk import DingTalkAdapter
from app.adapters.feishu import FeishuAdapter
from app.adapters.telegram import TelegramAdapter


async def _noop(platform: str, raw: dict) -> None:  # pragma: no cover
    return None


@pytest.fixture
def feishu() -> FeishuAdapter:
    return FeishuAdapter("cli_x", "secret_x", _noop)


@pytest.fixture
def dingtalk() -> DingTalkAdapter:
    return DingTalkAdapter("id", "secret", "robot", _noop)


@pytest.fixture
def telegram() -> TelegramAdapter:
    return TelegramAdapter("123:abc", _noop)


def test_feishu_text(feishu):
    raw = {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "create_time": "1762000000000"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"报价单帮我看下，今天要"}',
                "create_time": "1762000000000",
            },
        },
    }
    msg = feishu.to_unified(raw)
    assert msg is not None
    assert msg.platform == "feishu"
    assert msg.platform_msg_id == "om_1"
    assert msg.chat_type == "group"
    assert "今天要" in msg.text
    assert msg.sender_id == "ou_1"
    assert msg.created_at is not None


def test_feishu_file(feishu):
    raw = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_2",
                "chat_id": "oc_1",
                "message_type": "file",
                "content": '{"file_key":"file_x","file_name":"报价.xlsx"}',
            },
        },
    }
    msg = feishu.to_unified(raw)
    assert msg is not None
    assert msg.msg_type == "file"
    assert msg.attachments[0]["name"] == "报价.xlsx"


def test_feishu_ignores_non_message_event(feishu):
    assert feishu.to_unified({"header": {"event_type": "im.chat.updated_v1"}, "event": {}}) is None


def test_dingtalk_text(dingtalk):
    raw = {
        "msgId": "msg_1",
        "conversationId": "cid_1",
        "conversationType": "2",
        "msgtype": "text",
        "text": {"content": " 明天下午三点开会 "},
        "senderNick": "张三",
        "senderStaffId": "staff_1",
        "createAt": 1762000000000,
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=x",
    }
    msg = dingtalk.to_unified(raw)
    assert msg is not None
    assert msg.chat_type == "group"
    assert msg.text == "明天下午三点开会"
    assert msg.sender_name == "张三"
    # 出站优先用 sessionWebhook，必须能从 raw 里取到
    assert msg.raw["sessionWebhook"].startswith("https://")


def test_dingtalk_drops_self_message(dingtalk):
    raw = {"msgId": "m1", "msgtype": "text", "text": {"content": "hi"}, "chatbotUserId": "bot_1", "senderStaffId": "bot_1"}
    assert dingtalk.to_unified(raw) is None


def test_telegram_text(telegram):
    raw = {
        "message_id": 42,
        "date": 1762000000,
        "text": "hello from tg",
        "chat": {"id": 1001, "type": "private", "username": "yg"},
        "from": {"id": 1001, "first_name": "YG", "is_bot": False},
    }
    msg = telegram.to_unified(raw)
    assert msg is not None
    assert msg.platform_msg_id == "1001:42"
    assert msg.chat_type == "p2p"
    assert msg.sender_name == "YG"


def test_telegram_document(telegram):
    raw = {
        "message_id": 43,
        "date": 1762000000,
        "caption": "合同在这",
        "chat": {"id": 1001, "type": "private"},
        "from": {"id": 1001, "first_name": "YG"},
        "document": {"file_name": "合同.pdf", "file_id": "f_1", "mime_type": "application/pdf"},
    }
    msg = telegram.to_unified(raw)
    assert msg is not None
    assert msg.msg_type == "file"
    assert msg.attachments[0]["name"] == "合同.pdf"


def test_all_platforms_share_one_row_shape(feishu, dingtalk, telegram):
    """三个平台归一化后，字段集合必须完全一致 —— 这是统一收件箱能成立的前提。"""
    samples = {
        "feishu": {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {"sender": {"sender_id": {"open_id": "ou"}}, "message": {"message_id": "m", "chat_id": "c", "message_type": "text", "content": '{"text":"a"}'}},
        },
        "dingtalk": {"msgId": "m", "conversationId": "c", "msgtype": "text", "text": {"content": "a"}, "conversationType": "1"},
        "telegram": {"message_id": 1, "date": 1762000000, "text": "a", "chat": {"id": 1, "type": "private"}, "from": {"id": 1}},
    }
    rows = {}
    for name, raw in samples.items():
        adapter = {"feishu": feishu, "dingtalk": dingtalk, "telegram": telegram}[name]
        msg = adapter.to_unified(raw)
        assert msg is not None
        rows[name] = set(msg.as_row().keys())
    base = rows["feishu"]
    assert rows["dingtalk"] == base
    assert rows["telegram"] == base
