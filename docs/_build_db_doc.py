"""生成《数据库设计》Word 文档。"""

import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _docx_common import (  # noqa: E402
    chapter_lead,
    DB,
    DOCS,
    FONT_CN,
    FONT_EN,
    body,
    caption,
    code_block,
    cover_and_toc,
    heading,
    line_spacing,
    new_doc,
    set_run_font,
    start_body_section,
    table,
)

# 表的中文名与职责（人工写的，SQLite 读不出来）
META = {
    "message": ("消息表", "统一收件箱的核心表。各平台的原始消息经适配器转换后写入这里，"
                          "入站与出站共用一张表，用 direction 区分方向、用 status 记录处理进度。"),
    "outbox": ("出站队列", "所有要发出去的东西先进这张表，再由后台 worker 取走投递。"
                          "限流、重试、退避、失败归档都在这一层，与具体平台无关。"),
    "platform_account": ("平台接入配置", "界面上「接入新平台」写入的凭证，密码用 Fernet 加密后存 app_secret_enc。"
                                         "status 区分 active / disabled / error，停用不删行，避免误操作丢配置。"),
    "user_account": ("用户身份授权", "走 OAuth 拿到的用户级令牌（飞书用户身份）。与平台接入配置分开存，"
                                    "因为一个平台可以同时有应用身份和用户身份两套凭证。"),
    "app_user": ("系统用户", "本系统自己的登录账号。第一个注册者即所有者，密码用 PBKDF2-SHA256 加盐存储。"),
    "auto_rule": ("自动回复规则", "关键词命中即自动回复。命中次数与最后命中时间记在这里，便于观察规则是否真的在用。"),
    "todo": ("待办事项", "从某条消息派生出的待办，可回写状态到原平台。"),
    "event_log": ("原始事件日志", "每一条进来的平台原始事件都留一份，用于排查「平台到底推没推」。"),
    "feedback": ("反馈记录", "人工修正的留痕（改紧急度、改状态、改分类），前后值都记，为后续做偏好学习留数据。"),
}

ORDER = [
    "message", "outbox", "platform_account", "user_account",
    "app_user", "auto_rule", "todo", "event_log", "feedback",
]


