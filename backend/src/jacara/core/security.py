"""密码和登录 token。

密码：只存 Argon2id 哈希，不存原文。哈希是单向的：能验证"输入的密码对不对"，但算不回原文。
token：登录成功后发给用户的一串随机字符，之后每个请求都带上它。数据库里只存它的 SHA-256，
        数据库泄露了，拿到的也只是哈希，冒充不了任何人。
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # 默认参数就是 OWASP 推荐的 Argon2id 设置

_DUMMY_HASH = _hasher.hash("用户不存在时也算一次哈希")
"""用户名不存在时，也拿这个哈希验证一次：花的时间一样，别人就没法靠"响应快慢"猜出哪些用户名存在。"""

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """把密码变成哈希。同一个密码每次结果都不一样（里面带了随机的盐），这是正常的。"""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """密码对不对。password_hash 是 None（用户不存在）时也照样算一次，然后返回 False。"""
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)  # 密码不对会抛出异常
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None  # 用户不存在时，就算"碰巧对上"假哈希也不算


def new_token() -> str:
    """登录 token：32 字节的随机数，写成 43 个字符。猜中的概率可以忽略。"""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """数据库里存的是 token 的哈希。token 本身已经足够随机，用 SHA-256 就够了，不需要 Argon2。"""
    return hashlib.sha256(token.encode()).hexdigest()
