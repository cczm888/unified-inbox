"""API 层的请求/响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TodoIn(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    due_at: datetime | None = None
    assignee: str | None = None


class StatusIn(BaseModel):
    status: str = Field(pattern="^(received|claimed|replied|closed)$")
    # 是否把状态变更回写到原会话。
    # 默认 False：整理自己的处理进度不需要惊动对方；需要让对方知道进度时显式打开。
    notify: bool = False


class TodoStatusIn(BaseModel):
    status: str = Field(pattern="^(pending|in_progress|done|cancelled)$")


class PriorityIn(BaseModel):
    priority: str = Field(pattern="^(urgent|normal|low)$")


class BulkIdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class BulkStatusIn(BulkIdsIn):
    status: str = Field(pattern="^(received|claimed|replied|closed)$")


class BulkPriorityIn(BulkIdsIn):
    priority: str = Field(pattern="^(urgent|normal|low)$")


class ForwardIn(BaseModel):
    target_platform: str
    target_chat: str
    note: str | None = None


class ReadIn(BaseModel):
    """把某个会话标为已读（或只标某几条）。"""

    platform: str
    chat_id: str
    message_ids: list[int] | None = None


class TestSendIn(BaseModel):
    chat_id: str
    text: str = "统一收件箱连通性测试 ✅"


class ConnectIn(BaseModel):
    """界面上添加一个平台：只会用到其中一两项，按平台取。"""

    platform: str
    # email: 邮箱地址；telegram: 随便填（用 token）；feishu: app_id；dingtalk: client_id
    app_id: str = ""
    # email: 授权码；telegram: bot token；feishu: app_secret；dingtalk: client_secret
    secret: str = ""
    label: str | None = None
    # 可选覆盖：imap_host / smtp_host / imap_port / smtp_port / poll_seconds / mark_seen / robot_code
    extra: dict | None = None


class MessageOut(BaseModel):
    id: int
    platform: str
    chat_id: str
    chat_type: str | None = None
    sender_name: str | None = None
    sender_id: str | None = None
    msg_type: str
    text: str | None = None
    attachments: list = []
    direction: str
    status: str
    priority: str
    read_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TodoOut(BaseModel):
    id: int
    platform: str | None = None
    chat_id: str | None = None
    title: str
    status: str
    assignee: str | None = None
    due_at: datetime | None = None
    source_message_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ThreadOut(BaseModel):
    platform: str
    chat_id: str
    chat_title: str | None = None
    last_text: str | None = None
    last_at: datetime | None = None
    total: int
    pending: int
    unread: int = 0       # 还没看过的入站消息数
    urgent: int = 0       # 待处理里的紧急消息数（用于置顶）
