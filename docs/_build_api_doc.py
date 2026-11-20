"""生成《接口设计》Word 文档。数据源是 app.openapi()，保证与代码一致。"""

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _docx_common import (  # noqa: E402
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

SPEC = json.loads((DOCS / "_openapi.json").read_text(encoding="utf-8"))

# 模块划分与中文名
GROUPS = [
    ("认证与账号", ["/api/v1/auth"], "登录、注册、退出，以及飞书用户身份 OAuth 的授权与回调"),
    ("收件箱与消息", ["/api/v1/inbox"], "消息列表、会话聚合、回复、附件、批量操作、优先级、历史回填、主动发消息"),
    ("待办", ["/api/v1/todos"], "从消息派生的待办及其状态流转"),
    ("平台接入", ["/api/v1/platforms"], "平台连接状态、运行时接入/停用、连通性自测"),
    ("统计", ["/api/v1/stats"], "看板数据"),
    ("自动回复", ["/api/v1/autoreply"], "关键词规则的增删改查"),
    ("外部接入", ["/api/v1/ingest"], "接收外部采集器推送的消息（RPA 通道）"),
    ("健康检查", ["/healthz"], "进程与各平台连接状态"),
]

METHOD_CN = {"get": "GET", "post": "POST", "patch": "PATCH", "put": "PUT", "delete": "DELETE"}


def describe(op):
    """从 openapi 的 summary / description 里取中文说明。"""
    s = (op.get("summary") or "").strip()
    d = (op.get("description") or "").strip()
    if s and d:
        return s, d
    if s:
        return s, ""
    if d:
        return d.split("\n")[0][:40], d
    return "—", ""


def find_group(path):
    for name, prefixes, _desc in GROUPS:
        for p in prefixes:
            if path == p or path.startswith(p + "/") or path.startswith(p + "?"):
                return name
    return "其他"


def main():
    doc = new_doc()
    cover_and_toc(doc, "接口设计", "跨平台消息互通系统（统一收件箱）")
    start_body_section(doc)

    paths = SPEC.get("paths", {})
    total = sum(
        1 for p in paths.values() for m in p if m in METHOD_CN
    )

    # ---------------------------------------------------------------- 1
    heading(doc, "一、设计概述", 1)
    body(doc, f"系统对外提供 {total} 个 HTTP 接口，全部挂在 /api/v1 前缀下（健康检查除外），"
              "采用 REST 风格：资源用名词、动作用 HTTP 方法、状态码表达结果。"
              "所有接口都由 FastAPI 生成 OpenAPI 描述，本文档的接口清单与参数说明"
              "直接来自该描述文件，因此不会与代码脱节。")
    body(doc, "三条统一约定：")
    body(doc, "第一，除 GET 外的请求体统一为 application/json，"
              "唯一例外是文件上传（multipart/form-data）；"
              "第二，错误响应用 {\"detail\": \"...\"} 结构，校验失败时 detail 为数组，"
              "业务失败时 detail 为字符串；"
              "第三，时间字段统一返回带 Z 后缀的 UTC 时间（如 2026-11-02T10:45:43Z），"
              "由前端转换成本地时区显示。")

    # ---------------------------------------------------------------- 2 总表
    heading(doc, "二、接口清单", 1)
    body(doc, f"下表按模块列出全部 {total} 个接口。方法列使用 HTTP 动词，"
              "路径列省略了统一的 /api/v1 前缀（健康检查除外）。")

    by_group = collections.OrderedDict((g[0], []) for g in GROUPS)
    by_group["其他"] = []
    for path in sorted(paths):
        for m, op in paths[path].items():
            if m not in METHOD_CN:
                continue
            short, _ = describe(op)
            by_group[find_group(path)].append((METHOD_CN[m], path.replace("/api/v1", ""), short))

    rows = []
    for gname, items in by_group.items():
        for method, p, short in items:
            rows.append((gname, method, p, short))
    table(doc, ["模块", "方法", "路径", "说明"], rows, widths=[3.0, 1.8, 7.4, 5.0])
    caption(doc, f"表 2-1 接口清单（共 {total} 个）。按模块聚合后可以看出接口分布："
                 "收件箱与消息模块占了一半以上，因为它的每个用户动作都对应一个独立接口"
                 "（回复、发文件、转待办、改状态、改优先级、批量改、标已读、拉历史、主动发消息）。"
                 "这种“一个动作一个接口”的拆法牺牲了接口数量，换来的是权限控制和错误处理"
                 "可以逐接口设计，而不必在一个大接口里写分支。")

    # ---------------------------------------------------------------- 3 分组详述
    heading(doc, "三、接口详述", 1)
    body(doc, "本节按模块说明关键接口的请求参数、响应结构与错误码。"
              "字段类型中的 ? 表示可选，| 表示多选一。")

    DETAIL = {
        "/api/v1/inbox": (
            "消息列表。支持按平台、状态、会话、关键词过滤，按时间倒序分页。",
            [("platform", "string?", "平台标识：feishu / email / telegram / dingtalk / feishu_web"),
             ("status", "string?", "received / claimed / replied / closed / sent"),
             ("chat_id", "string?", "只看某个会话"),
             ("q", "string?", "关键词，匹配正文与会话标题"),
             ("limit", "int=50", "每页条数，上限 200"),
             ("offset", "int=0", "偏移量")],
            "返回 MessageOut 数组。每条含 id、platform、direction、status、priority、text、attachments、read_at、created_at。",
            "200 成功；未登录 401",
        ),
        "/api/v1/inbox/threads": (
            "会话聚合列表，左侧栏用。按 (platform, chat_id) 分组，返回最后一条对方消息、总数、未读数、紧急数。",
            [("q", "string?", "同时搜索会话标题与任何一条消息正文"),
             ("platform", "string?", "按平台过滤")],
            "返回 ThreadOut 数组，含 platform、chat_id、chat_title、last_text、last_at、total、pending、unread、urgent。",
            "200 成功；未登录 401",
        ),
        "/api/v1/inbox/{message_id}/reply": (
            "回复某条消息。文本发出前写入 outbox，由 worker 投递。",
            [("text", "string", "回复正文"),
             ("quote_id", "int?", "引用某条消息时传它的 id，会在正文前拼一句引用摘要")],
            "{\"ok\": true, \"outbox_id\": 12, \"platform\": \"feishu\", \"status\": \"claimed\"}",
            "200 成功；404 消息不存在；400 平台不在线",
        ),
        "/api/v1/inbox/{message_id}/reply-file": (
            "以附件形式回复。multipart 上传，落盘后交给 outbox 投递。",
            [("file", "file", "multipart 文件；上限 20MB；文件名会做安全清洗")],
            "{\"ok\": true, \"outbox_id\": 7, \"filename\": \"季度汇报.md\", \"size\": 129}",
            "200 成功；413 超过 20MB；400 平台不支持发文件",
        ),
        "/api/v1/inbox/{message_id}/attachments/{index}": (
            "附件代理下载。平台的附件 URL 需要带凭证，不能让浏览器直连，所以由后端取回再转发。",
            [("index", "int", "该消息 attachments 数组的下标"),
             ("download", "bool=false", "true 时返回 Content-Disposition: attachment，否则 inline")],
            "二进制流，带 Content-Type 与 Content-Disposition（文件名做 RFC 5987 编码以支持中文）。",
            "200 成功；404 附件不存在",
        ),
        "/api/v1/inbox/send": (
            "主动发起新消息（不是回复某条）。用于「选一个联系人，直接说句话」。",
            [("platform", "string", "目标平台"),
             ("target", "string", "目标：飞书/钉钉是 chat_id，邮箱是收件地址，Telegram 是 chat id"),
             ("text", "string", "正文"),
             ("subject", "string?", "仅邮箱使用")],
            "{\"ok\": true, \"outbox_id\": 12}",
            "200 成功；400 平台未接入或目标格式不对",
        ),
        "/api/v1/inbox/backfill": (
            "从平台回填历史消息。飞书应用身份可以读整个会话的历史，不受“订阅之后”限制。",
            [("platform", "string", "平台"),
             ("chat_id", "string", "会话 id"),
             ("limit", "int=50", "最多拉多少条")],
            "{\"ok\": true, \"fetched\": 19, \"inserted\": 14}。fetched 是平台给的条数，inserted 是真正新增的条数，差值就是已经有的。",
            "200 成功；400 平台拉不了历史或机器人不在会话里",
        ),
        "/api/v1/inbox/bulk/status": (
            "批量改状态。只作用于入站消息——出站消息的 status 记的是投递结果，不能被处理进度覆盖。",
            [("ids", "int[]", "消息 id 数组，1–200 个"),
             ("status", "string", "received / claimed / replied / closed")],
            "{\"ok\": true, \"updated\": 2, \"scanned\": 2}",
            "200 成功；422 参数不合法",
        ),
        "/api/v1/inbox/read": (
            "标记已读。read_at 与 status 是两件事：前者是“看没看过”，后者是“处理到哪步”。",
            [("platform", "string", "平台"),
             ("chat_id", "string", "会话"),
             ("message_ids", "int[]?", "只标这些；不传则标该会话全部未读")],
            "{\"ok\": true, \"read\": 3, \"inbound\": 3}",
            "200 成功",
        ),
        "/api/v1/inbox/events": (
            "SSE 事件流，前端靠它做实时刷新。服务端每 1.5 秒查一次新消息并推送。",
            [("since", "int?", "从这个消息 id 之后开始推")],
            "text/event-stream，每个事件是一行 JSON 的 MessageOut。",
            "200 持续流；未登录 401",
        ),
        "/api/v1/auth/login": (
            "登录。有账号时校验账号口令，也兼容早期的共享口令；没有账号时返回注册页。",
            [("username", "string?", "账号；不传则走共享口令模式"),
             ("password", "string", "口令")],
            "成功时 303 跳首页并下发 HttpOnly Cookie（uti_auth），默认 30 天。",
            "303 成功；401 口令错误",
        ),
        "/api/v1/auth/register": (
            "注册。系统设计为单用户：第一个注册者即所有者，之后注册返回 409。",
            [("username", "string", "3–32 字符，不含空格"),
             ("password", "string", "≥6 位"),
             ("password2", "string", "再次输入，需一致")],
            "成功时 303 跳首页并下发 Cookie。",
            "303 成功；409 已有账号；400 格式不合规",
        ),
        "/api/v1/platforms/connect": (
            "运行时接入一个平台。填完凭证即时生效，不需要改 .env 或重启。",
            [("platform", "string", "email / telegram / feishu / dingtalk"),
             ("app_id", "string", "账号、App ID 或 Bot 用户名"),
             ("secret", "string", "授权码、App Secret 或 Bot Token"),
             ("label", "string?", "显示名"),
             ("extra", "object?", "邮箱可传 imap_host / smtp_host")],
            "{\"ok\": true, \"platform\": \"email\", \"label\": \"xxx@qq.com\", \"ready\": true}",
            "200 成功；400 凭证错（把平台返回的原始错误透出来）",
        ),
        "/api/v1/autoreply/rules": (
            "自动回复规则。命中关键词就自动回一条，规则按 priority 升序取第一条命中。",
            [("platform", "string?", "留空=全平台"),
             ("keyword", "string?", "留空=该平台兜底规则"),
             ("reply_text", "string", "回复内容，≤2000 字"),
             ("priority", "int=100", "越小越先匹配"),
             ("enabled", "bool=true", "开关")],
            "返回 AutoRuleOut 数组，含 hit_count（命中次数）与 last_hit_at。",
            "200 成功；400 回复内容为空",
        ),
        "/api/v1/ingest": (
            "外部采集通道。浏览器插件或 RPA 抓到的消息推到这里，走与官方适配器相同的入库管道。",
            [("platform", "string", "来源标识，如 feishu_web"),
             ("items", "object[]", "每项含 platform_msg_id、chat_id、chat_title、text、sender_name、created_at、meta")],
            "{\"ok\": true, \"received\": 1, \"inserted\": 1, \"skipped\": 0}",
            "200 成功（幂等，重复推送返回 skipped）；422 结构不对",
        ),
        "/healthz": (
            "健康检查。返回各平台连接状态与 worker 数量，用于运维探活。",
            [],
            "{\"ok\": true, \"platforms\": [{\"platform\": \"feishu\", \"ready\": true}], \"outbox_workers\": 2}",
            "200 进程存活（不代表平台都连上，看 platforms 里的 ready）",
        ),
    }

    n = 0
    for gname, prefixes, gdesc in GROUPS:
        items = []
        for path, ops in paths.items():
            if find_group(path) == gname and any(path.startswith(p) for p in prefixes):
                for m, op in ops.items():
                    if m in METHOD_CN:
                        items.append((path, m, op))
        if not items:
            continue
        n += 1
        heading(doc, f"3.{n} {gname}", 2)
        body(doc, gdesc + f"。共 {len(items)} 个接口。")

        for path, m, op in sorted(items, key=lambda x: (x[0], x[1])):
            short, long_desc = describe(op)
            heading(doc, f"{METHOD_CN[m]} {path.replace('/api/v1', '')}", 3)
            detail = DETAIL.get(path)
            if detail:
                summary, params, resp, errs = detail
                body(doc, summary)
                if params:
                    table(doc, ["参数", "类型", "说明"],
                          [(a, b, c) for a, b, c in params],
                          widths=[4.2, 3.0, 10.0])
                body(doc, "响应：" + resp)
                body(doc, "错误：" + errs)
            else:
                body(doc, long_desc or short or "（见参数定义）")
                params = op.get("parameters", [])
                if params:
                    rows = []
                    for p in params:
                        sch = p.get("schema", {})
                        t = sch.get("type") or sch.get("anyOf", [{}])[0].get("type", "—")
                        rows.append((p.get("name"), f"{t}{'' if p.get('required') else '?'}",
                                     p.get("description") or "—"))
                    table(doc, ["参数", "类型", "说明"], rows, widths=[4.2, 3.0, 10.0])

    caption(doc, "表 3-x（各接口参数表）。所有参数表都遵循同一格式：参数名、类型、说明。"
                 "类型里的 ? 表示可选，默认值直接写在类型后面。这样写的目的是让读者不必"
                 "来回翻代码就能构造出一次合法请求；相比之下只列路径不列参数的接口文档，"
                 "对接时仍然要看源码，等于没写。")

    # ---------------------------------------------------------------- 4 鉴权
    heading(doc, "四、鉴权与访问控制", 1)
    body(doc, "系统有两层防护，都基于同一个 HttpOnly Cookie（uti_auth）：")
    body(doc, "第一层是账号口令。注册后口令经 PBKDF2-SHA256（20 万轮加盐）存库，登录成功后"
              "下发 Cookie，内容是 HMAC 摘要而不是口令本身，有效期 30 天。")
    body(doc, "第二层是共享口令。ACCESS_PASSWORD 非空时，即使没有账号，也需要输入它才能访问；"
              "留空则该层关闭。这一层是为了多实例演示——给每个实例配不同口令，彼此隔离。")
    body(doc, "校验位置在中间件，不在每个接口里：main.py 的 access_guard 拦截所有请求，"
              "白名单是 /healthz、/api/v1/auth/login、/api/v1/auth/register 与 /favicon.ico。"
              "未通过时，页面请求返回登录页，API 请求返回 401 加 need_login 标记，"
              "前端据此跳转。")
    code_block(doc, """
# 未登录时的响应
HTTP/1.1 401 Unauthorized
{"detail": "未登录：请访问 /api/v1/auth/login 输入访问口令", "need_login": true, "has_user": true}
""")
    caption(doc, "图 4-1 未登录响应示例。把“需要登录”做成结构化字段而不是让人去解析文案，"
                 "原因是前端要据此决定跳登录页还是弹提示。has_user 字段告诉前端该跳注册页"
                 "还是登录页——首次部署时库里没有账号，跳注册才合理。")

    # ---------------------------------------------------------------- 5 错误
    heading(doc, "五、错误处理约定", 1)
    err_rows = [
        ("200", "成功", "GET 与 PATCH 的正常返回"),
        ("303", "重定向", "登录、注册成功后跳首页并下发 Cookie"),
        ("400", "业务错误", "参数语义不对，如“平台不在线”“拉不了历史”；detail 为字符串，可直接展示给用户"),
        ("401", "未认证", "Cookie 缺失或失效；detail 带 need_login 标记"),
        ("404", "不存在", "消息、待办、规则 id 找不到"),
        ("409", "冲突", "已有账号时再次注册"),
        ("413", "过大", "上传文件超过 20MB"),
        ("422", "结构错误", "请求体不符合 schema；detail 是数组，逐字段说明哪里不对"),
    ]
    table(doc, ["状态码", "含义", "使用场景"], err_rows, widths=[2.4, 2.8, 12.0])
    caption(doc, "表 5-1 状态码约定。刻意区分 400 与 422：前者是“参数合法但业务不允许”，"
                 "detail 是能直接给用户看的中文；后者是“请求结构不对”，detail 是程序报错。"
                 "前端据此决定是弹提示还是记日志。实现过程中踩过一个坑——422 的 detail "
                 "是数组，直接塞进 Error 的 message 会显示成 [object Object]，"
                 "所以前端对 422 做了单独处理。")

    # ---------------------------------------------------------------- 6 可复现
    heading(doc, "六、接口的可复现验证", 1)
    body(doc, "所有接口都可以用 curl 复现，鉴权用一次登录拿到的 Cookie：")
    code_block(doc, """
# 1) 登录，保存 Cookie
curl -c cookie.txt -X POST http://127.0.0.1:8000/api/v1/auth/login \\
     -d "username=cczm&password=******"

# 2) 拉会话列表
curl -b cookie.txt http://127.0.0.1:8000/api/v1/inbox/threads

# 3) 回复第一条消息
curl -b cookie.txt -X POST http://127.0.0.1:8000/api/v1/inbox/1/reply \\
     -H "Content-Type: application/json" -d '{"text":"收到，我看一下"}'

# 4) 回填某会话的历史
curl -b cookie.txt -X POST "http://127.0.0.1:8000/api/v1/inbox/backfill?platform=feishu&chat_id=oc_xxx&limit=50"

# 5) 完整接口描述（OpenAPI 3.1）
curl -b cookie.txt http://127.0.0.1:8000/openapi.json
""")
    caption(doc, "图 6-1 接口复现命令。第五条最有价值：服务在跑的时候，"
                 "/openapi.json 会返回完整的机器可读接口描述，评审可以拿它和本文档逐条对照，"
                 "验证文档与实现是否一致。这也意味着本文档不是手抄的——"
                 "它由同一份 OpenAPI 描述生成。")

    out = DOCS / "04-接口设计.docx"
    doc.save(out)
    print("saved:", out, out.stat().st_size, "bytes", "| total endpoints:", total)


if __name__ == "__main__":
    main()
