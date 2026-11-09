from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_s, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def create_token(username: str, secret: str, ttl_seconds: int) -> str:
    payload = {"sub": username, "exp": int(time.time()) + ttl_seconds}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    body = _b64url(raw)
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return body + "." + _b64url(mac)


def decode_token(token: str, secret: str) -> str | None:
    try:
        body, mac_b64 = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        given = base64.urlsafe_b64decode(mac_b64 + "==")
        if not hmac.compare_digest(given, expected):
            return None
        raw = base64.urlsafe_b64decode(body + "==")
        payload = json.loads(raw)
        if int(payload["exp"]) < int(time.time()):
            return None
        return str(payload["sub"])
    except Exception:
        return None

