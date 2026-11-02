-- ---------------------------------------------------------------
-- 建表参考 SQL（与 app/models.py 保持一致，可直接贴进初赛设计文档）
-- 生产由 SQLAlchemy create_all 自动建表，这份用于文档与人工核查
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS platform_account (
    id              BIGSERIAL PRIMARY KEY,
    platform        VARCHAR(32)  NOT NULL,
    tenant_key      VARCHAR(128),
    app_id          VARCHAR(128),
    app_secret_enc  TEXT,
    bot_ref         VARCHAR(128),
    status          VARCHAR(16)  DEFAULT 'active',
    created_at      TIMESTAMPTZ  DEFAULT now(),
    CONSTRAINT uq_account_platform_app UNIQUE (platform, app_id)
);

CREATE TABLE IF NOT EXISTS message (
    id              BIGSERIAL PRIMARY KEY,
    platform        VARCHAR(32)  NOT NULL,
    platform_msg_id VARCHAR(128) NOT NULL,
    chat_id         VARCHAR(128) NOT NULL,
    chat_type       VARCHAR(16),
    chat_title      VARCHAR(256),
    sender_id       VARCHAR(128),
    sender_name     VARCHAR(128),
    msg_type        VARCHAR(16)  DEFAULT 'text',
    text_body       TEXT,
    attachments     JSONB        DEFAULT '[]'::jsonb,
    thread_root     VARCHAR(128),
    direction       VARCHAR(4)   DEFAULT 'in',
    status          VARCHAR(16)  DEFAULT 'received',
    priority        VARCHAR(8)   DEFAULT 'normal',
    created_at      TIMESTAMPTZ  DEFAULT now(),
    raw             JSONB        DEFAULT '{}'::jsonb,
    CONSTRAINT uq_message_platform_msgid UNIQUE (platform, platform_msg_id)
);
CREATE INDEX IF NOT EXISTS ix_message_created_at ON message (created_at);
CREATE INDEX IF NOT EXISTS ix_message_platform_status ON message (platform, status);

CREATE TABLE IF NOT EXISTS todo (
    id                 BIGSERIAL PRIMARY KEY,
    source_message_id  BIGINT REFERENCES message(id) ON DELETE SET NULL,
    platform           VARCHAR(32),
    chat_id            VARCHAR(128),
    title              VARCHAR(512) NOT NULL,
    due_at             TIMESTAMPTZ,
    assignee           VARCHAR(128),
    status             VARCHAR(16) DEFAULT 'pending',
    platform_todo_id   VARCHAR(128),
    updated_at         TIMESTAMPTZ DEFAULT now(),
    created_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbox (
    id                 BIGSERIAL PRIMARY KEY,
    parent_message_id  BIGINT,
    platform           VARCHAR(32)  NOT NULL,
    target_chat        VARCHAR(128) NOT NULL,
    payload            JSONB        DEFAULT '{}'::jsonb,
    status             VARCHAR(16)  DEFAULT 'queued',
    attempts           INT          DEFAULT 0,
    next_retry_at      TIMESTAMPTZ  DEFAULT now(),
    last_error         TEXT,
    result_msg_id      VARCHAR(128),
    created_at         TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_outbox_next_retry ON outbox (next_retry_at);

CREATE TABLE IF NOT EXISTS event_log (
    id           BIGSERIAL PRIMARY KEY,
    platform     VARCHAR(32) NOT NULL,
    event_type   VARCHAR(64) DEFAULT 'message',
    raw          JSONB       DEFAULT '{}'::jsonb,
    received_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_event_log_received ON event_log (received_at);

CREATE TABLE IF NOT EXISTS feedback (
    id          BIGSERIAL PRIMARY KEY,
    message_id  BIGINT,
    kind        VARCHAR(32),
    old_value   VARCHAR(256),
    new_value   VARCHAR(256),
    created_at  TIMESTAMPTZ DEFAULT now()
);
