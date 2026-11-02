"""Telegram 适配器（兜底 + 三平台加分项）。

零审核：找 @BotFather 发 /newbot，30 秒拿到 token。
接收用 getUpdates 长轮询（不需要公网、不需要 webhook）。

用它有三个好处：
  1. 飞书/钉钉任何一方审核卡住，演示不会开天窗；
  2. 凑满"三个及以上平台 +3 分"；
  3. 长轮询实现简单，是教学/答辩里最好讲清楚的入站链路。
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from .base import AttachmentBlob, BaseAdapter, InboundCallback, SendResult, UnifiedMessage, ms_to_dt

log = logging.getLogger(__name__)


class TelegramAdapter(BaseAdapter):
    platform = "telegram"
    display_name = "Telegram"
    rpm = 30

    def __init__(self, token: str, on_inbound: InboundCallback) -> None:
        super().__init__(on_inbound)
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self._http = httpx.AsyncClient(timeout=40.0)
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._offset: int | None = None

    # ---------------- 入站：长轮询 ----------------
    async def _start(self) -> None:
        # 先探活，token 错了要立刻暴露，而不是安静地转圈
        resp = await self._http.get(f"{self.base}/getMe", timeout=10)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getMe 失败: {data}")
        self.username = (data.get("result") or {}).get("username")
        self._task = asyncio.create_task(self._poll_loop(), name="telegram-poll")

    async def _stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
        await self._http.aclose()

    async def _poll_loop(self) -> None:
        backoff = 1
        while not self._stopping:
            try:
                params: dict = {"timeout": 25, "allowed_updates": '["message"]'}
                if self._offset is not None:
                    params["offset"] = self._offset
                resp = await self._http.get(f"{self.base}/getUpdates", params=params)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(str(data))
                for update in data.get("result") or []:
                    self._offset = int(update["update_id"]) + 1
                    msg = update.get("message") or update.get("edited_message")
                    if msg:
                        self._emit(msg)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("telegram poll error: %s (retry in %ss)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    # ---------------- 入站归一化 ----------------
    def to_unified(self, raw: dict) -> UnifiedMessage | None:
        if "message_id" not in raw:
            return None
        chat = raw.get("chat") or {}
        frm = raw.get("from") or {}
        text = raw.get("text") or raw.get("caption") or ""
        attachments: list[dict] = []

        if raw.get("document"):
            doc = raw["document"]
            attachments.append(
                {
                    "name": doc.get("file_name"),
                    "file_id": doc.get("file_id"),
                    "mime": doc.get("mime_type"),
                    "size": doc.get("file_size"),
                    "kind": "file",
                }
            )
            text = text or f"[文件] {doc.get('file_name')}"
        elif raw.get("photo"):
            photo = raw["photo"][-1]
            attachments.append(
                {"name": "image", "file_id": photo.get("file_id"), "mime": "image/jpeg", "kind": "image"}
            )
            text = text or "[图片]"

        return UnifiedMessage(
            platform=self.platform,
            platform_msg_id=f"{chat.get('id')}:{raw['message_id']}",
            chat_id=str(chat.get("id")),
            chat_type="group" if chat.get("type") in ("group", "supergroup") else "p2p",
            chat_title=chat.get("title") or chat.get("username"),
            sender_id=str(frm.get("id")) if frm.get("id") else None,
            sender_name=" ".join(filter(None, [frm.get("first_name"), frm.get("last_name")]))
            or frm.get("username"),
            msg_type="file" if attachments else "text",
            text=text,
            attachments=attachments,
            created_at=ms_to_dt(int(raw.get("date", 0)) * 1000) if raw.get("date") else None,
            raw=raw,
        )

    # ---------------- 出站 ----------------
    async def send_text(self, chat_id: str, text: str, meta: dict | None = None) -> SendResult:
        try:
            resp = await self._http.post(
                f"{self.base}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            data = resp.json()
            if data.get("ok"):
                return SendResult(True, str(data["result"]["message_id"]))
            return SendResult(False, error=str(data.get("description")))
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")

    async def send_file(self, chat_id: str, path: str, meta: dict | None = None) -> SendResult:
        try:
            name = os.path.basename(path)
            with open(path, "rb") as fh:
                resp = await self._http.post(
                    f"{self.base}/sendDocument",
                    data={"chat_id": chat_id},
                    files={"document": (name, fh.read(), "application/octet-stream")},
                )
            data = resp.json()
            if data.get("ok"):
                return SendResult(True, str(data["result"]["message_id"]))
            return SendResult(False, error=str(data.get("description")))
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")

    # ---------------- 附件下载（网页里预览/下载用） ----------------
    async def download_attachment(self, platform_msg_id: str, att: dict) -> AttachmentBlob | None:
        """Telegram 取附件分两步：getFile 拿 file_path，再从 /file/bot<token>/<path> 下载。"""
        file_id = att.get("file_id")
        if not file_id:
            return None
        try:
            resp = await self._http.post(f"{self.base}/getFile", json={"file_id": file_id})
            data = resp.json()
            if not data.get("ok"):
                log.warning("telegram getFile 失败: %s", data.get("description"))
                return None
            file_path = data["result"]["file_path"]
            blob = await self._http.get(
                f"https://api.telegram.org/file/bot{self.token}/{file_path}",
            )
            if blob.status_code != 200:
                log.warning("telegram 下载附件失败 status=%s", blob.status_code)
                return None
            mime = att.get("mime") or ("image/jpeg" if (att.get("kind") == "image") else "application/octet-stream")
            name = att.get("name") or file_path.rsplit("/", 1)[-1]
            if name == "image" or not name:
                name = "图片.jpg" if att.get("kind") == "image" else "附件"
            return AttachmentBlob(data=blob.content, mime=mime, filename=name)
        except Exception:
            log.exception("telegram 下载附件异常 file_id=%s", file_id)
            return None
