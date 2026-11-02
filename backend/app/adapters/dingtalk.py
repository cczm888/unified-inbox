"""钉钉适配器（第二平台）。

选它的理由：Stream 模式（WebSocket 长连接）接收机器人消息，**同样不需要公网回调**，
和飞书形成"两个主流办公平台"的对照，直接支撑架构抽象的说服力。

开放平台准备：
  1. 用组织（学校社团/个体户/个人团队均可）登录 open.dingtalk.com
  2. 企业内部应用 → 添加机器人 → 记下 Client ID(AppKey) / Client Secret(AppSecret) / RobotCode
  3. 权限：机器人消息接收（Stream 模式）、Robot 发消息
  4. 事件订阅选「Stream 模式推送」，无需填写回调 URL

出站两种方式（代码里都实现了）：
  A. sessionWebhook —— 收到消息时钉钉会下发一个 1.5 小时有效的回发地址，最省事最稳，优先用；
  B. 开放 API robot/groupMessages/send —— 需要 robotCode + openConversationId。
"""
from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from .base import BaseAdapter, InboundCallback, SendResult, UnifiedMessage, ms_to_dt

log = logging.getLogger(__name__)

DING_BASE = "https://api.dingtalk.com"


class DingTalkAdapter(BaseAdapter):
    platform = "dingtalk"
    display_name = "钉钉"
    rpm = 20  # 机器人单聊限流约 20 条/分钟，令牌桶按这个配

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        robot_code: str,
        on_inbound: InboundCallback,
    ) -> None:
        super().__init__(on_inbound)
        self.client_id = client_id
        self.client_secret = client_secret
        self.robot_code = robot_code or ""
        self._http = httpx.AsyncClient(timeout=20.0)
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._client = None

    # ---------------- 入站：Stream ----------------
    async def _start(self) -> None:
        from dingtalk_stream import AckMessage, ChatbotHandler, ChatbotMessage, Credential, DingTalkStreamClient

        outer = self

        class _Handler(ChatbotHandler):
            async def process(self, callback):  # type: ignore[override]
                try:
                    outer._emit(dict(callback.data))
                except Exception:
                    log.exception("dingtalk emit failed")
                # 必须尽快 ACK，否则钉钉会重推
                return AckMessage.STATUS_OK, "OK"

        client = DingTalkStreamClient(Credential(self.client_id, self.client_secret))
        client.register_callback_handler(ChatbotMessage.TOPIC, _Handler())
        self._client = client

        def _run() -> None:
            try:
                # 不同版本方法名有差异，做兼容
                runner = getattr(client, "start_forever", None) or client.start
                runner()
            except Exception:
                log.exception("dingtalk stream exited")

        threading.Thread(target=_run, name="dingtalk-stream", daemon=True).start()

    async def _stop(self) -> None:
        await self._http.aclose()

    # ---------------- 入站归一化 ----------------
    def to_unified(self, raw: dict) -> UnifiedMessage | None:
        msg_id = raw.get("msgId") or raw.get("messageId")
        if not msg_id:
            return None
        # 机器人自己发的消息不回灌，避免死循环
        if raw.get("chatbotUserId") and raw.get("senderStaffId") == raw.get("chatbotUserId"):
            return None

        msgtype = raw.get("msgtype") or "text"
        text = ""
        attachments: list[dict] = []
        if msgtype == "text":
            text = ((raw.get("text") or {}).get("content") or "").strip()
        elif msgtype in ("picture", "image"):
            attachments.append({"name": "image", "download_code": (raw.get("content") or {}).get("downloadCode")})
            text = "[图片]"
        elif msgtype == "file":
            content = raw.get("content") or {}
            attachments.append(
                {
                    "name": content.get("fileName"),
                    "download_code": content.get("downloadCode"),
                }
            )
            text = f"[文件] {content.get('fileName')}"
        elif msgtype == "richText":
            text = json.dumps(raw.get("content") or {}, ensure_ascii=False)[:1000]
        else:
            text = f"[{msgtype}]"

        conv_type = str(raw.get("conversationType") or "1")  # 1=单聊 2=群聊
        return UnifiedMessage(
            platform=self.platform,
            platform_msg_id=str(msg_id),
            chat_id=str(raw.get("conversationId") or ""),
            chat_type="p2p" if conv_type == "1" else "group",
            chat_title=raw.get("conversationTitle"),
            sender_id=raw.get("senderStaffId") or raw.get("senderId"),
            sender_name=raw.get("senderNick"),
            msg_type="file" if msgtype in ("file", "picture", "image") else "text",
            text=text,
            attachments=attachments,
            thread_root=None,
            created_at=ms_to_dt(raw.get("createAt")),
            raw=raw,
        )

    # ---------------- 出站 ----------------
    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        resp = await self._http.post(
            f"{DING_BASE}/v1.0/oauth2/accessToken",
            json={"appKey": self.client_id, "appSecret": self.client_secret},
        )
        data = resp.json()
        if "accessToken" not in data:
            raise RuntimeError(f"钉钉 token 获取失败: {data}")
        self._token = data["accessToken"]
        self._token_exp = time.time() + int(data.get("expireIn", 7200))
        return self._token

    async def send_text(self, chat_id: str, text: str, meta: dict | None = None) -> SendResult:
        meta = meta or {}
        webhook = meta.get("session_webhook")
        try:
            if webhook:  # 方案 A：会话内回发，1.5 小时内有效
                resp = await self._http.post(
                    webhook,
                    json={"msgtype": "text", "text": {"content": text}},
                )
                data = resp.json()
                if data.get("errcode") in (0, None):
                    return SendResult(True, data.get("processQueryKey"))
                return SendResult(False, error=f"webhook errcode={data.get('errcode')} {data.get('errmsg')}")

            # 方案 B：开放 API（需要 robotCode + openConversationId）
            if not self.robot_code:
                return SendResult(False, error="缺 robotCode 且无 session_webhook，无法发送")
            token = await self._access_token()
            resp = await self._http.post(
                f"{DING_BASE}/v1.0/robot/groupMessages/send",
                headers={"x-acs-dingtalk-access-token": token},
                json={
                    "robotCode": self.robot_code,
                    "openConversationId": chat_id,
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                },
            )
            data = resp.json()
            if "processQueryKey" in data:
                return SendResult(True, data["processQueryKey"])
            return SendResult(False, error=str(data))
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")

    async def send_file(self, chat_id: str, path: str, meta: dict | None = None) -> SendResult:
        # 钉钉发文件需先走 media/upload 换 media_id，演示阶段先用文字提示 + 后续扩展
        import os

        return await self.send_text(
            chat_id, f"[附件] {os.path.basename(path)}（钉钉侧文件转发待接入 media/upload）", meta
        )
