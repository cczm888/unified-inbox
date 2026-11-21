"""生成《接口设计》Word 文档。数据源是 app.openapi()，保证与代码一致。"""

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _docx_common import (  # noqa: E402
    chapter_lead,
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

    # ---------------------------------------------------------------- 1 清单
    heading(doc, "1. 接口清单", 1)
    chapter_lead(doc, f"{total} 个接口、{len(paths)} 条路径，收件箱占一半：一个用户动作一个接口。")
    body(doc, f"系统对外提供 {total} 个 HTTP 接口，分布在 {len(paths)} 条路径上"
              "（部分路径同时支持 GET 与 POST），全部挂在 /api/v1 前缀下（健康检查除外），"
              "采用 REST 风格：资源用名词、动作用 HTTP 方法、状态码表达结果。"
              "下表由 FastAPI 生成的 OpenAPI 描述直接产出，因此不会与代码脱节。")

    by_group = collections.OrderedDict((g[0], []) for g in GROUPS)
    by_group["其他"] = []
    for path in sorted(paths):
        for m, op in paths[path].items():
            if m not in METHOD_CN:
                continue
            short, _ = describe(op)
            by_group[find_group(path)].append(
                (op.get("summary") or short, f"{METHOD_CN[m].split('/')[0]} {path.replace('/api/v1', '')}", short)
            )

    rows = []
    n = 0
    for _gname, items in by_group.items():
        for name, route, short in items:
            n += 1
            rows.append((str(n), name, route, short))
    table(doc, ["序号", "接口名称", "接口路由", "接口解释"], rows,
          widths=[1.0, 4.6, 5.2, 5.2], font_size=8)
    caption(doc, f"表 1-1 接口清单（共 {total} 个）。收件箱模块占一半以上，"
                 "因为它的每个用户动作都是独立接口，权限与错误处理才能逐接口设计。")

    # ---------------------------------------------------------------- 2 详情
    heading(doc, "2. 接口详情", 1)
    chapter_lead(doc, "八个「少了它闭环就断」的接口，逐个给请求、响应、错误与涉及的数据表。")
    body(doc, "其余接口多为标准增删改查，参数可由上表与 /openapi.json 取得。"
              "下面八个决定闭环能否走通，格式统一为「做什么 / 请求 / 响应 / 错误 / 涉及数据表」，"
              "参数里的 ? 表示可选，｜ 表示多选一。")

    def core(tag, title, what, req, resp, err, tables):
        heading(doc, f"{tag} {title}", 2)
        body(doc, what)
        body(doc, "请求：" + req)
        body(doc, "响应：" + resp)
        body(doc, "错误：" + err)
        body(doc, "涉及数据表：" + tables)

    core("2.1", "GET /inbox/threads",
         "会话聚合列表，左侧栏的数据源。按 (platform, chat_id) 分组，算出最后一条对方消息、"
         "总条数、未读数、紧急数。最后一条优先取对方说的话——否则自己刚发的自动回执会把真实对话顶出预览。",
         "q（关键词，同时搜会话标题与消息正文）｜ platform（平台过滤）",
         "ThreadOut[]：platform、chat_id、chat_title、last_text、last_at、total、pending、unread、urgent",
         "401 未登录",
         "message（按 chat_id 分组聚合）、platform_account（过滤已接入平台）")

    core("2.2", "POST /inbox/{message_id}/reply",
         "回复某条消息。正文先写进 outbox，由 worker 投递，接口立刻返回、不阻塞在平台响应上。"
         "可选 quote_id 引用历史消息——各平台原生引用实现不一，统一层用「引用块 + 正文」的文本形式保证跨平台一致。",
         "text（正文）｜ quote_id?（被引用消息的 id，会在正文前拼一句摘要）",
         "{\"ok\": true, \"outbox_id\": 12, \"platform\": \"feishu\", \"status\": \"claimed\"}。"
         "状态是 claimed 而不是 replied——真正变成 replied 要等 worker 发送成功回写。",
         "400 平台不在线；404 消息不存在",
         "message（读父消息、写状态）、outbox（入队）、feedback（记状态变更）")

    core("2.3", "POST /inbox/{message_id}/reply-file",
         "以附件形式回复。multipart 上传，落盘后交给 outbox 投递。"
         "实测中这条链路穿过一次 Word 文档，往返后 sha256 与原件一致。",
         "file（multipart，≤20MB，文件名做安全清洗）",
         "{\"ok\": true, \"outbox_id\": 7, \"filename\": \"季度汇报.md\", \"size\": 129}",
         "413 超过 20MB；400 平台不支持发文件",
         "message（父消息）、outbox（payload 里存附件路径与大小）")

    core("2.4", "POST /inbox/send",
         "主动发起新消息，不是回复某条。闭环里「选一个联系人，直接说句话」走的这个接口。"
         "与 reply 的区别是没有父消息，出站记录由 worker 发送成功后补登。",
         "platform（目标平台）｜ target（飞书/钉钉是 chat_id，邮箱是地址，Telegram 是 chat id）"
         "｜ text（正文）｜ subject?（仅邮箱）",
         "{\"ok\": true, \"outbox_id\": 12}",
         "400 平台未接入，或目标格式不对（如邮箱缺 @）",
         "outbox（入队，parent_message_id 为空）、message（发送成功后补登出站记录）")

    core("2.5", "POST /inbox/backfill",
         "从平台回填历史。长连接只能收到订阅之后的消息，接入前的老消息靠这个补——"
         "飞书的应用身份可以读整个会话的历史，不受订阅时间限制。",
         "platform（平台）｜ chat_id（会话）｜ limit（默认 50）",
         "{\"ok\": true, \"fetched\": 19, \"inserted\": 14}。fetched 是平台给的条数，"
         "inserted 是真正新增的，两者之差就是已经有的。",
         "400 平台拉不了历史，或机器人不在这个会话里",
         "message（ON CONFLICT DO NOTHING 幂等写入）、event_log（记录本次回填）")

    core("2.6", "POST /platforms/connect",
         "运行时接入一个平台。填完凭证即时生效，不需要改 .env 也不需要重启——"
         "这是「填凭证即接入」的关键，否则每加一个平台都要停机。凭证用 Fernet 加密后入库，接口从不返回明文。",
         "platform（email / telegram / feishu / dingtalk）｜ app_id（账号、App ID 或 Bot 用户名）"
         "｜ secret（授权码、App Secret 或 Bot Token）｜ label?（显示名）｜ extra?（邮箱可传 imap_host / smtp_host）",
         "{\"ok\": true, \"platform\": \"email\", \"label\": \"xxx@qq.com\", \"ready\": true}",
         "400 凭证错——把平台返回的原始错误透出来，而不是笼统地说“接入失败”",
         "platform_account（写凭证密文与状态）、event_log（记录接入事件）")

    core("2.7", "POST /ingest",
         "外部采集通道。浏览器插件或 RPA 抓到的消息推到这里，走与官方适配器完全相同的入库管道"
         "（同一套幂等去重、同一套自动回复触发）。这是「官方接口拿不到时的补充通道」，不是主链路。",
         "platform（来源标识，如 feishu_web）｜ items（每项含 platform_msg_id、chat_id、"
         "chat_title、text、sender_name、created_at、meta）",
         "{\"ok\": true, \"received\": 1, \"inserted\": 1, \"skipped\": 0}",
         "422 结构不对；重复推送返回 skipped 而不是报错",
         "message（幂等写入）、event_log（原始报文留档）、auto_rule（触发自动回复）")

    # ---------------------------------------------------------------- 3 其他
    heading(doc, "3. 其他", 1)
    chapter_lead(doc, "两层鉴权、一张状态码表、三条 curl，用来复现上面每一个结论。")

    # ---------------------------------------------------------------- 4 鉴权
    heading(doc, "3.1 两层鉴权", 2)
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
    heading(doc, "3.2 状态码约定", 2)
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
    heading(doc, "3.3 用 curl 复现任意接口", 2)
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

    out = DOCS / "04_接口设计.docx"
    doc.save(out)
    print("saved:", out, out.stat().st_size, "bytes", "| total endpoints:", total)


if __name__ == "__main__":
    main()