# 字段中文名与解释（官方要求：每个数据表一个表格，含表名、序号、字段英文名、
# 字段类型、字段中文名、字段解释）。SQLite 读不出中文名，这里是人工维护的。
COL_CN = {
    "message": {
        "id": ("主键", "自增主键"),
        "platform": ("平台", "feishu / email / telegram / feishu_web"),
        "platform_msg_id": ("平台消息 ID", "平台侧唯一标识，与 platform 组成幂等键"),
        "chat_id": ("会话 ID", "平台侧会话标识，回复时用它定位"),
        "chat_type": ("会话类型", "p2p 单聊 / group 群聊"),
        "chat_title": ("会话名称", "群名或对方名称，列表上显示"),
        "sender_id": ("发送者 ID", "平台侧用户标识"),
        "sender_name": ("发送者名称", "显示用，平台可能不给"),
        "msg_type": ("消息类型", "text / post / image / file / audio"),
        "text_body": ("正文", "富文本降级后的纯文本，搜索与摘要都读它"),
        "attachments": ("附件", "数组：文件名、大小、下载地址、sha256"),
        "thread_root": ("话题根", "飞书话题回复的根消息 ID"),
        "direction": ("方向", "in 入站 / out 出站"),
        "status": ("处理状态", "received / claimed / replied / closed"),
        "priority": ("紧急度", "normal / urgent / critical"),
        "created_at": ("平台时间", "消息在平台上产生的时间"),
        "raw": ("原始报文", "平台原始 JSON，平台差异只存这里"),
        "read_at": ("已读时间", "空表示未读；与 status 分开存"),
        "account_id": ("接入账号", "由哪套平台配置接入，外键 SET NULL"),
    },
    "outbox": {
        "id": ("主键", "自增主键"),
        "parent_message_id": ("来源消息", "回复的是哪条消息，外键 SET NULL"),
        "platform": ("目标平台", "投递到哪个平台"),
        "target_chat": ("目标会话", "发给哪个 chat_id"),
        "payload": ("投递内容", "正文、附件、引用关系的 JSON"),
        "status": ("队列状态", "queued / sending / sent / failed / dead / rpa"),
        "attempts": ("已尝试", "投递次数，用于指数退避"),
        "next_retry_at": ("下次重试", "退避后的时间点，worker 按它取任务"),
        "last_error": ("最后错误", "失败原因，便于排查"),
        "result_msg_id": ("结果消息 ID", "成功后平台返回的 ID，用于回填防重"),
        "created_at": ("入队时间", "进入队列的时间"),
    },
    "platform_account": {
        "id": ("主键", "自增主键"),
        "platform": ("平台", "feishu / email / telegram / dingtalk"),
        "tenant_key": ("租户标识", "飞书 tenant_key"),
        "app_id": ("应用 ID", "平台侧应用标识"),
        "app_secret_enc": ("应用密钥", "密文，Fernet 加密，密钥来自环境变量"),
        "bot_ref": ("机器人标识", "机器人 open_id"),
        "status": ("状态", "active / disabled / error，停用不删行"),
        "created_at": ("创建时间", "接入时间"),
        "label": ("备注名", "多套凭证时用于区分"),
        "extra": ("附加配置", "平台特有字段的 JSON"),
        "last_error": ("最后错误", "连接失败原因"),
    },
    "user_account": {
        "id": ("主键", "自增主键"),
        "platform": ("平台", "当前只有 feishu 支持用户身份"),
        "user_id": ("用户 ID", "平台侧用户标识"),
        "user_name": ("用户名", "显示用"),
        "avatar_url": ("头像地址", "界面上显示"),
        "tenant_key": ("租户标识", "飞书 tenant_key"),
        "access_token_enc": ("访问令牌", "密文，调用平台接口用"),
        "refresh_token_enc": ("刷新令牌", "密文，30 天有效"),
        "token_expires_at": ("令牌过期", "访问令牌到期时间"),
        "refresh_expires_at": ("刷新过期", "刷新令牌到期时间"),
        "scope": ("授权范围", "空格分隔的 scope 列表"),
        "status": ("状态", "active / expired / error"),
        "last_error": ("最后错误", "刷新失败原因"),
        "last_sync_at": ("最后同步", "上次同步目录数据的时间"),
        "created_at": ("创建时间", "授权时间"),
        "updated_at": ("更新时间", "任意字段变更时更新"),
    },
    "app_user": {
        "id": ("主键", "自增主键"),
        "username": ("用户名", "登录名，3–32 字符、不含空格"),
        "password_hash": ("口令哈希", "PBKDF2-SHA256 20 万轮加盐，不可逆"),
        "display_name": ("显示名", "界面上显示的名字"),
        "created_at": ("创建时间", "注册时间"),
        "last_login_at": ("最后登录", "上次登录成功时间"),
    },
    "auto_rule": {
        "id": ("主键", "自增主键"),
        "platform": ("适用平台", "空表示全部平台"),
        "keyword": ("关键词", "命中即触发；空表示该平台的兜底规则"),
        "reply_text": ("回复内容", "自动回复的正文"),
        "priority": ("优先级", "数字小的先匹配，第一条命中即用"),
        "enabled": ("是否启用", "开关，停用不删规则"),
        "hit_count": ("命中次数", "累计命中，用于观察规则是否真在用"),
        "created_at": ("创建时间", "规则添加时间"),
        "last_hit_at": ("最后命中", "最近一次命中的时间"),
    },
    "todo": {
        "id": ("主键", "自增主键"),
        "source_message_id": ("来源消息", "从哪条消息派生，外键 SET NULL"),
        "platform": ("平台", "回写状态时用"),
        "chat_id": ("会话 ID", "回写状态时定位会话"),
        "title": ("标题", "待办内容"),
        "due_at": ("截止时间", "可为空"),
        "assignee": ("负责人", "可为空"),
        "status": ("状态", "open / doing / done"),
        "platform_todo_id": ("平台待办 ID", "回写成功后平台返回的标识"),
        "updated_at": ("更新时间", "状态变更时更新"),
        "created_at": ("创建时间", "派生时间"),
    },
    "event_log": {
        "id": ("主键", "自增主键"),
        "platform": ("来源平台", "事件来自哪个平台"),
        "event_type": ("事件类型", "平台事件名，如 im.message.receive_v1"),
        "raw": ("原始报文", "平台推来的完整 JSON，排查用"),
        "received_at": ("接收时间", "本系统收到的时间"),
    },
    "feedback": {
        "id": ("主键", "自增主键"),
        "message_id": ("关联消息", "修正的是哪条消息，外键 SET NULL"),
        "kind": ("修正类型", "priority / status / category"),
        "old_value": ("修改前", "原值"),
        "new_value": ("修改后", "新值"),
        "created_at": ("记录时间", "修正发生时间"),
    },
}

