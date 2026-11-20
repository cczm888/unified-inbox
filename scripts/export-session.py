#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 DSH 的会话日志（session.v4.jsonl.zstd）导出成可读的 Markdown。

为什么需要它：比赛要求「使用 AI 必须附对话过程链接」。DSH 的原始日志是
zstd 压缩的 JSONL，一条事件一行、混着上千条工具调用，评审不可能去读。
所以要转成人能顺着看的时间线。

产出两份：
  1) session-XXXXXXXX-dialogue.md   人说的话 + AI 说的话，工具调用压成一行
  2) session-XXXXXXXX-full.md       再加 AI 的思考过程和工具入参/结果摘要

过滤：框架注入的运行时上下文、技能目录、记忆提示、压缩摘要都不算「人说的话」，
一律跳过——否则导出来的东西一半是系统噪声，评审翻两页就关了。

用法：
  python scripts/export-session.py [session-id]
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import zstandard as zstd
except ImportError:
    sys.exit("缺少依赖：pip install zstandard")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "ai-session-export"
SESSIONS = pathlib.Path.home() / ".dsh" / "sessions"

# 这些前缀出现即判定为框架注入，不是人敲的字
NOISE_PREFIXES = (
    "Current runtime context.",
    "<system-reminder>",
    "<hindsight_knowledge",
    "<available_skills>",
    "Time sampled while preparing",
    "[Compressed conversation section]",
    "This is a reminder that",
    "<acp tokens",
    "background job pwsh-",
)


def find_session(session_id: str | None) -> pathlib.Path:
    if session_id:
        hits = list(SESSIONS.rglob(f"session-{session_id}/session.v4.jsonl.zstd"))
        hits += list(SESSIONS.rglob(f"{session_id}/session.v4.jsonl.zstd"))
        if not hits:
            sys.exit(f"找不到会话 {session_id}")
        return hits[0]
    files = sorted(SESSIONS.rglob("session.v4.jsonl.zstd"), key=lambda p: p.stat().st_mtime)
    if not files:
        sys.exit("找不到任何会话日志")
    return files[-1]


def load(path: pathlib.Path) -> list[dict]:
    with path.open("rb") as f:
        raw = zstd.ZstdDecompressor().stream_reader(f).read()
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def ts(ms) -> str:
    if not isinstance(ms, (int, float)) or not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M:%S")


def is_noise(text: str) -> bool:
    head = text.lstrip()
    return any(head.startswith(p) for p in NOISE_PREFIXES)


def parts_of(message: dict) -> list[dict]:
    """拿到 content 数组（user 与 assistant 结构不同，统一一下）。"""
    if not isinstance(message, dict):
        return []
    c = message.get("content")
    return c if isinstance(c, list) else []


def render_user(message: dict) -> str:
    out = []
    for c in parts_of(message):
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == "text":
            out.append(c.get("text") or "")
        elif t == "file":
            att = c.get("attachment") or {}
            size = att.get("bytes")
            out.append(f"[附件: {att.get('name', '附件')}{f' · {size} B' if size else ''}]")
        elif t == "image":
            out.append("[图片]")
    return "\n".join(x for x in out if x).strip()


def tool_line(name: str, args) -> str:
    if not isinstance(args, dict):
        try:
            args = json.loads(args) if isinstance(args, str) else {}
        except Exception:
            args = {}
    for key in ("description", "command", "file_path", "path", "query", "prompt", "url"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            one = " ".join(v.split())
            if len(one) > 150:
                one = one[:150] + " …"
            return f"`{name}` — {one}"
    return f"`{name}`"


def build(records: list[dict], *, full: bool) -> str:
    meta = next((r for r in records if r.get("type") == "session"), {})
    sid = meta.get("id", "unknown")

    L: list[str] = []
    L.append(f"# 会话记录：{sid}")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 会话 ID | `{sid}` |")
    L.append(f"| 开始时间 | {ts(meta.get('createdAt'))} |")
    L.append(f"| 工作目录 | `{meta.get('cwd', '')}` |")
    L.append(f"| 原始事件数 | {len(records)} |")
    L.append(f"| 导出形态 | {'完整（含思考与工具细节）' if full else '对话（工具压成一行）'} |")
    L.append("")
    L.append("> 原始日志为 zstd 压缩的 JSONL，一条事件一行。本文件按时间顺序重建人机往返；")
    L.append("> 框架注入的运行时上下文、技能目录等已过滤，工具调用压成一行摘要。")
    L.append("")

    turn = 0
    for rec in records:
        t = rec.get("type")
        d = rec.get("data") or {}
        when = ts(rec.get("time"))

        if t == "turn/start":
            turn = d.get("turn", turn + 1)
            L.append("")
            L.append(f"## 第 {turn} 轮")
            L.append("")
            continue

        if t == "user/message":
            text = render_user(d)
            if not text or is_noise(text):
                continue
            L.append(f"**人** · {when}")
            L.append("")
            L.extend(text.split("\n"))
            L.append("")
            continue

        if t != "assistant/message":
            continue

        msg = d.get("message") or {}
        for c in parts_of(msg):
            if not isinstance(c, dict):
                continue
            kind = c.get("type")
            if kind == "text":
                body = (c.get("text") or "").strip()
                if not body:
                    continue
                L.append(f"**AI** · {when}")
                L.append("")
                L.extend(body.split("\n"))
                L.append("")
            elif kind == "tool-call":
                L.append(f"- {tool_line(c.get('name') or 'tool', c.get('arguments'))}")
            elif kind == "reasoning" and full:
                body = (c.get("text") or "").strip()
                if not body:
                    continue
                if len(body) > 1200:
                    body = body[:1200] + " …"
                L.append("")
                L.append("<details><summary>思考过程</summary>")
                L.append("")
                L.extend(body.split("\n"))
                L.append("")
                L.append("</details>")
                L.append("")

    L.append("")
    L.append("---")
    L.append("")
    L.append(f"由 `scripts/export-session.py` 从 `{sid}/session.v4.jsonl.zstd` 生成。")
    return "\n".join(L)


def main() -> None:
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    path = find_session(sid)
    print(f"读取 {path}")
    records = load(path)
    print(f"解析 {len(records)} 条事件")

    real_id = next((r.get("id") for r in records if r.get("type") == "session"), "session")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    short = str(real_id).replace("session-", "")[:8]

    for suffix, full in (("dialogue", False), ("full", True)):
        body = build(records, full=full)
        dest = OUT_DIR / f"session-{short}-{suffix}.md"
        dest.write_text(body, encoding="utf-8")
        print(f"写出 {dest.name}  {len(body):,} 字符 / {body.count(chr(10)) + 1} 行")


if __name__ == "__main__":
    main()
