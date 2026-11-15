"""Passwords and login tokens.

Passwords: only Argon2id hashes are stored, never the plaintext. The hash is one-way: it can
verify a password but cannot be reversed.
Tokens: a random string issued at login and sent with every subsequent request. The database
stores only its SHA-256, so a leaked database cannot be used to impersonate anyone."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # Defaults match the OWASP-recommended Argon2id parameters.

_DUMMY_HASH = _hasher.hash("用户不存在时也算一次哈希")
"""Verified against when the user name does not exist, so the response time is the same and cannot
reveal which user names exist."""

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Hash a password. The same password yields a different hash each time (random salt); this is
    expected."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Whether the password is correct. When password_hash is None (unknown user), a hash is still
    computed and False is returned."""
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)  # Raises on a wrong password.
    except (VerificationError, InvalidHashError):
        return False
    # Unknown user: never succeed, even if the dummy hash happens to match.
    return password_hash is not None


def new_token() -> str:
    """Login token: 32 random bytes encoded as 43 characters. The probability of guessing it is
    negligible."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """The database stores a hash of the token. The token already has full entropy, so SHA-256
    suffices; Argon2 is unnecessary."""
    return hashlib.sha256(token.encode()).hexdigest()


RESET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Alphabet for reset codes: excludes the easily confused characters 0 O 1 I."""


def new_reset_code() -> str:
    """One-time reset code such as "K7QM-3XWD": 8 characters from a 32-character alphabet, about
    10^12 combinations.

        Valid for 15 minutes and usable once; an administrator passes it on in person or by
    phone. Much shorter than a login token, yet secure enough under these limits."""
    raw = "".join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_reset_code(code: str) -> str:
    """Input is case-insensitive and ignores hyphens and spaces: "k7qm 3xwd" equals "K7QM-3XWD"."""
    return "".join(c for c in code.upper() if c.isalnum())
