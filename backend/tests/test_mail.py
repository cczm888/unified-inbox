"""邮箱适配器单测：MIME 解析 / 中文头解码 / 附件识别 / 统一格式对齐。

运行：pytest -q    （纯离线，不需要真实邮箱）
"""
from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.adapters.dingtalk import DingTalkAdapter
from app.adapters.feishu import FeishuAdapter
from app.adapters.mail import MailAdapter, _html_to_text, _parse_mail
from app.adapters.telegram import TelegramAdapter


async def _noop(platform: str, raw: dict) -> None:  # pragma: no cover
    return None


@pytest.fixture
def mail() -> MailAdapter:
    return MailAdapter(
        "me@example.com",
        "auth-code",
        "imap.example.com",
        "smtp.example.com",
        _noop,
    )


def _build_eml(*, with_attachment: bool = True, html: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = "张三 <zhangsan@corp.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = "周报：本周进度"
    msg["Message-ID"] = "<abc123@corp.com>"
    msg["Date"] = "Sun, 02 Nov 2026 20:30:00 +0800"
    if html:
        msg.set_content("<p>请查收</p><p>本周进度 80%</p>", subtype="html")
    else:
        msg.set_content("请查收，本周进度 80%。\n下周继续。")
    if with_attachment:
        msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="周报.pdf")
    return msg.as_bytes()


def test_parse_mail_decodes_chinese_headers():
    raw = _parse_mail(_build_eml(), "42")
    assert raw["subject"] == "周报：本周进度"
    assert raw["from_addr"] == "zhangsan@corp.com"
    assert raw["from_name"] == "张三"
    assert "本周进度 80%" in raw["body"]
    assert raw["message_id"] == "<abc123@corp.com>"


def test_parse_mail_finds_attachment_with_part_number():
    raw = _parse_mail(_build_eml(), "42")
    assert len(raw["attachments"]) == 1
    att = raw["attachments"][0]
    assert att["name"] == "周报.pdf"
    assert att["kind"] == "file"
    assert att["uid"] == "42"
    assert att["part"]  # IMAP BODY[part] 编号必须存在，否则下载不了


def test_parse_mail_html_only_body_becomes_text():
    raw = _parse_mail(_build_eml(with_attachment=False, html=True), "7")
    assert "请查收" in raw["body"]
    assert "本周进度 80%" in raw["body"]
    assert "<p>" not in raw["body"]


def test_html_to_text_strips_tags():
    assert _html_to_text("<div>a<br>b</div>") == "a\nb"


def test_mail_to_unified_basic(mail):
    msg = mail.to_unified(_parse_mail(_build_eml(), "42"))
    assert msg is not None
    assert msg.platform == "email"
    assert msg.chat_id == "zhangsan@corp.com"
    assert msg.chat_type == "email"
    assert msg.sender_name == "张三"
    assert msg.msg_type == "file"
    assert msg.attachments[0]["name"] == "周报.pdf"
    assert msg.created_at is not None
    assert msg.created_at.year == 2026


def test_mail_ignores_self_sent(mail):
    raw = _parse_mail(_build_eml(), "42")
    raw["from_addr"] = "ME@example.com"  # 自己发出去的，不能回流成入站
    assert mail.to_unified(raw) is None


def test_mail_text_only_has_no_attachments(mail):
    msg = mail.to_unified(_parse_mail(_build_eml(with_attachment=False), "9"))
    assert msg is not None
    assert msg.msg_type == "text"
    assert msg.attachments == []


def test_mail_auto_subject(mail):
    assert mail._auto_subject("收到，我马上处理\n第二行").startswith("[统一收件箱] 收到")
    assert mail._auto_subject("") == "[统一收件箱] 回复"


def test_four_platforms_share_one_row_shape(mail):
    """四个平台归一化后字段集合必须完全一致 —— 统一收件箱能成立的前提。"""
    feishu = FeishuAdapter("cli_x", "secret_x", _noop)
    dingtalk = DingTalkAdapter("id", "secret", "robot", _noop)
    telegram = TelegramAdapter("123:abc", _noop)

    rows = {
        "feishu": feishu.to_unified(
            {
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou"}},
                    "message": {"message_id": "m", "chat_id": "c", "message_type": "text", "content": '{"text":"a"}'},
                },
            }
        ),
        "dingtalk": dingtalk.to_unified(
            {"msgId": "m", "conversationId": "c", "msgtype": "text", "text": {"content": "a"}, "conversationType": "1"}
        ),
        "telegram": telegram.to_unified(
            {"message_id": 1, "date": 1762000000, "text": "a", "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}
        ),
        "email": mail.to_unified(_parse_mail(_build_eml(), "42")),
    }
    shapes = {name: set(m.as_row().keys()) for name, m in rows.items() if m is not None}
    base = shapes["feishu"]
    for name, shape in shapes.items():
        assert shape == base, f"{name} 的字段集合与飞书不一致"
    assert len(shapes) == 4
