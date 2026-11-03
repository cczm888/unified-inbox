"""邮箱适配器：IMAP 收信 + SMTP 发信。

为什么把邮箱算作"平台"：
  - 邮箱是办公场景里最原始的"待办来源"，任何系统（监控、审批、报名表、告警）
    的通知都会落在邮箱里；
  - IMAP / SMTP 是 IETF 标准协议，各大邮箱（QQ / 163 / Gmail / Outlook）都提供，
    不需要企业认证、不需要公网回调、不需要审核；
  - 收件箱里的邮件和飞书/Telegram 的消息在语义上是同一类东西（谁发我、要做什么、
    我回什么、处理到哪一步），所以完全可以塞进同一套统一消息模型。

实现要点：
  - 收：后台线程里跑同步 imaplib，只取 UNSEEN，处理完打 \\Seen（可用 EMAIL_MARK_SEEN=false 关闭）；
  - 发：SMTP_SSL（465）或 SMTP + STARTTLS（587），发件人就是配置的邮箱自己；
  - 附件：入库时只记元数据（文件名/大小/mime/IMAP part 编号），网页要下载时再按
    part 编号回邮箱取一次，避免把大文件塞进数据库。
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import make_msgid, parseaddr, parsedate_to_datetime

from .base import AttachmentBlob, BaseAdapter, InboundCallback, SendResult, UnifiedMessage

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")


def _dec(value: str | None) -> str:
    """MIME 头解码（=?utf-8?B?...?= -> 正常中文）。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def _iter_parts(msg: Message, prefix: str = ""):
    """按 IMAP 的 BODY[part] 编号遍历叶子节点（1, 2, 1.1 ...）。

    注意：单 part 的邮件（只有 text/plain 或只有 text/html）本身不是 multipart，
    但它在 IMAP 里的编号就是 1，所以这里要把它自己当作叶子节点返回，
    否则正文/附件都会漏掉。
    """
    if not msg.is_multipart():
        yield "1", msg
        return
    payload = msg.get_payload()
    if not isinstance(payload, list):
        return
    for idx, part in enumerate(payload, 1):
        num = f"{prefix}{idx}" if not prefix else f"{prefix}.{idx}"
        if part.is_multipart():
            yield from _iter_parts(part, num + ".")
        else:
            yield num, part


