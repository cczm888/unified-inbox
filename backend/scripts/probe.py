"""平台连通性自检工具。

拿到 app_id / secret 之后，用一条命令验证「真的通了」——不用等 Docker、不用起服务。
这一步的输出同时就是初赛要交的「API 连通性证据」。

用法（在 backend 目录下执行）：

    python scripts/probe.py feishu                    # 换 token + 机器人信息 + 可见会话
    python scripts/probe.py feishu --listen 60        # 长连接收消息：60 秒内你在飞书里发一句
    python scripts/probe.py feishu --send CHAT_ID "测试消息"
    python scripts/probe.py dingtalk --listen 60
    python scripts/probe.py telegram
    python scripts/probe.py all

每次运行会把输出同时写进 docs/证据/ 下，方便直接当截图证据提交。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent          # backend/
REPO = ROOT.parent                                     # unified-inbox/
EVIDENCE = REPO / "docs" / "证据"

FEISHU_BASE = "https://open.feishu.cn/open-apis"
DINGTALK_BASE = "https://api.dingtalk.com"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def load_env() -> Path | None:
    """按 backend/.env → 仓库根 .env → 当前目录 .env 的顺序找配置文件。"""
    for cand in (ROOT / ".env", REPO / ".env", Path.cwd() / ".env"):
        if not cand.exists():
            continue
        for line in cand.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        return cand
    return None


class Tee:
    """同时往屏幕和证据文件写，避免复制粘贴。"""

    def __init__(self, name: str) -> None:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = EVIDENCE / f"{name}-{stamp}.log"
        self.fh = self.path.open("w", encoding="utf-8")

    def __call__(self, msg: str = "") -> None:
        print(msg)
        self.fh.write(msg + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()


def mask(s: str) -> str:
    if not s:
        return "(空)"
    return s[:6] + "…" + s[-4:] if len(s) > 12 else s[:2] + "***"


# --------------------------------------------------------------------------- #
# 飞书
# --------------------------------------------------------------------------- #
def feishu_token(app_id: str, app_secret: str) -> str:
    r = httpx.post(
        f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=20,
    )
    data = r.json()
    if data.get("code") != 0:
        raise SystemExit(
            f"❌ 取 tenant_access_token 失败：code={data.get('code')} msg={data.get('msg')}\n"
            f"   排查：app_id/app_secret 是否抄错；应用是否已「创建版本并发布」；是否被停用。"
        )
    return data["tenant_access_token"]


def probe_feishu(send: str | None, chat: str | None, listen: int) -> bool:
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    say = Tee("feishu-probe")
    ok = True
    try:
        say("=" * 68)
        say(f"飞书连通性自检   {datetime.now():%Y-%m-%d %H:%M:%S}")
        say("=" * 68)
        say(f"APP_ID     = {mask(app_id)}")
        say(f"APP_SECRET = {mask(app_secret)}")
        if not app_id or not app_secret:
            say("❌ .env 里 FEISHU_APP_ID / FEISHU_APP_SECRET 还是空的")
            say("   去 open.feishu.cn → 开发者后台 → 你的应用 → 凭证与基础信息 里复制")
            return False

        say("\n[1/4] 换取 tenant_access_token …")
        token = feishu_token(app_id, app_secret)
        say(f"✅ 成功，token = {mask(token)}（有效期约 2 小时，代码里会自动缓存）")

        headers = {"Authorization": f"Bearer {token}"}

        say("\n[2/4] 读取机器人自身信息 …")
        r = httpx.get(f"{FEISHU_BASE}/bot/v3/info", headers=headers, timeout=20)
        d = r.json()
        if d.get("code") == 0:
            bot = d.get("bot", {})
            say(f"✅ 机器人已启用：{bot.get('app_name')}  open_id={bot.get('open_id')}")
        else:
            ok = False
            say(f"⚠️ 读不到机器人信息 code={d.get('code')} msg={d.get('msg')}")
            say("   多半是没开「机器人」能力：开发者后台 → 添加应用能力 → 机器人")

        say("\n[3/4] 列出机器人可见的会话（群）…")
        r = httpx.get(f"{FEISHU_BASE}/im/v1/chats", params={"page_size": 20}, headers=headers, timeout=20)
        d = r.json()
        if d.get("code") == 0:
            items = d.get("data", {}).get("items", [])
            if not items:
                say("⚠️ 机器人还没被拉进任何群（单聊不受影响，可以直接搜应用名私聊）")
            for it in items:
                say(f"   · chat_id={it.get('chat_id')}  name={it.get('name')}  mode={it.get('chat_mode')}")
        else:
            say(f"⚠️ 列会话失败 code={d.get('code')} msg={d.get('msg')}（可能缺 im:chat:readonly 权限，不影响收发）")

        if send and chat:
            say(f"\n[4/4] 向 chat_id={chat} 发送测试消息 …")
            r = httpx.post(
                f"{FEISHU_BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers=headers,
                json={
                    "receive_id": chat,
                    "msg_type": "text",
                    "content": json.dumps({"text": send}, ensure_ascii=False),
                },
                timeout=20,
            )
            d = r.json()
            if d.get("code") == 0:
                say(f"✅ 发送成功 message_id={d.get('data', {}).get('message_id')}，去飞书看看有没有收到")
            else:
                ok = False
                say(f"❌ 发送失败 code={d.get('code')} msg={d.get('msg')}")
                say("   排查：缺 im:message:send_as_bot 权限；chat_id 填的是群还是用户；应用未发布")
        else:
            say("\n[4/4] 跳过主动发消息（加 --send CHAT_ID '文本' 可测）")

        if listen > 0:
            say(f"\n[+] 长连接收消息测试：请现在打开飞书，搜索你的应用名，私聊发一句「测试」，等待 {listen} 秒 …")
            got = _feishu_listen(app_id, app_secret, listen, say)
            ok = ok and got
    finally:
        say(f"\n证据已保存：{say.path}")
        say.close()
    return ok


def _feishu_listen(app_id: str, app_secret: str, seconds: int, say) -> bool:
    import lark_oapi as lark

    received: list[dict] = []

    def on_message(data) -> None:
        try:
            ev = data.event
            msg = ev.message
            body = ""
            try:
                body = json.loads(msg.content).get("text", "")
            except Exception:
                body = str(msg.content)[:200]
            received.append(
                {
                    "event_type": data.header.event_type,
                    "chat_id": msg.chat_id,
                    "chat_type": msg.chat_type,
                    "msg_type": msg.message_type,
                    "sender": getattr(getattr(ev, "sender", None), "sender_id", None) and ev.sender.sender_id.open_id,
                    "text": body,
                }
            )
            say(f"✅ 收到事件：{json.dumps(received[-1], ensure_ascii=False)}")
        except Exception as exc:  # 回调里绝不能让异常冒出去
            say(f"⚠️ 回调解析异常：{exc!r}")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    # 探针：WS 收到任何 payload 都先打一行，用来区分「平台没推」和「推了但没派发到 handler」
    _orig_dispatch = handler._do_without_validation

    def _spy(payload):
        try:
            say(f"🛰️ 原始事件包：{str(payload)[:500]}")
        except Exception:
            pass
        return _orig_dispatch(payload)

    handler._do_without_validation = _spy
    client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    threading.Thread(target=client.start, name="feishu-ws", daemon=True).start()

    deadline = time.time() + seconds
    while time.time() < deadline:
        if received:
            say("✅ 长连接正常：事件订阅配置成功，后端能收到真实消息")
            return True
        time.sleep(1)

    say("⚠️ 超时没收到消息。逐项确认：")
    say("   1) 开发者后台 → 事件与回调 → 订阅方式是否选了「长连接」")
    say("   2) 是否添加了事件 im.message.receive_v1")
    say("   3) 是否加了权限 im:message 并「创建版本 → 申请发布」通过")
    say("   4) 发消息的账号和这个应用是不是同一个企业")
    return False


# --------------------------------------------------------------------------- #
# 钉钉
# --------------------------------------------------------------------------- #
def probe_dingtalk(listen: int) -> bool:
    cid = os.environ.get("DINGTALK_CLIENT_ID", "")
    csec = os.environ.get("DINGTALK_CLIENT_SECRET", "")
    robot = os.environ.get("DINGTALK_ROBOT_CODE", "")
    say = Tee("dingtalk-probe")
    ok = True
    try:
        say("=" * 68)
        say(f"钉钉连通性自检   {datetime.now():%Y-%m-%d %H:%M:%S}")
        say("=" * 68)
        say(f"CLIENT_ID     = {mask(cid)}")
        say(f"CLIENT_SECRET = {mask(csec)}")
        say(f"ROBOT_CODE    = {mask(robot)}（仅主动群发时需要）")
        if not cid or not csec:
            say("❌ .env 里 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET 还是空的")
            say("   去 open-dev.dingtalk.com → 应用管理 → 你的应用 → 凭证与基础信息")
            return False

        say("\n[1/3] 换取 accessToken …")
        r = httpx.post(
            f"{DINGTALK_BASE}/v1.0/oauth2/accessToken",
            json={"appKey": cid, "appSecret": csec},
            timeout=20,
        )
        d = r.json()
        if "accessToken" not in d:
            say(f"❌ 失败：{json.dumps(d, ensure_ascii=False)}")
            say("   排查：AppKey/AppSecret 是否抄错；应用是否被停用。")
            return False
        say(f"✅ 成功，token = {mask(d['accessToken'])}（expireIn={d.get('expireIn')}s）")

        say("\n[2/3] 检查机器人配置 …")
        if robot:
            say(f"✅ robotCode 已填：{mask(robot)}")
        else:
            say("⚠️ DINGTALK_ROBOT_CODE 为空：只用 sessionWebhook 回消息可以，主动群发不行")

        say("\n[3/3] Stream 模式连接测试 …")
        seconds = listen if listen > 0 else 15
        say(f"   等待 {seconds} 秒，期间在钉钉里给机器人发一条消息 …")
        got = _dingtalk_listen(cid, csec, seconds, say)
        ok = ok and got
    finally:
        say(f"\n证据已保存：{say.path}")
        say.close()
    return ok


def _dingtalk_listen(cid: str, csec: str, seconds: int, say) -> bool:
    from dingtalk_stream import AckMessage, ChatbotHandler, ChatbotMessage, Credential, DingTalkStreamClient

    received: list[dict] = []

    class Handler(ChatbotHandler):
        def process(self, callback):  # noqa: ANN001
            data = dict(callback.data or {})
            received.append(data)
            say(f"✅ 收到钉钉消息：{json.dumps(data, ensure_ascii=False)[:600]}")
            return AckMessage.STATUS_OK, "OK"

    client = DingTalkStreamClient(Credential(cid, csec))
    client.register_callback_handler(ChatbotMessage.TOPIC, Handler())
    starter = getattr(client, "start_forever", None) or client.start
    threading.Thread(target=starter, name="dingtalk-stream", daemon=True).start()

    deadline = time.time() + seconds
    while time.time() < deadline:
        if received:
            say("✅ Stream 长连接正常：事件能推到本机，不需要公网回调")
            return True
        time.sleep(1)

    say("⚠️ 超时没收到消息（但连接若没报错，通常只是没发消息）。逐项确认：")
    say("   1) 应用 → 机器人 → 消息接收模式是否选了「Stream 模式」")
    say("   2) 应用是否已发布（钉钉要求发布后才生效）")
    say("   3) 是否把机器人加进了群，或在单聊里能找到它")
    return False


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def probe_telegram() -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    say = Tee("telegram-probe")
    ok = True
    try:
        say("=" * 68)
        say(f"Telegram 连通性自检   {datetime.now():%Y-%m-%d %H:%M:%S}")
        say("=" * 68)
        say(f"BOT_TOKEN = {mask(token)}")
        if not token:
            say("❌ .env 里 TELEGRAM_BOT_TOKEN 是空的（找 @BotFather /newbot 拿）")
            return False

        say("\n[1/2] getMe …")
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        d = r.json()
        if not d.get("ok"):
            say(f"❌ 失败：{json.dumps(d, ensure_ascii=False)}")
            return False
        me = d["result"]
        say(f"✅ 机器人 @{me.get('username')}（id={me.get('id')}）")

        say("\n[2/2] getUpdates 探测待处理消息 …")
        r = httpx.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"timeout": 0, "limit": 5},
            timeout=20,
        )
        d = r.json()
        ups = d.get("result", []) if d.get("ok") else []
        say(f"✅ 当前待处理 update 数：{len(ups)}")
        for u in ups[:3]:
            msg = u.get("message", {})
            say(f"   · from={msg.get('from', {}).get('username')} text={(msg.get('text') or '')[:60]}")
        if not ups:
            say("   （空的正常：说明已消费完。去 Telegram 给机器人发句话再看）")
    finally:
        say(f"\n证据已保存：{say.path}")
        say.close()
    return ok


# --------------------------------------------------------------------------- #
def main() -> int:
    # Windows 默认代码页是 GBK，直接 print emoji 会 UnicodeEncodeError，这里强制 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="平台连通性自检")
    ap.add_argument("platform", choices=["feishu", "dingtalk", "telegram", "all"])
    ap.add_argument("--listen", type=int, default=0, help="长连接等待秒数，收到消息即通过")
    ap.add_argument("--send", nargs=2, metavar=("CHAT_ID", "TEXT"), help="主动发一条测试消息")
    args = ap.parse_args()

    env = load_env()
    print(f"配置文件：{env or '未找到 .env（将从系统环境变量读取）'}\n")

    results: dict[str, bool] = {}
    if args.platform in ("feishu", "all"):
        chat, text = (args.send if args.send else (None, None))
        results["飞书"] = probe_feishu(text, chat, args.listen)
        print()
    if args.platform in ("dingtalk", "all"):
        results["钉钉"] = probe_dingtalk(args.listen)
        print()
    if args.platform in ("telegram", "all"):
        results["Telegram"] = probe_telegram()
        print()

    print("=" * 68)
    for name, ok in results.items():
        print(f"{'✅ 通过' if ok else '❌ 未通过'}  {name}")
    print("=" * 68)
    sys.stdout.flush()
    os._exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
