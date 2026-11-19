"""访问控制：账号注册 + 登录 + 一个签名 cookie。

三种模式（按优先级）
--------------------
1. **有账号**：所有 /api 请求都要带有效 cookie；没账号时登录页变成「创建账号」页。
2. **无账号 + ACCESS_PASSWORD 非空**：退回到单一口令模式（兼容旧部署，也是应急后门）。
3. **无账号 + ACCESS_PASSWORD 为空**：不校验，只适合纯本机自己看。

为什么要有账号体系
------------------
赛题要求闭环包含「用户注册」。原实现只有 .env 里一个共享口令，没有"注册"这一步，
也没有"谁在用"的概念。这里补上，但刻意保持**单用户**：数据表没有 owner 维，
真做多租户要动每一张表，而赛题要的是这个环节存在且可用。

口令存储
--------
PBKDF2-HMAC-SHA256，200000 轮 + 16 字节随机盐，格式
`pbkdf2_sha256$200000$<salt_hex>$<hash_hex>`。
校验用 hmac.compare_digest 做定长比较。

cookie 里不存任何口令，存 sha256(身份 | SECRET_KEY) 的前 40 位：
拿到 cookie 反推不出口令，改了 SECRET_KEY 所有会话自动失效。
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os

from .config import settings

COOKIE_NAME = "uti_auth"

_PBKDF2_ROUNDS = 200_000
_PBKDF2_PREFIX = "pbkdf2_sha256"

# 系统里有没有账号：启动时由 main.py 加载，注册成功后即时更新。
# 放在模块级是为了让中间件不必每个请求都查库。
_has_user = False


def set_has_user(value: bool) -> None:
    global _has_user
    _has_user = bool(value)


def has_user() -> bool:
    return _has_user


# ---------------- 口令哈希 ----------------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not password or not stored:
        return False
    try:
        prefix, rounds_s, salt_hex, hash_hex = stored.split("$")
        if prefix != _PBKDF2_PREFIX:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds_s)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    """返回人类可读的拒绝理由；通过则返回 None。"""
    if not password:
        return "口令不能为空。"
    if len(password) < 6:
        return "口令至少 6 位。"
    if len(password) > 128:
        return "口令太长（上限 128 位）。"
    return None


def username_problem(username: str) -> str | None:
    if not username:
        return "用户名不能为空。"
    if not (3 <= len(username) <= 32):
        return "用户名需要 3–32 个字符。"
    if any(c.isspace() for c in username):
        return "用户名不能包含空格。"
    return None


# ---------------- cookie ----------------


def _digest(identity: str) -> str:
    raw = f"{identity}|{settings.secret_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def make_token(username: str = "") -> str:
    """带用户名时按账号签名，否则退回到共享口令的签名。"""
    return _digest(f"user:{username}" if username else "password")


def token_username(cookie_value: str | None, known_usernames: list[str]) -> str | None:
    """从 cookie 反查是哪个账号（逐个比对，账号数量极少）。"""
    if not cookie_value:
        return None
    for name in known_usernames:
        if hmac.compare_digest(cookie_value, make_token(name)):
            return name
    return None


def check_password(pw: str) -> bool:
    """共享口令校验（兼容旧部署 / 应急入口）。"""
    if not settings.access_password or not pw:
        return False
    return hmac.compare_digest(pw, settings.access_password)


def is_password_token(cookie_value: str | None) -> bool:
    if not settings.access_password or not cookie_value:
        return False
    return hmac.compare_digest(cookie_value, make_token(""))


def is_authenticated(cookie_value: str | None, known_usernames: list[str] | None = None) -> bool:
    """中间件用：这个请求算不算已登录。

    * 有账号：必须匹配某个账号的签名；
    * 没账号：ACCESS_PASSWORD 为空即放行，否则认共享口令签名。
    """
    if _has_user:
        if is_password_token(cookie_value):
            return True  # 应急口令始终有效，避免把自己锁在外面
        return token_username(cookie_value, known_usernames or []) is not None
    if not settings.auth_required:
        return True
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value, _digest("password"))


# ---------------- 页面 ----------------


_PAGE_CSS = """
  :root { --brand:#2f6bff; --line:#e9ecf0; --t1:#1a1d21; --t2:#5a6474; --t3:#98a1b0; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:#f6f7f9; color:var(--t1);
         font-family:-apple-system,"Segoe UI","Microsoft YaHei UI","PingFang SC",sans-serif; }
  .card { width:360px; background:#fff; border:1px solid var(--line); border-radius:14px;
          padding:26px 24px 22px; box-shadow:0 6px 24px rgba(20,28,45,.06); }
  .logo { width:40px; height:40px; border-radius:11px; display:grid; place-items:center;
          background:linear-gradient(135deg,#3a6eff,#563ceb); color:#fff;
          font-weight:700; font-size:19px; margin-bottom:14px; }
  h1 { font-size:16px; margin:0 0 6px; }
  p  { font-size:12.5px; color:var(--t2); margin:0 0 18px; line-height:1.6; }
  label { display:block; font-size:12px; color:var(--t2); margin:12px 0 6px; }
  input { width:100%; height:38px; padding:0 11px; font-size:14px;
          border:1px solid var(--line); border-radius:9px; outline:none; }
  input:focus { border-color:var(--brand); box-shadow:0 0 0 3px rgba(47,107,255,.12); }
  button { width:100%; height:38px; margin-top:18px; border:0; border-radius:9px;
           background:var(--brand); color:#fff; font-size:14px; font-weight:600; cursor:pointer; }
  button:hover { background:#1f5aec; }
  .err { margin-top:12px; font-size:12.5px; color:#e5484d; }
  .foot { margin-top:16px; font-size:11.5px; color:var(--t3); line-height:1.7; }
  .alt { margin-top:14px; font-size:12px; color:var(--t2); text-align:center; }
  .alt a { color:var(--brand); text-decoration:none; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def register_page(error: str = "", username: str = "") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    body = f"""
  <form class="card" method="post" action="/api/v1/auth/register">
    <div class="logo">收</div>
    <h1>创建账号</h1>
    <p>这是第一次启动，先给你自己建一个账号。这个收件箱目前是单用户模式——
       第一个注册的人就是它的主人，之后的人请用这个账号登录。</p>
    <label for="u">用户名</label>
    <input id="u" name="username" type="text" autocomplete="username" autofocus
           value="{html.escape(username)}" placeholder="3–32 个字符，不含空格">
    <label for="p">口令</label>
    <input id="p" name="password" type="password" autocomplete="new-password" placeholder="至少 6 位">
    <label for="p2">再输一次</label>
    <input id="p2" name="password2" type="password" autocomplete="new-password" placeholder="确认口令">
    <button type="submit">创建并进入</button>
    {err}
    <div class="foot">口令用 PBKDF2 存哈希，库里看不到明文。忘记口令可以走
      <code>ACCESS_PASSWORD</code> 应急入口进来后改。</div>
  </form>"""
    return _page("统一收件箱 · 创建账号", body)


def login_page(error: str = "", username: str = "") -> str:
    if not _has_user:
        # 没有账号时不该出现登录页，交给 register_page
        return register_page(error, username)
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    body = f"""
  <form class="card" method="post" action="/api/v1/auth/login">
    <div class="logo">收</div>
    <h1>统一收件箱</h1>
    <p>这里汇聚了飞书、邮箱等平台的原始消息，需要登录才能查看。</p>
    <label for="u">用户名</label>
    <input id="u" name="username" type="text" autocomplete="username" autofocus
           value="{html.escape(username)}">
    <label for="p">口令</label>
    <input id="p" name="password" type="password" autocomplete="current-password">
    <button type="submit">进入</button>
    {err}
    <div class="foot">登录状态保留 30 天。口令不对就进不来。</div>
  </form>"""
    return _page("统一收件箱 · 登录", body)
