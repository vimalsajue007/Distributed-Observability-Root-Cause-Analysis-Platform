"""Password hashing (PBKDF2-HMAC-SHA256) and JWT helpers for the observability console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import jwt

PBKDF2_ITERATIONS = 240_000


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(candidate, expected)
    except Exception:  # noqa: BLE001
        return False


def create_token(secret: str, *, subject: str, role: str, ttl_seconds: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": subject, "role": role, "iat": now, "exp": now + ttl_seconds}, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid/expired tokens."""
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
