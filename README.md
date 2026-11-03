# 统一消息收件箱 · 多平台连接器（赛题 A）

[![CI](https://github.com/cczm888/unified-inbox/actions/workflows/ci.yml/badge.svg)](https://github.com/cczm888/unified-inbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](backend/requirements.txt)
[![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vue.js&logoColor=white)](web/package.json)

一个部署单元同时接入 **飞书 / 邮箱 / Telegram / 钉钉** 多个平台，把外部消息归一化成内部标准格式，
在一个网页收件箱里完成 **接收 → 统一展示 → 处理 → 发送反馈 → 状态回流** 的完整闭环。

## 1. 接入方式与当前实测状态

| 平台 | 接入方式 | 是否需要公网域名 | 审核 | 实测状态 |
| --- | --- | --- | --- | --- |
| 飞书 | WebSocket 长连接（`lark-oapi` ws.Client） | 不需要 | 个人可建企业自建应用，即时生效 | **已端到端跑通**：入站 → 网页 → 回复 → 飞书收到 → 状态回流 |
| 邮箱 | IMAP 收 + SMTP 发（标准协议） | 不需要 | 无 | **代码完成，待填授权码**：任一支持 IMAP 的邮箱（QQ/163/Gmail/企业邮） |
| Telegram | HTTP 长轮询 `getUpdates` | 不需要 | @BotFather 生成即用 | 代码完成，待填 Bot Token |
| 钉钉 | Stream 模式（`dingtalk-stream`） | 不需要 | 需组织管理员开开发者权限 | 代码完成；个人账号卡在「不是管理员」，需先建自己的组织 |

全部平台都是**出站长连接**，因此部署在一台没有公网 IP、没有备案域名的机器（甚至家里的笔记本）上即可跑通全流程 —— 这是本题最大的工程陷阱，很多队伍会卡在「回调地址必须是公网 HTTPS」上。

平台还可以**运行时接入**：打开网页 → 右上「账号」→ 抽屉底部「接入新平台」→ 选平台、填凭证 → 立即生效，不用改 `.env`、不用重启。


## 2. 一条命令启动

```bash
cp .env.example .env      # 填入飞书/钉钉（/Telegram）凭证
docker compose up -d --build
```

- 前端收件箱：<http://localhost:8080>
- 后端 API 与文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/healthz>

不填任何平台凭证也能启动：`/healthz` 会显示各平台 `ready: false`，前端给出「未就绪」提示，
这本身就是「边界与异常处理」的证据之一。

### 拿到凭证后先自检（不需要 Docker）

申请平台应用前请先看 [docs/接入操作指引.md](docs/接入操作指引.md)。拿到 App ID / Secret 之后，
不必等数据库起来，直接一条命令验证接入层是否真的通了：

```bash
cd backend
python scripts/probe.py feishu --listen 60     # 期间在飞书里给机器人发一句话
python scripts/probe.py dingtalk --listen 60
python scripts/probe.py telegram
python scripts/probe.py all                    # 三平台一起
```

它做四件事：换 token → 读机器人信息 → 列可见会话 → 开长连接等真实消息。
**跑出来的日志会自动存到 `docs/证据/`，直接就是初赛要交的「API 连通性证据」。**

## 3. 闭环在哪（评审对照表）

| 环节 | 实现位置 | 可演示动作 |
| --- | --- | --- |
| 接收 | `backend/app/adapters/*` 长连接/长轮询 → `services/inbound.py` | 从飞书/钉钉/TG 各发一条消息，收件箱实时出现（SSE 推送） |
| 统一展示 | `GET /api/v1/inbox/threads`、`/inbox`，前端三栏 | 三个平台的消息混在一个列表，按会话聚合，未读数角标 |
| 处理 | `POST /inbox/{id}/todo`、`PATCH /inbox/{id}/status`、AI 摘要/草稿 | 转待办、改状态、让模型给摘要与回复草稿 |
| 发送反馈 | `POST /inbox/{id}/reply`、`/forward` → `services/outbox.py` | 在统一入口回一条，自动转成该平台的消息格式发出 |
| 状态回流 | `PATCH /inbox/{id}/status`、`PATCH /todos/{id}` 都会往原会话发一条状态通知 | 标记「处理中/已关闭」后，对方在飞书里能收到「该消息已标记为…」 |
| 可靠投递 | outbox 表 + `FOR UPDATE SKIP LOCKED` + 指数退避 + 死信 | 断网/限流时消息不丢，恢复后自动补发 |

## 4. 统一消息格式

所有平台入站后都被映射为同一个 `UnifiedMessage`（`backend/app/adapters/base.py`）：

```
platform        平台标识 feishu|dingtalk|telegram
platform_msg_id 平台内唯一消息 ID（幂等键）
chat_id/chat_type/chat_title  会话标识、单聊/群聊、群名
sender_id/sender_name         发送者
msg_type        text|file|image
text            统一文本（文件消息取 caption）
attachments     统一附件数组 [{name,key,url,mime}]
thread_root     线程根（回复串联用）
created_at      UTC 时间
raw             平台原始 payload（排障用）
```

单测 `backend/tests/test_convert.py` 里有一条断言：三个平台归一化后**字段集合必须完全一致**，
这是「格式统一转换」这条要求的可复现证据。

## 5. 目录结构

```
unified-inbox/
├── backend/
│   ├── app/
│   │   ├── adapters/     平台适配器（feishu/dingtalk/telegram）
│   │   ├── api/          REST + SSE 路由
│   │   ├── services/     入站去重、限流、outbox 投递、AI
│   │   ├── models.py     数据模型
│   │   └── main.py       生命周期装配
│   ├── sql/001_init.sql  建表 SQL（写文档可直接引用）
│   ├── scripts/probe.py  平台连通性自检（拿凭证后先跑这个，无需 Docker）
│   └── tests/            转换层单测（无需 DB/凭证）
├── web/                  Vue3 + ant-design-vue 收件箱
├── docs/                 初赛设计文档、可行性分析、接入操作指引
└── docker-compose.yml    postgres + redis + api + web
```

## 6. 本地开发（不用 Docker）

### 6.1 一键启动（Windows，推荐演示时用）

```powershell
.\scripts\dev-up.ps1
```

脚本会：用 SQLite 起后端（`sqlite+aiosqlite:///./local.db`，不需要 Postgres 和 Redis）→ 等 `/healthz` 就绪并打印各平台状态 → 起前端 → 打开 `http://127.0.0.1:5173`。关掉弹出的两个最小化窗口即可停服。

### 6.2 手动启动

```bash
# 后端（SQLite 开发模式，不连 Postgres/Redis）
cd backend
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements.txt                    # 加 --no-cache-dir 更快
$env:DATABASE_URL="sqlite+aiosqlite:///./local.db"  # Windows PowerShell 写法
uvicorn app.main:app --reload --port 8000

# 前端
cd web
npm install && npm run dev        # http://localhost:5173，已配好 /api 代理
```

`.env` 里的 `DATABASE_URL` 默认指向 Postgres（给 Docker 用），本地开发用上面的环境变量覆盖即可，两边不冲突。Docker 部署时不需要 SQLite，走 `docker compose up` 全自动。

## 7. 限流与边界

- 每平台一个 Redis 令牌桶（`services/ratelimit.py`）：飞书 900 rpm、钉钉 20 rpm、Telegram 30 rpm。
- 拿不到令牌不阻塞线程，出站消息回到 `queued` 并延后重试，避免「发一条卡十秒」。
- 失败重试 `min(2^attempts, 60)` 秒随机抖动，超过 5 次进 `dead` 状态，可在看板里看到。
- 入站用 `(platform, platform_msg_id)` 唯一键去重，平台重投不会产生重复消息。
- 钉钉机器人会收到自己发出的消息，适配器用 `senderStaffId == chatbotUserId` 过滤，防自回环。

## 8. 已验证状态（可复现）

```bash
cd backend && python -m pytest -q      # 26 passed
cd web && npm run build                # vite build 成功，dist/ 产出
```

- 26 条单测：`test_convert.py` 8（三平台转换 + 自回环丢弃）、`test_mail.py` 9（邮件解析/HTML 转文本/附件 part 编号/**四平台字段集合一致性**）、`test_services.py` 9（outbox payload、时间戳、优先级规则、LLM 与限流降级）；
- 前端 `vite build` 3162 modules 转换成功；
- `app.openapi()` 实测 **38 条路由** + `/healthz`。

### 8.1 飞书真机联调记录（2026-11-02 实测通过）

| 环节 | 实测结果 |
| --- | --- |
| 应用与机器人 | 自建应用「统一收件箱」，已发布 1.0.0，机器人能力已添加 |
| 事件订阅 | 长连接（WebSocket），事件 `im.message.receive_v1` |
| 入站 | 飞书里给机器人发消息 → 2 秒内出现在统一收件箱，`sender_name` 解析为真实姓名 |
| 出站 | 网页里点「发送回复」→ 飞书侧收到，`message_id` 回写数据库 |
| 状态回流 | 网页标记「关闭」→ 飞书侧收到「该消息已标记为『已关闭』」 |
| 处理链路 | AI 摘要 / AI 草稿 / 转待办 / 平均处理时长统计均可用（未配 LLM Key 时走规则降级） |
| 证据 | `docs/证据/feishu-probe-*.log`（探活、收消息、发消息原始日志） |

> 联调踩到的两个真问题已修：① `lark_oapi` 在 import 时缓存事件循环，必须在该模块导入前重置 `ws_mod.loop`；② 只开 `im:message` 收不到事件，必须补 `im:message.p2p_msg:readonly`。

> Docker 相关（`docker compose up`）需在装有 Docker Desktop / Docker Engine 的机器上执行，
> 当前开发机未安装 Docker，因此 compose 编排只做了 YAML 结构与变量插值校验。

## 9. 本机环境踩坑记录（换机器时照此排查）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `pip install` 长时间零输出、`site-packages` 不增长 | pip 缓存目录异常导致阻塞 | 加 `--no-cache-dir`，并可用 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 加速 |
| `vite build` 报 `esbuild ... Access is denied` | esbuild 在 `%TEMP%` 建临时文件被拦 | 把 `TEMP`/`TMP` 指到项目内目录后再 build |
| `npm install` 报 `EALLOWSCRIPTS` | 某些 npm 发行版启用了依赖脚本白名单，esbuild 的 postinstall 被拒 | 在 `package.json` 加 `"allowScripts": {"esbuild": true}`，或设 `npm_config_allow_scripts` 前明确放行 |
| `ImportError: SQLAlchemy asyncio requires greenlet` | 装的是裸 `sqlalchemy` | 依赖已声明为 `sqlalchemy[asyncio]`，重装即可 |