# 数据表清单的「与其他表关系」列
REL = {
    "message": "被 outbox.parent_message_id、todo.source_message_id、feedback.message_id 引用；account_id → platform_account.id",
    "outbox": "parent_message_id → message.id",
    "platform_account": "被 message.account_id 引用",
    "user_account": "独立；按 platform 与 platform_account 对应",
    "app_user": "独立（单用户）",
    "auto_rule": "独立；按 platform 匹配 message.platform",
    "todo": "source_message_id → message.id",
    "event_log": "独立（只追加，不参与业务关联）",
    "feedback": "message_id → message.id",
}


def read_schema():
    con = sqlite3.connect(DB)
    out = {}
    for (name,) in con.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
    ):
        cols = []
        for cid, cname, ctype, notnull, default, pk in con.execute(f'pragma table_info("{name}")'):
            cols.append({
                "name": cname,
                "type": ctype or "—",
                "notnull": notnull,
                "default": default,
                "pk": pk,
            })
        idx = [
            (r[1], r[2])
            for r in con.execute(f'pragma index_list("{name}")')
            if not r[1].startswith("sqlite_autoindex")
        ]
        fks = [(r[3], r[4]) for r in con.execute(f'pragma foreign_key_list("{name}")')]
        cnt = con.execute(f'select count(*) from "{name}"').fetchone()[0]
        out[name] = {"cols": cols, "idx": idx, "fks": fks, "rows": cnt}
    return out


