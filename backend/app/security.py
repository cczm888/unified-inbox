"""访问控制：一个访问口令 + 一个签名 cookie。

为什么需要它
------------
这个系统里装的是「一个人所有平台的消息」：飞书单聊、邮箱正文、附件原文。
一旦监听 0.0.0.0 或者开了公网隧道，任何拿到地址的人都能看到全部内容，
而且还能以你的身份往外发消息 —— 这比只读泄露更严重。

所以只要 `ACCESS_PASSWORD` 非空，所有 /api 请求都必须带有效 cookie；
留空时不校验，只适合纯本机自己看。

cookie 里不存口令本身，存的是 sha256(口令 | SECRET_KEY) 的前 40 位：
拿到 cookie 也反推不出口令，改了 SECRET_KEY 所有会话自动失效。
"""
from __future__ import annotations

import hashlib
import hmac
import html

from .config import settings

COOKIE_NAME = "uti_auth"


def _digest() -> str:
    raw = f"{settings.access_password}|{settings.secret_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def make_token() -> str:
    return _digest()


def check_password(pw: str) -> bool:
    """定长比较，避免按字节提前返回泄露口令长度。"""
    if not settings.access_password or not pw:
        return False
    return hmac.compare_digest(pw, settings.access_password)


def is_authenticated(cookie_value: str | None) -> bool:
    if not settings.auth_required:
        return True
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value, _digest())


_LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>统一收件箱 · 需要访问口令</title>
<style>
  :root {{ --brand:#2f6bff; --line:#e9ecf0; --t1:#1a1d21; --t2:#5a6474; --t3:#98a1b0; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         background:#f6f7f9; color:var(--t1);
         font-family:-apple-system,"Segoe UI","Microsoft YaHei UI","PingFang SC",sans-serif; }}
  .card {{ width:340px; background:#fff; border:1px solid var(--line); border-radius:14px;
          padding:26px 24px 22px; box-shadow:0 6px 24px rgba(20,28,45,.06); }}
  .logo {{ width:40px; height:40px; border-radius:11px; display:grid; place-items:center;
          background:linear-gradient(135deg,#3a6eff,#563ceb); color:#fff;
          font-weight:700; font-size:19px; margin-bottom:14px; }}
  h1 {{ font-size:16px; margin:0 0 6px; }}
  p  {{ font-size:12.5px; color:var(--t2); margin:0 0 18px; line-height:1.6; }}
  label {{ display:block; font-size:12px; color:var(--t2); margin-bottom:6px; }}
  input {{ width:100%; height:38px; padding:0 11px; font-size:14px;
          border:1px solid var(--line); border-radius:9px; outline:none; }}
  input:focus {{ border-color:var(--brand); box-shadow:0 0 0 3px rgba(47,107,255,.12); }}
  button {{ width:100%; height:38px; margin-top:14px; border:0; border-radius:9px;
           background:var(--brand); color:#fff; font-size:14px; font-weight:600; cursor:pointer; }}
  button:hover {{ background:#1f5aec; }}
  .err {{ margin-top:12px; font-size:12.5px; color:#e5484d; }}
  .foot {{ margin-top:16px; font-size:11.5px; color:var(--t3); line-height:1.6; }}
</style></head>
<body>
  <form class="card" method="post" action="/api/v1/auth/login">
    <div class="logo">收</div>
    <h1>统一收件箱</h1>
    <p>这里汇聚了飞书、邮箱等平台的原始消息，需要访问口令才能查看。</p>
    <label for="pw">访问口令</label>
    <input id="pw" name="password" type="password" autocomplete="current-password"
           autofocus placeholder="在 .env 里设置 ACCESS_PASSWORD">
    <button type="submit">进入</button>
    {err_block}
    <div class="foot">口令不对就进不来；想停用校验，把 <code>ACCESS_PASSWORD</code> 留空后重启。</div>
  </form>
</body></html>"""


def login_page(error: str = "") -> str:
    err_block = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return _LOGIN_HTML.format(err_block=err_block)
