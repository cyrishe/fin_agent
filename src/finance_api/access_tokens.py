"""Purpose-bound, expiring credentials derived from an existing API key.

No public mint endpoint: only an operator holding the parent key can issue one.
Rotating/removing that key invalidates all of its temporary tokens.
"""
from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
from typing import Mapping

PREFIX = "fa_tmp_v1"
DEFAULT_TTL_SECONDS = 4 * 60 * 60


def signing_key(api_key: str) -> bytes:
    return hmac.digest(api_key.encode(), b"fin-agent/temporary-access/v1", "sha256")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def issue_token(principal: str, key: bytes, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict:
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 2**31 - 1:
        raise ValueError("ttl_seconds must be a positive integer no greater than 2147483647")
    issued = int(time.time())
    claims = {"sub": principal, "iat": issued, "exp": issued + ttl_seconds,
              "jti": secrets.token_urlsafe(16)}
    body = _encode(json.dumps(claims, separators=(",", ":")).encode())
    signed = f"{PREFIX}.{body}"
    token = f"{signed}.{_encode(hmac.digest(key, signed.encode(), 'sha256'))}"
    return {"access_token": token, "token_type": "Bearer", "principal_id": principal,
            "issued_at": issued, "expires_at": claims["exp"], "expires_in": ttl_seconds}


def verify_token(token: str, keys: Mapping[str, bytes]) -> str:
    """Return the signed principal or raise a non-secret-bearing ValueError."""
    try:
        if len(token) > 2048:
            raise ValueError
        prefix, body, signature = token.split(".")
        if prefix != PREFIX:
            raise ValueError
        claims = json.loads(base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True))
        principal = claims["sub"]
        key = keys[principal]
        expected = _encode(hmac.digest(key, f"{prefix}.{body}".encode(), "sha256"))
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        issued, expires = claims["iat"], claims["exp"]
        now = time.time()
        if (type(issued) is not int or type(expires) is not int
                or not issued <= now < expires or not isinstance(claims["jti"], str)):
            raise ValueError
        return principal
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Temporary access token is invalid or expired.") from None
