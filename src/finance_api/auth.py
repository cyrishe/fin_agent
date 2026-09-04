from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Mapping


_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class FinanceApiAuthError(ValueError):
    """Stable authentication error that never includes the supplied secret."""

    def __init__(self, code: str, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class FinanceApiPrincipal:
    principal_id: str


class FinanceApiKeyAuth:
    """Constant-time API-key verifier backed by environment configuration.

    Keys are hashed as soon as configuration is parsed.  The raw values are not
    retained on the service object and must never be written to logs or traces.
    """

    def __init__(self, keys: Mapping[str, str]) -> None:
        normalized: dict[str, bytes] = {}
        for raw_principal, raw_key in keys.items():
            principal = str(raw_principal or "").strip()
            key = str(raw_key or "").strip()
            if not _PRINCIPAL_RE.fullmatch(principal):
                raise ValueError(
                    "finance API key principal must match "
                    "[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
                )
            if len(key) < 24:
                raise ValueError(
                    f"finance API key for principal={principal} must contain at least 24 characters"
                )
            normalized[principal] = hashlib.sha256(key.encode("utf-8")).digest()
        if not normalized:
            raise ValueError(
                "finance API key is not configured; set FINANCE_API_KEYS_JSON "
                "or FINANCE_API_KEY"
            )
        self._digests = normalized

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FinanceApiKeyAuth:
        source = os.environ if env is None else env
        raw_json = str(source.get("FINANCE_API_KEYS_JSON") or "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise ValueError("FINANCE_API_KEYS_JSON must be a JSON object") from exc
            if not isinstance(parsed, Mapping):
                raise ValueError("FINANCE_API_KEYS_JSON must be a JSON object")
            return cls({str(key): str(value) for key, value in parsed.items()})

        single_key = str(source.get("FINANCE_API_KEY") or "").strip()
        if single_key:
            principal = str(
                source.get("FINANCE_API_KEY_ID") or "default"
            ).strip()
            return cls({principal: single_key})
        return cls({})

    def authenticate(self, authorization: str = "", x_api_key: str = "") -> FinanceApiPrincipal:
        bearer = str(authorization or "").strip()
        supplied = str(x_api_key or "").strip()
        if bearer:
            scheme, separator, credentials = bearer.partition(" ")
            if not separator or scheme.lower() != "bearer" or not credentials.strip():
                raise FinanceApiAuthError(
                    "invalid_authorization_header",
                    "Authorization must use the Bearer API-key scheme.",
                )
            supplied = credentials.strip()
        if not supplied:
            raise FinanceApiAuthError(
                "missing_api_key",
                "Provide the API key with Authorization: Bearer <key> or X-API-Key.",
            )

        supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
        matched = ""
        for principal, expected_digest in self._digests.items():
            if hmac.compare_digest(supplied_digest, expected_digest):
                matched = principal
        if not matched:
            raise FinanceApiAuthError(
                "invalid_api_key",
                "The supplied finance API key is invalid.",
            )
        return FinanceApiPrincipal(principal_id=matched)

    def status(self) -> dict[str, object]:
        return {
            "configured": True,
            "key_count": len(self._digests),
            "schemes": ["bearer", "x-api-key"],
        }
