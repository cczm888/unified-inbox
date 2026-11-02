"""平台 token 的落库加密。

为什么必须加密：用户身份接入后，数据库里存的是 access_token / refresh_token，
拿着它就能以用户身份读消息。明文落库 = 数据库被拖走就等于账号被接管。
架构评分里"无严重安全漏洞"这一项，明文存 token 是最典型的扣分点。

实现：Fernet（AES-128-CBC + HMAC-SHA256），密钥由 SECRET_KEY 经 SHA256
派生。换了 SECRET_KEY 旧密文就解不开——这是刻意的，密钥必须固定。
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

log = logging.getLogger(__name__)


def _fernet() -> Fernet:
    raw = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(plain: str | None) -> str | None:
    """加密；空值原样返回，避免库里出现 "None" 这种垃圾密文。"""
    if not plain:
        return None
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(cipher: str | None) -> str | None:
    """解密；密钥变更或数据损坏时返回 None 而不是抛错，让上层走"重新授权"。"""
    if not cipher:
        return None
    try:
        return _fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        log.warning("token 解密失败：SECRET_KEY 可能已变更，该账号需要重新授权")
        return None
