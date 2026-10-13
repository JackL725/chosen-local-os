"""Founder passwords: scrypt from the standard library.

Stored as "scrypt$<log2 n>$<r>$<p>$<salt b64>$<hash b64>", so the cost can be raised
later without breaking existing hashes. Default cost n=2^17, r=8, p=1 (about 128 MB per
check), which follows OWASP's password storage guidance for scrypt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

DEFAULT_LOG2_N = 17
DEFAULT_R = 8
DEFAULT_P = 1
MIN_LENGTH = 12


class WeakPassword(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _derive(password: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    n = 1 << log2_n
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=256 * n * r + 2**20, dklen=32
    )


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"Use at least {MIN_LENGTH} characters.")
    if password.strip() != password:
        raise WeakPassword("A password cannot start or end with a space.")


def hash_password(password: str, *, log2_n: int | None = None) -> str:
    check_strength(password)
    log2_n = DEFAULT_LOG2_N if log2_n is None else log2_n
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, log2_n, DEFAULT_R, DEFAULT_P)
    return f"scrypt${log2_n}${DEFAULT_R}${DEFAULT_P}${_b64(salt)}${_b64(digest)}"


def verify(password: str, stored: str) -> bool:
    try:
        scheme, log2_n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt" or not (10 <= int(log2_n) <= 20):
            return False
        expected = base64.b64decode(digest)
        actual = _derive(password, base64.b64decode(salt), int(log2_n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A hash of a random password, checked when the account does not exist, so a wrong
# username takes as long as a wrong password.
_DUMMY = None


def dummy_verify(password: str) -> None:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(24))
    verify(password, _DUMMY)
