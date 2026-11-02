"""用户身份还能做什么：多接口探测权限边界。"""
import asyncio
import json
import sys

import httpx

sys.path.insert(0, ".")
from app.db import SessionLocal  # noqa: E402
from app.services import accounts  # noqa: E402

BASE = "https://open.feishu.cn/open-apis"
CHAT_ID = "oc_f27dc86d678be49541a0a213e6c23280"


async def probe(cli, token, name, method, path, **kw):
    r = await cli.request(method, BASE + path, headers={"Authorization": f"Bearer {token}"}, **kw)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    code = body.get("code")
    msg = (body.get("msg") or "")[:120]
    ext = (body.get("error", {}) or {}).get("ext") or body.get("ext") or ""
    print(f"[{name}] http={r.status_code} code={code} msg={msg} ext={str(ext)[:90]}")


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    async with SessionLocal() as session:
        acc = (await accounts.list_accounts(session))[0]
        token = await accounts.get_valid_token(session, acc)
        async with httpx.AsyncClient(timeout=25) as cli:
            await probe(cli, token, "user_info", "GET", "/authen/v1/user_info")
            await probe(cli, token, "chats", "GET", "/im/v1/chats", params={"page_size": 5})
            await probe(cli, token, "messages(chat)", "GET", "/im/v1/messages",
                        params={"container_id_type": "chat", "container_id": CHAT_ID, "page_size": 5})
            await probe(cli, token, "chat_detail", "GET", f"/im/v1/chats/{CHAT_ID}")
            await probe(cli, token, "legacy_chat_messages", "GET", f"/im/v1/chats/{CHAT_ID}/messages",
                        params={"page_size": 5})
            await probe(cli, token, "contact_me", "GET", "/contact/v3/users/me")


asyncio.run(main())