def main():
    doc = new_doc()
    cover_and_toc(doc, "数据库设计", "跨平台消息互通系统（统一收件箱）")
    start_body_section(doc)

    schema = read_schema()

    # ---------------------------------------------------------------- 1
    heading(doc, "1. 数据表清单", 1)
    chapter_lead(doc, "9 张表，流水与配置分开：流水只追加，配置每个平台一行。")
    body(doc, "本系统把飞书、邮箱、Telegram、钉钉等平台的消息收敛到同一张表里，"
              "再在同一个界面上完成阅读、处理与回复。首要目标不是「把各平台的数据存下来」，"
              "而是「让不同来源的消息在结构上不可区分」。四条设计原则：统一优先（平台差异只留在 "
              "raw 字段，新增平台不改表结构）、方向同表（入站出站共用 message 表，用 direction "
              "区分，一条会话时间线一次查询拉全）、投递与业务解耦（先写 outbox 再由 worker 投递，"
              "限流重试都收敛在这层）、可移植（with_variant 处理方言差异，本地不必依赖 Docker）。")

    rows = []
    for i, name in enumerate(ORDER, start=1):
        cn, desc = META[name]
        rows.append((str(i), f"{name}（{cn}）", desc.split("。")[0] + "。", REL[name]))
    table(doc, ["序号", "数据表名", "作用", "与其他表关系"], rows,
          widths=[1.0, 3.4, 6.0, 4.6], font_size=8)
    caption(doc, "表 1-1 数据表清单与关联关系。message 与 outbox 是流水主干，"
                 "platform_account 与 user_account 是配置类数据，每个平台只需一行。")

    # ---------------------------------------------------------------- 2
    heading(doc, "2. 数据表内容", 1)
    chapter_lead(doc, "九张表逐张列出字段：英文名、类型、中文名与解释。")
    body(doc, "类型列写的是 SQLite 的声明类型；PostgreSQL 侧由模型里的 with_variant 自动映射，"
              "只有四处不同：DATETIME→TIMESTAMPTZ、JSON→JSONB、FLOAT→DOUBLE PRECISION、"
              "自增主键 BIGINT→BIGSERIAL，其余同名，业务代码不区分方言。"
              "「主键」「非空」这类约束写进类型列，便于对照建表语句。")

    for ti, name in enumerate(ORDER, start=1):
        cn, _desc = META[name]
        heading(doc, f"2.{ti} {name}（{cn}）", 2)
        cols = schema[name]["cols"]
        rows = []
        for i, c in enumerate(cols, start=1):
            cn_name, expl = COL_CN[name].get(c["name"], ("—", "—"))
            typ = c["type"] or "—"
            flag = []
            if c["pk"]:
                flag.append("PK")
            if c["notnull"]:
                flag.append("NOT NULL")
            if flag:
                typ = f"{typ} {' '.join(flag)}"
            rows.append((str(i), c["name"], typ, cn_name, expl))
        table(doc, ["序号", "字段英文名", "字段类型", "字段中文名", "字段解释"],
              rows, widths=[0.9, 3.3, 3.2, 2.4, 5.2], font_size=7.5)
        caption(doc, f"表 2-{ti} {name}（{cn}）共 {len(cols)} 个字段。"
                     f"当前行数 {schema[name]['rows']:,} 行。")

    # ---------------------------------------------------------------- 3
    heading(doc, "3. SQL 语句", 1)
    chapter_lead(doc, "九张表的建表语句，与上表字段逐一对应，可直接在 SQLite 执行。")
    body(doc, "下面是本地开发库实际使用的建表语句（去掉自增语法差异后 PostgreSQL 同样可建）。"
              "唯一约束与外键写在语句末尾，说明见第四章。")

    con = sqlite3.connect(DB)
    for ti, name in enumerate(ORDER, start=1):
        cn, _d = META[name]
        row = con.execute(
            "select sql from sqlite_master where type='table' and name=?", (name,)
        ).fetchone()
        ddl = (row[0] if row and row[0] else f"-- {name} 建表语句缺失").strip()
        code_block(doc, f"-- ({ti}) {name}  {cn}\n{ddl}", size=6.5, space_after=5)
    con.close()

    # ---------------------------------------------------------------- 4
    heading(doc, "4. 其他", 1)
    chapter_lead(doc, "约束与索引、消息流转路径、SQLite 与 PostgreSQL 的适配点。")

    heading(doc, "4.1 关系、约束与索引", 2)
    body(doc, "九张表分成三组：流水组（message、outbox、todo、feedback、event_log）、"
              "配置组（platform_account、user_account、app_user、auto_rule）。")
    body(doc, "外键统一用 SET NULL 而不是 CASCADE：消息是审计凭证，撤销一个平台的授权"
              "不应该连带删掉已经收到的历史消息。早期的版本用过 CASCADE，第三方审查指出后改正。"
              "唯一约束只有一处：uq_message_platform_msgid，它是幂等的保证——"
              "长连接重连、历史回填、采集器重复推送三条路径都用 ON CONFLICT DO NOTHING，"
              "重复到达不会产生重复行。")

    constraints = [
        ("外键", "fk_outbox_parent", "outbox.parent_message_id → message.id", "删消息不删投递记录，置空"),
        ("外键", "fk_todo_source", "todo.source_message_id → message.id", "同上，保留待办本身"),
        ("外键", "fk_feedback_message", "feedback.message_id → message.id", "修正记录独立留存"),
        ("外键", "fk_message_account", "message.account_id → platform_account.id", "删配置不删消息，置空"),
        ("唯一", "uq_message_platform_msgid", "message(platform, platform_msg_id)", "幂等写入的唯一保证"),
        ("唯一", "uq_platform_account", "platform_account(platform, label)", "同一平台可挂多套凭证"),
        ("索引", "ix_message_chat_time", "message(chat_id, created_at)", "按会话拉时间线"),
        ("索引", "ix_message_status", "message(status)", "待处理计数与筛选"),
        ("索引", "ix_outbox_status_retry", "outbox(status, next_retry_at)", "worker 取任务"),
        ("索引", "ix_todo_status", "todo(status)", "待办页签"),
        ("索引", "ix_event_platform_time", "event_log(platform, received_at)", "按平台查事件"),
        ("索引", "ix_auto_rule_enabled", "auto_rule(enabled, priority)", "规则匹配顺序"),
    ]
    table(doc, ["类别", "名称", "涉及字段", "作用"], constraints,
          widths=[1.4, 4.6, 5.0, 4.0], font_size=7.5)
    caption(doc, "表 4-1 外键、唯一约束与索引。")

    heading(doc, "4.2 数据流转", 2)
    body(doc, "一条消息从进来到出去只经过一次入库与一次出队，下图是完整路径，"
              "也说明了为什么入站与出站共用一张 message 表：")
    code_block(doc, """
平台事件 / IMAP / HTTP 推送
        │
        ▼  适配器转 UnifiedMessage
   message 表（ON CONFLICT DO NOTHING 幂等）
        │
        ├──► 统一界面（会话列表 → 消息流 → 处理面板）
        │
        └──► 自动回复规则命中 ─┐
                              ▼
                         outbox 表（queued）
                              │  worker：限流 → 发送 → 重试/退避
                              ▼
                         sent / failed / dead
                              │
                              ▼
                  回填一条 direction=out 的 message
""")
    caption(doc, "图 4-1 消息处理链路。入站与出站都落在 message 表，靠 direction 区分。")

    heading(doc, "4.3 方言适配", 2)
    body(doc, "同一套模型要能在 SQLite（本地开发、评审快速验证）与 PostgreSQL（部署）上跑，"
              "做法是「同一模型 + 运行时分支」，不是维护两套 SQL。")
    diff_rows = [
        ("自增主键", "BIGINT + autoincrement → BIGSERIAL / IDENTITY", "两库自增语法不同"),
        ("JSON 列", "JSON（TEXT 存）→ JSONB（可索引）", "PG 有原生 JSONB，查询更快"),
        ("时间列", "DATETIME 存 naive UTC → TIMESTAMPTZ", "避免时区二义性"),
        ("插入去重、时间差", "ON CONFLICT DO NOTHING 与非原生 SQL 分支", "两边 SQL 方言不同"),
    ]
    table(doc, ["差异点", "处理方式", "原因"], diff_rows, widths=[3.2, 7.2, 6.6], font_size=8)
    caption(doc, "表 4-2 SQLite 与 PostgreSQL 的适配点。")

    heading(doc, "4.4 AI 对话过程", 2)
    body(doc, "本项目使用 AI 辅助开发，全部对话过程导出后放在代码仓库的 ai-session-export 目录，"
              "链接：https://github.com/cczm888/unified-inbox/tree/main/ai-session-export 。")

    out = DOCS / "03_数据库设计.docx"
    doc.save(out)
    print("saved:", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