def _clean_body(body: str) -> str:
    """HTML 转出来的文本会留下一堆空格缩进和成片空行，直接塞进气泡没法看。

    邮件客户端多半还会在末尾附一大段退订/隐私声明，这里也不做智能剔除——
    宁可多留，也别把用户真正说的话误删。
    """
    if not body:
        return ""
    lines = [ln.rstrip() for ln in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            # 整行 strip：HTML 转文本留下的缩进（几十个空格）没有任何语义，
            # 留着只会让气泡里出现一片锯齿状的空洞。
            out.append(ln.strip())
        else:
            blank += 1
            if blank <= 1:  # 连续空行最多留一行
                out.append("")
    return "\n".join(out).strip()


def _parse_mail(raw_bytes: bytes, uid: str) -> dict:
    """把一封原始邮件解析成 dict（放 EventLog 里可追溯，也便于调试）。"""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    subject = _dec(msg.get("Subject"))
    from_name, from_addr = parseaddr(_dec(msg.get("From")))
    to_name, to_addr = parseaddr(_dec(msg.get("To")))

    try:
        sent_at = parsedate_to_datetime(msg.get("Date"))
        if sent_at and sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except Exception:
        sent_at = None

    body = ""
    attachments: list[dict] = []
    for num, part in _iter_parts(msg):
        ctype = part.get_content_type()
        filename = _dec(part.get_filename())
        disp = (part.get("Content-Disposition") or "").lower()
        if filename or "attachment" in disp:
            try:
                size = len(part.get_payload(decode=True) or b"")
            except Exception:
                size = 0
            attachments.append(
                {
                    "kind": "file",
                    "name": filename or f"part-{num}",
                    "mime": ctype,
                    "size": size,
                    "part": num,
                    "uid": uid,
                }
            )
            continue
        if not body and ctype == "text/plain":
            try:
                body = part.get_content()
            except Exception:
                body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        elif not body and ctype == "text/html":
            try:
                body = _html_to_text(part.get_content())
            except Exception:
                body = _html_to_text((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))

    return {
        "uid": uid,
        "message_id": (msg.get("Message-ID") or "").strip() or f"uid-{uid}",
        "subject": subject,
        "from_name": from_name,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "date": sent_at.isoformat() if sent_at else None,
        "body": body[:8000],
        "attachments": attachments,
        "in_reply_to": (msg.get("In-Reply-To") or "").strip() or None,
    }


class MailAdapter(BaseAdapter):
    platform = "email"
    display_name = "邮箱"
    supports_file = True
    rpm = 60

    def __init__(
        self,
        address: str,
        password: str,
        imap_host: str,
        smtp_host: str,
        on_inbound: InboundCallback,
        *,
        imap_port: int = 993,
        smtp_port: int = 465,
        poll_seconds: int = 30,
        mark_seen: bool = True,
        use_ssl_smtp: bool = True,
    ) -> None:
        super().__init__(on_inbound)
        self.address = address
        self.password = password
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.poll_seconds = poll_seconds
        self.mark_seen = mark_seen
        self.use_ssl_smtp = use_ssl_smtp
        self._task: asyncio.Task | None = None
        self._first = True
        self._seen_uids: set[str] = set()

    # ---------- 生命周期 ----------
    async def _start(self) -> None:
        # 启动时先探一次连接，配置错了立刻在 /platforms 上暴露出来，而不是静默失败
        try:
            await asyncio.to_thread(self._probe)
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(
                f"IMAP 登录失败：{exc}（请检查邮箱地址、授权码，以及是否已在邮箱设置里开启 IMAP 服务）"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"连不上 IMAP 服务器 {self.imap_host}:{self.imap_port}：{exc}（检查主机名/网络/端口）"
            ) from exc
        self._task = asyncio.create_task(self._poll_loop())

    async def _stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def _probe(self) -> None:
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as cli:
            cli.login(self.address, self.password)
            cli.select("INBOX", readonly=True)

    # ---------- 收 ----------
    async def _poll_loop(self) -> None:
        while True:
            try:
                rows = await asyncio.to_thread(self._fetch_unseen, 10 if self._first else 20)
                self._first = False
                for raw in rows:
                    if raw["message_id"] in self._seen_uids:
                        continue
                    self._seen_uids.add(raw["message_id"])
                    await self._on_inbound(self.platform, raw)
                if rows:
                    log.info("[email] 收到 %d 封新邮件", len(rows))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("[email] 拉取失败：%s", self.last_error)
            await asyncio.sleep(self.poll_seconds)

    def _fetch_unseen(self, limit: int) -> list[dict]:
        out: list[dict] = []
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as cli:
            cli.login(self.address, self.password)
            cli.select("INBOX")
            typ, data = cli.search(None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                return out
            uids = data[0].split()[-limit:]
            for uid in uids:
                u = uid.decode()
                typ, parts = cli.fetch(uid, "(RFC822)")
                if typ != "OK":
                    continue
                raw_bytes = None
                for item in parts:
                    if isinstance(item, tuple) and len(item) > 1:
                        raw_bytes = item[1]
                        break
                if not raw_bytes:
                    continue
                out.append(_parse_mail(raw_bytes, u))
                if self.mark_seen:
                    try:
                        cli.store(uid, "+FLAGS", "\\Seen")
                    except Exception:
                        pass
        return out

    def to_unified(self, raw: dict) -> UnifiedMessage | None:
        if not raw.get("from_addr"):
            return None
        sender = raw["from_addr"].lower()
        if sender == self.address.lower():
            return None  # 自己发的邮件，避免自回环
        created = None
        if raw.get("date"):
            try:
                created = datetime.fromisoformat(raw["date"])
            except Exception:
                created = None
        subject = raw.get("subject") or "(无主题)"
        body = _clean_body(raw.get("body") or "")
        text = body if body else subject
        return UnifiedMessage(
            platform=self.platform,
            platform_msg_id=raw["message_id"],
            chat_id=sender,
            chat_type="email",
            chat_title=f"{raw.get('from_name') or sender} <{sender}>",
            sender_id=sender,
            sender_name=raw.get("from_name") or sender,
            text=text,
            msg_type="file" if raw.get("attachments") else "text",
            attachments=raw.get("attachments") or [],
            thread_root=raw.get("in_reply_to"),
            created_at=created,
            raw={"subject": subject, **raw},
        )

    async def resolve_sender(self, m: UnifiedMessage) -> None:
        # 邮件头里本来就有显示名，不需要额外查询
        return None

    # ---------- 发 ----------
    async def send_text(self, chat_id: str, text: str, meta: dict | None = None) -> SendResult:
        meta = meta or {}
        subject = meta.get("subject") or self._auto_subject(text)
        return await asyncio.to_thread(self._send_sync, chat_id, subject, text, None, None, meta)

    async def send_file(
        self, chat_id: str, path: str, meta: dict | None = None, filename: str | None = None
    ) -> SendResult:
        meta = meta or {}
        subject = meta.get("subject") or f"[统一收件箱] 文件：{filename or path.split('/')[-1]}"
        return await asyncio.to_thread(
            self._send_sync, chat_id, subject, meta.get("text") or "", path, filename, meta
        )

    def _auto_subject(self, text: str) -> str:
        first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return f"[统一收件箱] {first[:60]}" if first else "[统一收件箱] 回复"

    def _send_sync(
        self,
        to_addr: str,
        subject: str,
        text: str,
        file_path: str | None,
        filename: str | None,
        meta: dict,
    ) -> SendResult:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg_id = make_msgid(domain=self.address.split("@")[-1] or "unified-inbox")
        msg["Message-ID"] = msg_id
        in_reply = meta.get("in_reply_to") or meta.get("thread_root")
        if in_reply:
            msg["In-Reply-To"] = in_reply
            msg["References"] = in_reply
        msg.set_content(text or "")

        if file_path:
            try:
                with open(file_path, "rb") as fh:
                    data = fh.read()
                name = filename or file_path.split("/")[-1].split("\\")[-1]
                maintype = "application"
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    maintype = "image"
                msg.add_attachment(data, maintype=maintype, subtype="octet-stream", filename=name)
            except Exception as exc:
                return SendResult(False, error=f"读取附件失败：{exc}")

        try:
            ctx = ssl.create_default_context()
            if self.use_ssl_smtp:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ctx, timeout=25) as cli:
                    cli.login(self.address, self.password)
                    cli.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=25) as cli:
                    cli.starttls(context=ctx)
                    cli.login(self.address, self.password)
                    cli.send_message(msg)
            return SendResult(True, platform_msg_id=msg_id)
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")

    # ---------- 附件下载 ----------
    async def download_attachment(self, platform_msg_id: str, att: dict) -> AttachmentBlob | None:
        uid = att.get("uid")
        part = att.get("part")
        if not uid or not part:
            return None
        return await asyncio.to_thread(self._download_sync, uid, part, att)

    def _download_sync(self, uid: str, part: str, att: dict) -> AttachmentBlob | None:
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as cli:
            cli.login(self.address, self.password)
            cli.select("INBOX", readonly=True)
            typ, data = cli.fetch(uid.encode(), f"(BODY[{part}])")
            if typ != "OK":
                return None
            payload = None
            for item in data:
                if isinstance(item, tuple) and len(item) > 1:
                    payload = item[1]
                    break
            if not payload:
                return None
            return AttachmentBlob(
                data=payload,
                mime=att.get("mime") or "application/octet-stream",
                filename=att.get("name") or "attachment",
            )
