"""飞书适配器（主平台）。

为什么选飞书：
  * 个人可注册，免费版即可创建企业、建自建应用，审核当天过；
  * 官方支持「长连接」接收事件（lark.ws.Client），**不需要公网回调地址、不需要域名备案**；
  * 消息 API 完整（文本/文件/图片/卡片），限流约 1000 次/分钟。

需要的应用权限（开放平台 → 权限管理）：
  im:message            读取用户发给机器人的单聊/群聊消息
  im:message:send_as_bot 以应用身份发消息
  im:resource            上传/下载图片与文件
事件订阅：im.message.receive_v1（长连接模式，无需填回调地址）
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import httpx

from .base import AttachmentBlob, BaseAdapter, InboundCallback, SendResult, UnifiedMessage, ms_to_dt

log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuAdapter(BaseAdapter):
    platform = "feishu"
    display_name = "飞书"
    rpm = 900

    def __init__(self, app_id: str, app_secret: str, on_inbound: InboundCallback) -> None:
        super().__init__(on_inbound)
        self.app_id = app_id
        self.app_secret = app_secret
        self._http = httpx.AsyncClient(timeout=20.0)
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._thread: threading.Thread | None = None
        self._cli = None
        # open_id -> 显示名 缓存（同一个人反复发言只查一次）
        self._name_cache: dict[str, str] = {}

    async def resolve_sender(self, m: UnifiedMessage) -> None:
        """把 open_id 解析成人名，让收件箱里显示"陈zm"而不是 ou_xxx。

        飞书的 im.message.receive_v1 事件里只有 sender.open_id，没有昵称，
        需要额外调 contact/v3/users/{open_id}（已开通 contact:user.base:readonly）。
        查询失败不影响入库，只是名字留空。
        """
        if not m.sender_id:
            return
        name = self._name_cache.get(m.sender_id)
        if name is None:
            try:
                token = await self._access_token()
                resp = await self._http.get(
                    f"{FEISHU_BASE}/contact/v3/users/{m.sender_id}",
                    params={"user_id_type": "open_id"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json().get("data") or {}
                name = ((data.get("user") or {}).get("name")) or ""
                self._name_cache[m.sender_id] = name
            except Exception:
                log.warning("feishu 查询发送者信息失败 open_id=%s", m.sender_id, exc_info=False)
                name = ""
        if not name:
            return
        m.sender_name = name
        # 单聊会话没有群名，用对方姓名当会话标题，左侧列表才可读
        if m.chat_type == "p2p" and (not m.chat_title or m.chat_title.startswith("oc_")):
            m.chat_title = f"{name}（飞书单聊）"

    # ---------------- 入站：长连接 ----------------
    async def _start(self) -> None:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        outer = self

        def _on_message(data: "P2ImMessageReceiveV1") -> None:
            try:
                raw = json.loads(lark.JSON.marshal(data))
            except Exception:
                log.exception("feishu marshal failed")
                return
            outer._emit(raw)

        handler = (
            lark.EventDispatcherHandler.builder("", "")  # 长连接模式不需要 encrypt_key / verification_token
            .register_p2_im_message_receive_v1(_on_message)
            .build()
        )
        # client.start() 是阻塞调用（内部 loop.run_until_complete），必须放到守护线程里。
        # 关键：线程内要先 new_event_loop + set_event_loop，否则 lark 会拿到主线程
        # 那个已经在跑的 uvicorn loop，抛 "This event loop is already running"。
        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # lark_oapi/ws/client.py 在**模块导入时**就把 asyncio.get_event_loop()
                # 的结果存成了模块级全局变量 loop，start() 里直接用这个全局。
                # 本项目 import 发生在 uvicorn 主 loop 已运行之后，所以那个全局指向
                # 主 loop，直接 start() 会报 "This event loop is already running"。
                # 解决：在守护线程里把该模块全局换成线程自己的 loop。
                import lark_oapi.ws.client as ws_mod

                ws_mod.loop = loop
                self._cli = lark.ws.Client(
                    self.app_id,
                    self.app_secret,
                    event_handler=handler,
                    log_level=lark.LogLevel.INFO,
                )
                self._cli.start()
            except Exception:
                log.exception("feishu ws client exited")
                self.last_error = "feishu ws client exited"

        self._thread = threading.Thread(target=_run, name="feishu-ws", daemon=True)
        self._thread.start()

    async def _stop(self) -> None:
        await self._http.aclose()

    # ---------------- 入站归一化 ----------------
    def to_unified(self, raw: dict) -> UnifiedMessage | None:
        header = raw.get("header") or {}
        if header.get("event_type") not in (None, "im.message.receive_v1"):
            return None
        event = raw.get("event") or {}
        msg = event.get("message") or {}
        if not msg.get("message_id"):
            return None
        sender = (event.get("sender") or {}).get("sender_id") or {}

        try:
            content = json.loads(msg.get("content") or "{}")
        except json.JSONDecodeError:
            content = {}

        mtype = msg.get("message_type") or "text"
        attachments: list[dict] = []
        text = ""
        if mtype == "text":
            text = (content.get("text") or "").strip()
        elif mtype == "file":
            attachments.append(
                {
                    "name": content.get("file_name"),
                    "file_key": content.get("file_key"),
                    "mime": content.get("file_type"),
                    "kind": "file",
                }
            )
            text = f"[文件] {content.get('file_name')}"
        elif mtype == "image":
            attachments.append(
                {"name": "image", "file_key": content.get("image_key"), "mime": "image/png", "kind": "image"}
            )
            text = "[图片]"
        else:
            text = f"[{mtype}]"

        return UnifiedMessage(
            platform=self.platform,
            platform_msg_id=msg["message_id"],
            chat_id=msg.get("chat_id") or "",
            chat_type=msg.get("chat_type"),
            sender_id=sender.get("open_id"),
            sender_name=None,
            msg_type="file" if mtype in ("file", "image") else "text",
            text=text,
            attachments=attachments,
            thread_root=msg.get("parent_id") or msg.get("root_id"),
            created_at=ms_to_dt(msg.get("create_time") or header.get("create_time")),
            raw=raw,
        )

    # ---------------- 出站 ----------------
    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        resp = await self._http.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 token 获取失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_exp = time.time() + int(data.get("expire", 7200))
        return self._token

    async def send_text(self, chat_id: str, text: str, meta: dict | None = None) -> SendResult:
        try:
            token = await self._access_token()
            resp = await self._http.post(
                f"{FEISHU_BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            data = resp.json()
            if data.get("code") == 0:
                return SendResult(True, data["data"]["message_id"])
            return SendResult(False, error=f"code={data.get('code')} msg={data.get('msg')}")
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")

    async def send_file(self, chat_id: str, path: str, meta: dict | None = None) -> SendResult:
        import os

        try:
            token = await self._access_token()
            name = os.path.basename(path)
            with open(path, "rb") as fh:
                blob = fh.read()
            up = await self._http.post(
                f"{FEISHU_BASE}/im/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                data={"file_type": "stream", "file_name": name},
                files={"file": (name, blob, "application/octet-stream")},
            )
            upd = up.json()
            if upd.get("code") != 0:
                return SendResult(False, error=f"upload failed: {upd}")
            file_key = upd["data"]["file_key"]
            resp = await self._http.post(
                f"{FEISHU_BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
                },
            )
            data = resp.json()
            if data.get("code") == 0:
                return SendResult(True, data["data"]["message_id"])
            return SendResult(False, error=f"code={data.get('code')} msg={data.get('msg')}")
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")

    # ---------------- 附件下载（网页里预览/下载用） ----------------
    async def download_attachment(self, platform_msg_id: str, att: dict) -> AttachmentBlob | None:
        """代理下载飞书消息里的图片/文件。

        飞书的资源必须用 tenant_access_token 才给，浏览器拿不到，
        所以后端做一层代理：浏览器只请求站内的 /attachments/{i}，
        平台凭证永远不出服务器。
        """
        key = att.get("file_key")
        if not key or not platform_msg_id:
            return None
        kind = att.get("kind") or ("image" if str(att.get("mime") or "").startswith("image/") else "file")
        try:
            token = await self._access_token()
            resp = await self._http.get(
                f"{FEISHU_BASE}/im/v1/messages/{platform_msg_id}/resources/{key}",
                params={"type": kind},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                log.warning(
                    "feishu 下载附件失败 msg=%s key=%s status=%s body=%s",
                    platform_msg_id,
                    key,
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            mime = resp.headers.get("content-type") or att.get("mime") or "application/octet-stream"
            name = att.get("name") or ""
            if kind == "image":
                ext = ".png" if "png" in mime else ".jpg"
                if not name or name == "image" or "." not in name:
                    name = "图片" + ext
            elif not name:
                name = "附件"
            return AttachmentBlob(data=resp.content, mime=mime, filename=name)
        except Exception:
            log.exception("feishu 下载附件异常 msg=%s key=%s", platform_msg_id, key)
            return None
