"""Local account authentication for the read-only web console.

Passwords are hashed with PBKDF2-HMAC-SHA256 (600k rounds, stdlib only).
Sessions are stateless HMAC-signed cookies: ``base64url(payload).hexsig``.
Login failures are rate limited in memory with exponential backoff to slow
down LAN brute force attempts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

PBKDF2_ITERATIONS = 600_000
PASSWORD_SCHEME = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"{PASSWORD_SCHEME}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = str(stored).split("$")
        if scheme != PASSWORD_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session_token(
    secret: str,
    user_id: int,
    username: str,
    role: str,
    *,
    ttl_hours: float = 12.0,
    now: float | None = None,
) -> str:
    now = time.time() if now is None else now
    payload = {
        "uid": int(user_id),
        "u": str(username),
        "r": str(role),
        "iat": int(now),
        "exp": int(now + float(ttl_hours) * 3600),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256)
    return f"{body}.{signature.hexdigest()}"


def verify_session_token(
    secret: str, token: str | None, *, now: float | None = None
) -> dict[str, Any] | None:
    """Return the session payload, or ``None`` when invalid/expired."""
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    now = time.time() if now is None else now
    try:
        if float(payload.get("exp", 0)) < now:
            return None
    except (TypeError, ValueError):
        return None
    return payload


class LoginRateLimiter:
    """In-memory per-username login failure counter with exponential backoff."""

    def __init__(
        self,
        max_attempts: int = 5,
        base_backoff_sec: float = 2.0,
        max_backoff_sec: float = 300.0,
    ):
        self.max_attempts = int(max_attempts)
        self.base_backoff_sec = float(base_backoff_sec)
        self.max_backoff_sec = float(max_backoff_sec)
        self._failures: dict[str, tuple[int, float]] = {}

    def blocked_until(self, key: str, now: float | None = None) -> float:
        """Return the epoch until which ``key`` is blocked (0 = not blocked)."""
        now = time.time() if now is None else now
        entry = self._failures.get(key)
        if not entry:
            return 0.0
        _, blocked_until = entry
        return blocked_until if blocked_until > now else 0.0

    def record_failure(self, key: str, now: float | None = None) -> float:
        now = time.time() if now is None else now
        count, _ = self._failures.get(key, (0, 0.0))
        count += 1
        blocked_until = 0.0
        if count >= self.max_attempts:
            backoff = min(
                self.max_backoff_sec,
                self.base_backoff_sec * (2 ** (count - self.max_attempts)),
            )
            blocked_until = now + backoff
        self._failures[key] = (count, blocked_until)
        return blocked_until

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
