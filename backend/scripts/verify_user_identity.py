"""端到端验证：用户身份列会话 + 同步历史消息。"""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    async with httpx.AsyncClient(timeout=60.0, base_url=BASE) as c:
        acc = (await c.get("/api/v1/auth/accounts")).json()
        print("accounts:", json.dumps(acc, ensure_ascii=False)[:300])
        if not acc:
            print("!! 没有账号")
            return
        aid = acc[0]["id"]

        r = await c.get(f"/api/v1/auth/accounts/{aid}/chats")
        print("chats status:", r.status_code)
        chats = r.json() if r.status_code == 200 else []
        print("chats count:", len(chats) if isinstance(chats, list) else chats)
        if isinstance(chats, list):
            for ch in chats[:15]:
                print("  -", ch.get("name"), "|", ch.get("chat_mode"), "|", ch.get("member_count"))

        r = await c.post(f"/api/v1/auth/accounts/{aid}/sync", params={"chats": 20, "per_chat": 50})
        print("sync status:", r.status_code)
        print("sync body:", json.dumps(r.json(), ensure_ascii=False)[:600])

        r = await c.get("/api/v1/inbox/threads")
        threads = r.json()
        print("threads count:", len(threads))
        for t in threads[:12]:
            print("  *", t.get("platform"), "|", t.get("chat_title"), "| total", t.get("total"),
                  "|", (t.get("last_text") or "")[:40])


asyncio.run(main())
