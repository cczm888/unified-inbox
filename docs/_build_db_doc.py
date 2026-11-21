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
    heading(doc, "一、设计概述", 1)
    chapter_lead(doc, "9 张表，流水与配置分开：流水只追加，配置每个平台一行。")
    body(doc, "本系统把飞书、邮箱、Telegram、钉钉等平台的消息收敛到同一张表里，"
              "再在同一个界面上完成阅读、处理与回复。因此数据库设计的首要目标不是"
              "“把各平台的数据存下来”，而是“让不同来源的消息在结构上不可区分”。")
    body(doc, "围绕这个目标，确定了四条设计原则：")
    body(doc, "统一优先：所有平台共用一套字段，平台差异只体现在 raw 字段里保留的原始报文。"
              "新增一个平台不需要改表结构，只需要写一个适配器。")
    body(doc, "方向同表：入站消息和出站消息存同一张 message 表，用 direction 字段区分。"
              "这样一条会话的时间线可以用一次查询拉全，不必 union 两张表。")
    body(doc, "投递与业务解耦：任何要发出去的内容先写 outbox，再由后台 worker 投递。"
              "限流、重试、退避、失败归档都收敛在这一层，业务代码不需要关心平台限速。")
    body(doc, "可移植：同一套模型能在 SQLite 与 PostgreSQL 上跑。用 with_variant 处理"
              "自增主键与 JSON 列的方言差异，让本地开发不必依赖 Docker。")

    body(doc, "下面这张表列出全部 9 张表及其职责。")
    rows = []
    for name in ORDER:
        cn, desc = META[name]
        rows.append((name, cn, f"{schema[name]['rows']:,}", desc))
    table(doc, ["表名", "中文名", "当前行数", "职责"], rows, widths=[3.2, 2.2, 1.6, 8.0])
    caption(doc, "表 1-1 数据表清单，行数取自本地开发库（SQLite）。message 与 outbox 最大，"
                 "符合“消息系统以流水为主”的特征；platform_account 与 user_account 很小，"
                 "因为它们是配置类数据，每个平台只需一行。")

    # ---------------------------------------------------------------- 2 逐表
    heading(doc, "二、表结构详述", 1)
    chapter_lead(doc, "字段按组集中列，同组同表共用首列，扫一眼就能看出分组。")
    body(doc, "本节逐张说明字段含义。类型列写 SQLite 的声明类型；PostgreSQL 侧由模型里的 "
              "with_variant 自动映射，只有四处不同：DATETIME→TIMESTAMPTZ、JSON→JSONB、"
              "FLOAT→DOUBLE PRECISION、自增主键 BIGINT→BIGSERIAL，其余同名。业务代码不区分方言。"
              "为压住篇幅，同组的几张表合并在同一张明细表里：首列是表名，"
              "同一张表的字段共用一个合并单元格，扫一眼就能看出分组。")

    pg_types = {
        "BIGINT": "BIGINT", "INTEGER": "INTEGER", "VARCHAR": "VARCHAR",
        "TEXT": "TEXT", "DATETIME": "TIMESTAMPTZ", "JSON": "JSONB",
        "BOOLEAN": "BOOLEAN", "FLOAT": "DOUBLE PRECISION",
    }

    groups = [
        ("2.1 流水组：message / outbox / todo / feedback / event_log",
         "流水组记录“发生过什么”，只追加、少更新：五张表都有 created_at，索引都建在时间与状态上。",
         ["message", "outbox", "todo", "feedback", "event_log"]),
        ("2.2 配置与账号组：platform_account / user_account / app_user / auto_rule",
         "这一组记录“按什么规则运行”，行数少、改动少：凭证加密存、账号单用户、规则按优先级排序。",
         ["platform_account", "user_account", "app_user", "auto_rule"]),
    ]

    for gi, (title, intro, names) in enumerate(groups, start=1):
        heading(doc, title, 2)
        body(doc, intro)

        rows, spans = [], []
        for name in names:
            cn, _desc = META[name]
            info = schema[name]
            start = len(rows)
            for c in info["cols"]:
                base = c["type"].split("(")[0].upper()
                pg = pg_types.get(base, base)
                typ = c["type"]
                flag = []
                if c["pk"]:
                    flag.append("主键")
                if c["notnull"]:
                    flag.append("非空")
                rows.append((
                    "",
                    c["name"],
                    typ,
                    "、".join(flag) or "可空",
                    c["default"] if c["default"] is not None else "—",
                ))
            spans.append((start, len(rows) - 1, f"{name}\n{cn}\n{len(info['cols'])} 字段"))

        t = table(doc, ["表", "字段", "类型", "约束", "默认值"], rows,
                  widths=[2.9, 3.6, 4.0, 2.0, 2.4], font_size=7.5)

        # 首列按表合并，避免每行重复表名
        for start, end, label in spans:
            merged = t.cell(start + 1, 0).merge(t.cell(end + 1, 0))
            for p in list(merged.paragraphs):
                p._element.getparent().remove(p._element)
            p = merged.add_paragraph()
            line_spacing(p, 1.15)
            for k, seg in enumerate(label.split("\n")):
                if k:
                    p.add_run().add_break()
                set_run_font(p.add_run(seg), size=7.5,
                             bold=(k == 0), en=FONT_EN if k == 0 else FONT_CN)

        caption(doc, f"表 2-{gi} " + "、".join(f"{n}（{META[n][0]}）" for n in names) +
                     " 的完整字段。当前行数见第一章表 1-1；"
                     "外键、唯一约束与索引见第三章，这里只列字段本身。")

    # ---------------------------------------------------------------- 3 关系
    heading(doc, "三、关系、约束与索引", 1)
    chapter_lead(doc, "外键一律 SET NULL：撤销授权不该删掉已经收到的历史消息。")
    body(doc, "九张表可以分成四组：流水组（message、outbox、todo、feedback、event_log）、"
              "配置组（platform_account、user_account）、账号组（app_user）与规则组（auto_rule）。"
              "组之间的耦合刻意做得很轻，任何一组出问题都不会让主链路停摆。"
              "外键、唯一约束与索引都是围绕“消息要幂等、worker 要抢得快、列表要翻得动”这三件事加的，"
              "所以合在一张表里对照着看更清楚。")

    cons_rows = [
        ("外键", "outbox.parent_message_id → message.id", "SET NULL",
         "出站消息对应的入站消息；原始消息被删时置空而不是级联删除"),
        ("外键", "todo.source_message_id → message.id", "SET NULL", "待办来自哪条消息"),
        ("外键", "feedback.message_id → message.id", "SET NULL", "修正记录挂在哪条消息上"),
        ("外键", "message.account_id → user_account.id", "SET NULL",
         "该消息属于哪个用户身份（应用身份为 NULL）"),
        ("唯一", "uq_message_platform_msgid", "message(platform, platform_msg_id)",
         "同一平台同一条消息只入库一次；重连、回填、采集器重复推送都靠它去重"),
        ("唯一", "uq_account_platform_app", "platform_account(platform, app_id)",
         "一个平台的一个应用只保留一份配置"),
        ("唯一", "uq_user_account_platform_user", "user_account(platform, user_id)",
         "一个平台的一个用户身份只保留一份授权"),
        ("索引", "ix_message_platform_status", "message(platform, status)",
         "左侧会话列表按平台筛 + 统计待处理数"),
        ("索引", "ix_message_created_at", "message(created_at)", "消息流按时间倒序分页"),
        ("索引", "ix_outbox_next_retry", "outbox(next_retry_at)",
         "worker 取下一批待投递任务（配合 SKIP LOCKED）"),
        ("索引", "ix_event_log_received", "event_log(received_at)", "排查时按时间翻原始事件"),
        ("索引", "ix_message_account", "message(account_id)", "按用户身份查消息（用户身份通道预留）"),
    ]
    table(doc, ["类别", "名称", "涉及字段", "作用 / 删除策略"], cons_rows,
          widths=[1.6, 5.2, 4.6, 5.6], font_size=7.5)
    caption(doc, "表 3-1 外键、唯一约束与索引。外键统一用 SET NULL 而不是 CASCADE——"
                 "消息是审计凭证，撤销授权不该连带删掉已经收到的历史消息。"
                 "uq_message_platform_msgid 是幂等性的唯一保证：实时事件、历史回填、外部采集器"
                 "三条写入路径都用 ON CONFLICT DO NOTHING 指定它，重复到达只被静默忽略。")

    # ---------------------------------------------------------------- 4 数据流
    heading(doc, "四、数据流转", 1)
    chapter_lead(doc, "入站与出站共用一张 message 表，区别只在 direction。")
    body(doc, "一条消息从平台到达、到用户回复、再到状态回流，会经过以下表的写入：")
    code_block(doc, """
① 平台事件 → event_log（原始报文留痕）
              ↓
② 适配器转换 → message（direction=in, status=received）
              ↓  命中自动回复规则
③ auto_rule.hit_count+1 → outbox（payload.kind=text）
              ↓
④ worker 领取 → 调平台接口 → outbox.status=sent
                              → message（补一条 direction=out）
                              → 原消息 message.status=replied
              ↓  用户在网页上手动回复
⑤ 同一路径，另外写 feedback（如果人改了紧急度/状态）
""")
    caption(doc, "图 4-1 消息处理链路，也说明了为什么入站与出站共用一张 message 表："
                 "②与④写的是同一张表的两个方向，按 created_at 排序即可还原完整对话。"
                 "event_log 与 feedback 是两条旁路——前者留原始报文备排查，后者留人工修正痕迹。")

    # ---------------------------------------------------------------- 6 方言
    heading(doc, "五、方言适配", 1)
    chapter_lead(doc, "同一模型 + 运行时分支，SQLite 与 PostgreSQL 都能跑，不维护两套 SQL。")
    body(doc, "同一套模型要跑在 SQLite（本地）与 PostgreSQL（容器），差异四处，都按方言分支处理。")
    diff_rows = [
        ("主键类型", "BigInteger().with_variant(Integer, 'sqlite')", "SQLite 只有 INTEGER PRIMARY KEY 才自增"),
        ("JSON 列", "JSON().with_variant(JSONB(), 'postgresql')", "SQLite 无 JSONB，用 TEXT 存 JSON"),
        ("插入去重、时间差", "sqlite_insert / pg_insert；julianday() / extract(epoch from)", "on_conflict 语法与平均时长算法不同"),
    ]
    table(doc, ["差异点", "处理方式", "原因"], diff_rows, widths=[3.2, 7.2, 6.6], font_size=8)
    caption(doc, "表 5-1 SQLite 与 PostgreSQL 的适配点。走“同一模型 + 运行时分支”而不是两套 SQL，"
                 "是为了避免本地能跑、容器里跑不起来的迁移漂移。")

    out = DOCS / "03_数据库设计.docx"
    doc.save(out)
    print("saved:", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
