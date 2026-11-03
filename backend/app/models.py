"""数据模型：统一消息 / 待办 / 出站队列 / 事件日志 / 反馈。

对应 docs/初赛设计文档.md 里"关键表结构"一节。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base

# ---------------------------------------------------------------------------
# 多数据库兼容层
#   * 生产/Docker 用 PostgreSQL：JSONB 可建 GIN 索引、BIGINT 自增
#   * 本地零依赖演示用 SQLite：JSONB 不存在 → 退化为 JSON；SQLite 只有
#     INTEGER PRIMARY KEY 才会自增，BIGINT 主键必须降级为 INTEGER
# 两者字段名、语义完全一致，业务代码不需要感知。
# ---------------------------------------------------------------------------
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
PK_BIGINT = BigInteger().with_variant(Integer, "sqlite")
FK_BIGINT = BigInteger().with_variant(Integer, "sqlite")


class UTCDateTime(TypeDecorator):
    """始终以 UTC 感知时间进出的 DateTime。

    为什么需要它：SQLite（以及部分驱动）读回来的 datetime 是 naive 的，
    FastAPI 序列化时就不会带时区后缀，前端 dayjs("2026-11-02T10:45:43")
    会按**浏览器本地时间**解析，于是 UTC 的时间被当成北京时间显示，
    整整差了 8 小时。这里在读出/写入两头都补上 UTC 时区信息。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class PlatformAccount(Base):
    """平台凭证登记表：密钥加密后存库，便于多租户扩展与轮换。"""

    __tablename__ = "platform_account"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_key: Mapped[str | None] = mapped_column(String(128))
    app_id: Mapped[str | None] = mapped_column(String(128))
    app_secret_enc: Mapped[str | None] = mapped_column(Text)   # 建议 pgcrypto 加密，禁止明文
    bot_ref: Mapped[str | None] = mapped_column(String(128))   # 机器人 robotCode / bot id
    # 界面上"添加平台"时填的自由配置（邮箱的 imap_host / smtp_host / 端口 / 轮询间隔等）
    label: Mapped[str | None] = mapped_column(String(128))     # 给人看的名字，如 abc@qq.com
    extra: Mapped[dict | None] = mapped_column(JSON_TYPE)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/disabled/error
    last_error: Mapped[str | None] = mapped_column(Text)  # 最近一次启动失败的原因，界面上直接显示
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    __table_args__ = (UniqueConstraint("platform", "app_id", name="uq_account_platform_app"),)


class UserAccount(Base):
    """以「人」的身份接入的平台账号（区别于 PlatformAccount 的「应用」身份）。

    为什么需要它：
      * 应用身份（tenant_access_token / bot token）只收得到**机器人参与**的消息，
        群里没 @ 机器人的说话、机器人没进去的群，一概收不到；
      * 用户身份（OAuth 拿到的 user_access_token / Telegram 用户会话）能看到
        这个人在平台里**能看到的一切**——自己加入的所有群、全部单聊、历史消息。

    token 全部加密落库（services/crypto.py），明文只在内存里存在几秒钟。
    """

    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)   # open_id / tg user id
    user_name: Mapped[str | None] = mapped_column(String(128))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    tenant_key: Mapped[str | None] = mapped_column(String(128))

    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    refresh_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scope: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), default="active")  # active/expired/revoked
    last_error: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("platform", "user_id", name="uq_user_account_platform_user"),
    )


class Message(Base):
    """统一消息：不管来自哪个平台，入站出站都落这张表。"""

    __tablename__ = "message"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    # 消息归属于哪个"人"的账号：应用身份接入时为 NULL，用户身份接入时填账号 id。
    # 加上这一维之后，"我的产品群"和"你的产品群"才是两个不同会话。
    account_id: Mapped[int | None] = mapped_column(
        FK_BIGINT, ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_msg_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_type: Mapped[str | None] = mapped_column(String(16))       # p2p / group
    chat_title: Mapped[str | None] = mapped_column(String(256))
    sender_id: Mapped[str | None] = mapped_column(String(128))
    sender_name: Mapped[str | None] = mapped_column(String(128))
    msg_type: Mapped[str] = mapped_column(String(16), default="text")  # text/file/image/card
    text_body: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON_TYPE, default=list)
    thread_root: Mapped[str | None] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(4), default="in")   # in / out
    status: Mapped[str] = mapped_column(String(16), default="received")
    # received -> claimed -> replied -> closed
    priority: Mapped[str] = mapped_column(String(8), default="normal")  # urgent / normal / low
    # 已读标记：员工在网页上打开过这个会话就算读过。
    # 和 status 是两件事——status 是「处理进度」，read_at 是「看没看过」。
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), index=True
    )
    raw: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)

    __table_args__ = (
        # 幂等去重：同一个平台的消息重复推送只入库一次
        UniqueConstraint("platform", "platform_msg_id", name="uq_message_platform_msgid"),
        Index("ix_message_platform_status", "platform", "status"),
    )

    @property
    def text(self) -> str | None:
        """对外统一字段名 text（DB 列名保持 text_body，便于和统一格式对齐）。"""
        return self.text_body


class Todo(Base):
    """由消息派生出的待办，状态变更需要回流到原平台。"""

    __tablename__ = "todo"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    source_message_id: Mapped[int | None] = mapped_column(
        FK_BIGINT, ForeignKey("message.id", ondelete="SET NULL")
    )
    platform: Mapped[str | None] = mapped_column(String(32))
    chat_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    assignee: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending -> in_progress -> done / cancelled
    platform_todo_id: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Outbox(Base):
    """出站队列：所有发送先落库再投递，天然可重试、可幂等、可限流。"""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    parent_message_id: Mapped[int | None] = mapped_column(FK_BIGINT)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    target_chat: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # queued -> sending -> sent / failed -> dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    result_msg_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class EventLog(Base):
    """原始事件留档：答辩时可回放、可复现。"""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), default="message")
    raw: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), index=True
    )


class Feedback(Base):
    """人工修正记录：闭环里"数据反馈、持续优化"8 分的落地证据。"""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    message_id: Mapped[int | None] = mapped_column(FK_BIGINT)
    kind: Mapped[str] = mapped_column(String(32))      # category / priority / reply_template
    old_value: Mapped[str | None] = mapped_column(String(256))
    new_value: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
