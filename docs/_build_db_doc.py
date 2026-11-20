"""生成《数据库设计》Word 文档。"""

import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _docx_common import (  # noqa: E402
    DB,
    DOCS,
    body,
    caption,
    code_block,
    cover_and_toc,
    heading,
    new_doc,
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
    caption(doc, "表 1-1 数据表清单。当前行数取自本地开发库（SQLite），"
                 "用于说明各表在演示阶段的实际使用情况。可以看到 message 与 outbox "
                 "是数据量最大的两张表，符合“消息系统以流水为主”的特征；platform_account "
                 "与 user_account 行数较少，因为它们是配置类数据，每个平台只需一行。")

    # ---------------------------------------------------------------- 2 逐表
    heading(doc, "二、表结构详述", 1)
    body(doc, "本节逐张说明字段含义。类型列写 SQLite 的声明类型，括号内是 PostgreSQL 对应类型——"
              "两者由模型里的 with_variant 自动映射，业务代码不需要区分。")

    pg_types = {
        "BIGINT": "BIGINT", "INTEGER": "INTEGER", "VARCHAR": "VARCHAR",
        "TEXT": "TEXT", "DATETIME": "TIMESTAMPTZ", "JSON": "JSONB",
        "BOOLEAN": "BOOLEAN", "FLOAT": "DOUBLE PRECISION",
    }

    for i, name in enumerate(ORDER, start=1):
        cn, desc = META[name]
        info = schema[name]
        heading(doc, f"2.{i} {name}（{cn}）", 2)

        rows = []
        for c in info["cols"]:
            base = c["type"].split("(")[0].upper()
            pg = pg_types.get(base, base)
            typ = f"{c['type']}（PG: {pg}）" if pg != base else c["type"]
            flag = []
            if c["pk"]:
                flag.append("主键")
            if c["notnull"]:
                flag.append("非空")
            rows.append((
                c["name"],
                typ,
                "、".join(flag) or "可空",
                c["default"] if c["default"] is not None else "—",
            ))
        table(doc, ["字段", "类型", "约束", "默认值"], rows, widths=[4.2, 5.4, 2.6, 3.0], font_size=8)
        caption(doc, f"表 2-{i} {name}：{desc}"
                     f"共 {len(info['cols'])} 个字段，当前 {info['rows']:,} 行。"
                     + ("外键：" + "、".join(f"{a} → {b}" for a, b in info["fks"]) + "。" if info["fks"] else "")
                     + ("索引：" + "、".join(n for n, _u in info["idx"]) + "。" if info["idx"] else ""))

    # ---------------------------------------------------------------- 3 关系
    heading(doc, "三、表间关系与约束", 1)
    body(doc, "九张表可以分成四组：流水组（message、outbox、todo、feedback、event_log）、"
              "配置组（platform_account、user_account）、账号组（app_user）与规则组（auto_rule）。"
              "组之间的耦合刻意做得很轻，任何一组出问题都不会让主链路停摆。")

    rel_rows = [
        ("outbox.parent_message_id", "message.id", "SET NULL", "出站消息对应的入站消息；原始消息被删时置空而不是级联删除"),
        ("todo.source_message_id", "message.id", "SET NULL", "待办来自哪条消息"),
        ("feedback.message_id", "message.id", "SET NULL", "修正记录挂在哪条消息上"),
        ("message.account_id", "user_account.id", "SET NULL", "该消息属于哪个用户身份（应用身份为 NULL）"),
    ]
    table(doc, ["外键字段", "指向", "删除策略", "说明"], rel_rows, widths=[4.6, 3.4, 2.2, 6.8])
    caption(doc, "表 3-1 外键与删除策略。所有外键统一使用 SET NULL 而不是 CASCADE，"
                 "原因是消息是审计凭证：撤销一个平台授权不应该连带删掉已经收到的历史消息。"
                 "早期版本这里用的是 CASCADE，第三方代码审查指出后已改正。")

    body(doc, "唯一约束承担了另一部分一致性职责：")
    uniq_rows = [
        ("uq_message_platform_msgid", "message(platform, platform_msg_id)", "同一平台同一条消息只入库一次，重连、回填、采集器重复推送都靠它去重"),
        ("uq_account_platform_app", "platform_account(platform, app_id)", "一个平台的一个应用只保留一份配置"),
        ("uq_user_account_platform_user", "user_account(platform, user_id)", "一个平台的一个用户身份只保留一份授权"),
    ]
    table(doc, ["约束名", "字段", "作用"], uniq_rows, widths=[5.0, 5.2, 6.8])
    caption(doc, "表 3-2 唯一约束。uq_message_platform_msgid 是幂等性的唯一保证："
                 "所有写入路径（实时事件、历史回填、外部采集器）都用 INSERT ... ON CONFLICT DO NOTHING "
                 "并指定这个索引，因此同一条平台消息重复到达时只会被静默忽略，不会产生重复行。")

    # ---------------------------------------------------------------- 4 索引
    heading(doc, "四、索引与查询路径", 1)
    body(doc, "索引不是按“可能用到”加的，而是按界面上真实存在的查询加的。")
    idx_rows = [
        ("ix_message_platform_status", "message(platform, status)", "左侧会话列表按平台筛 + 统计待处理数"),
        ("ix_message_created_at", "message(created_at)", "消息流按时间倒序分页"),
        ("ix_outbox_next_retry", "outbox(next_retry_at)", "worker 取下一批待投递任务（配合 SKIP LOCKED）"),
        ("ix_event_log_received", "event_log(received_at)", "排查时按时间翻原始事件"),
        ("ix_message_account", "message(account_id)", "按用户身份查消息（用户身份通道预留）"),
    ]
    table(doc, ["索引名", "覆盖字段", "服务的查询"], idx_rows, widths=[5.4, 5.0, 6.6])
    caption(doc, "表 4-1 索引清单。其中 ix_outbox_next_retry 最关键：出站 worker 用"
                 "“UPDATE ... WHERE id IN (SELECT ... ORDER BY id LIMIT n FOR UPDATE SKIP LOCKED)”"
                 "批量领取任务，没有这个索引会在任务堆积时全表扫描。")

    # ---------------------------------------------------------------- 5 数据流
    heading(doc, "五、数据流转", 1)
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
    caption(doc, "图 5-1 消息处理链路。这张图说明了为什么入站与出站要共用一张 message 表："
                 "第②步和第④步写入的是同一张表的不同方向行，界面上按 created_at 排序即可还原"
                 "完整对话。event_log 与 feedback 是两条旁路：前者为排查保留原始报文，"
                 "后者为人机协同保留修正痕迹。")

    # ---------------------------------------------------------------- 6 方言
    heading(doc, "六、方言适配", 1)
    body(doc, "同一套模型要同时跑在 SQLite（本地开发）与 PostgreSQL（容器部署），"
              "差异集中在四处，都用“按方言分支”处理，没有引入 ORM 之外的抽象层。")
    diff_rows = [
        ("主键类型", "BigInteger().with_variant(Integer, 'sqlite')", "SQLite 只有 INTEGER PRIMARY KEY 才自增"),
        ("JSON 列", "JSON().with_variant(JSONB(), 'postgresql')", "SQLite 无 JSONB，用 TEXT 存 JSON"),
        ("插入去重", "sqlite_insert / pg_insert", "两者的 on_conflict 语法不同"),
        ("时间差", "julianday() / extract(epoch from)", "平均处理时长的算法不同"),
    ]
    table(doc, ["差异点", "处理方式", "原因"], diff_rows, widths=[3.4, 6.6, 7.0])
    caption(doc, "表 6-1 SQLite 与 PostgreSQL 的适配点。采用“同一模型 + 运行时分支”"
                 "而不是两套 SQL 文件，是为了避免迁移脚本漂移——本地能跑、容器里跑不起来，"
                 "是这类项目最常见的翻车点。补齐字段则用幂等的 ALTER TABLE，"
                 "见 db.py 的 _ensure_columns()。")

    out = DOCS / "03-数据库设计.docx"
    doc.save(out)
    print("saved:", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
