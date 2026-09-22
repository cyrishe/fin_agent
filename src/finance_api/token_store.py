"""Database-backed finance access tokens with revocation support.

Only a SHA-256 digest and a short display preview are persisted.  The complete
credential is returned once to the operator and must never be logged or stored
in this table.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Callable

from src.utils.system_db_utils import connect_system_db


MANAGED_TOKEN_PREFIX = "fa_db_v1"
_TOKEN_ID_RE = re.compile(r"[0-9a-f]{32}")
_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class ManagedTokenError(ValueError):
    """Base error whose message is safe to expose without credential data."""


class ManagedTokenInvalid(ManagedTokenError):
    """The supplied managed token is absent, disabled, expired, or invalid."""


class ManagedTokenStoreUnavailable(ManagedTokenError):
    """The system database could not be reached or queried."""


def _utc_datetime(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).replace(tzinfo=None)


def _epoch_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _validate_label(field: str, value: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{field} must contain 1-{maximum} printable characters")
    return normalized


class FinanceAccessTokenStore:
    """Issue, authenticate, list, and disable managed access tokens."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], object] = connect_system_db,
        now_provider: Callable[[], float] = time.time,
    ) -> None:
        self._connection_factory = connection_factory
        self._now_provider = now_provider

    def _connect(self):
        try:
            return self._connection_factory()
        except Exception as exc:
            raise ManagedTokenStoreUnavailable("Managed access-token storage is unavailable.") from exc

    def issue(
        self,
        *,
        project_name: str,
        token_name: str,
        principal_id: str,
        ttl_seconds: int | None,
        created_by: str | None = None,
    ) -> dict[str, object]:
        project = _validate_label("project_name", project_name, 128)
        name = _validate_label("token_name", token_name, 128)
        principal = str(principal_id or "").strip()
        if not _PRINCIPAL_RE.fullmatch(principal):
            raise ValueError("principal_id is invalid")
        creator = None if created_by is None else _validate_label("created_by", created_by, 64)
        if ttl_seconds is not None and (
            type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 2**31 - 1
        ):
            raise ValueError("ttl_seconds must be null or a positive integer no greater than 2147483647")

        issued_at = int(self._now_provider())
        expires_at = None if ttl_seconds is None else issued_at + ttl_seconds
        token_id = secrets.token_hex(16)
        token = f"{MANAGED_TOKEN_PREFIX}.{token_id}.{secrets.token_urlsafe(32)}"
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        preview_start, preview_end = token[:16], token[-8:]
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO aiia_finance_access_token
                        (token_id, project_name, token_name, principal_id, token_digest,
                         token_prefix, token_suffix, created_at, expires_at, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        token_id,
                        project,
                        name,
                        principal,
                        digest,
                        preview_start,
                        preview_end,
                        _utc_datetime(issued_at),
                        None if expires_at is None else _utc_datetime(expires_at),
                        creator,
                    ),
                )
            connection.commit()
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise ManagedTokenStoreUnavailable("Managed access-token storage is unavailable.") from exc
        finally:
            connection.close()

        return {
            "access_token": token,
            "token_type": "Bearer",
            "token_id": token_id,
            "project_name": project,
            "token_name": name,
            "principal_id": principal,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "expires_in": ttl_seconds,
            "never_expires": ttl_seconds is None,
            "masked_token": f"{preview_start}...{preview_end}",
        }

    def authenticate(self, token: str) -> str:
        try:
            if len(token) > 512:
                raise ManagedTokenInvalid("The managed access token is invalid or inactive.")
            prefix, token_id, secret = token.split(".")
            if (
                prefix != MANAGED_TOKEN_PREFIX
                or not _TOKEN_ID_RE.fullmatch(token_id)
                or len(secret) < 32
            ):
                raise ManagedTokenInvalid("The managed access token is invalid or inactive.")
        except ValueError:
            raise ManagedTokenInvalid("The managed access token is invalid or inactive.") from None

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT principal_id, token_digest, expires_at, disabled_at
                    FROM aiia_finance_access_token
                    WHERE token_id = %s
                    """,
                    (token_id,),
                )
                row = cursor.fetchone()
        except Exception as exc:
            raise ManagedTokenStoreUnavailable("Managed access-token storage is unavailable.") from exc
        finally:
            connection.close()

        if not row:
            raise ManagedTokenInvalid("The managed access token is invalid or inactive.")
        try:
            principal_id, expected_digest, expires_at, disabled_at = row
            supplied_digest = hashlib.sha256(token.encode("utf-8")).digest()
            if (
                disabled_at is not None
                or not hmac.compare_digest(bytes(expected_digest), supplied_digest)
                or (expires_at is not None and _epoch_seconds(expires_at) <= int(self._now_provider()))
            ):
                raise ManagedTokenInvalid("The managed access token is invalid or inactive.")
            return str(principal_id)
        except ManagedTokenInvalid:
            raise
        except Exception as exc:
            raise ManagedTokenStoreUnavailable("Managed access-token storage is unavailable.") from exc

    def list_tokens(self, *, project_name: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        project = None if project_name is None else _validate_label("project_name", project_name, 128)
        query = """
            SELECT token_id, project_name, token_name, principal_id, token_prefix,
                   token_suffix, created_at, expires_at, disabled_at, disabled_reason,
                   created_by, disabled_by
            FROM aiia_finance_access_token
        """
        params: tuple[object, ...]
        if project is None:
            query += " ORDER BY created_at DESC LIMIT %s"
            params = (limit,)
        else:
            query += " WHERE project_name = %s ORDER BY created_at DESC LIMIT %s"
            params = (project, limit)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall()
        except Exception as exc:
            raise ManagedTokenStoreUnavailable("Managed access-token storage is unavailable.") from exc
        finally:
            connection.close()

        now = int(self._now_provider())
        results: list[dict[str, object]] = []
        for row in rows:
            (token_id, row_project, name, principal, preview_start, preview_end,
             created_at, expires_at, disabled_at, disabled_reason, created_by, disabled_by) = row
            expires_epoch = _epoch_seconds(expires_at)
            state = "disabled" if disabled_at is not None else (
                "expired" if expires_epoch is not None and expires_epoch <= now else "active"
            )
            results.append({
                "token_id": token_id,
                "project_name": row_project,
                "token_name": name,
                "principal_id": principal,
                "masked_token": f"{preview_start}...{preview_end}",
                "created_at": _epoch_seconds(created_at),
                "expires_at": expires_epoch,
                "never_expires": expires_epoch is None,
                "state": state,
                "disabled_at": _epoch_seconds(disabled_at),
                "disabled_reason": disabled_reason,
                "created_by": created_by,
                "disabled_by": disabled_by,
            })
        return results

    def disable(
        self,
        token_id: str,
        *,
        disabled_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        normalized_id = str(token_id or "").strip().lower()
        if not _TOKEN_ID_RE.fullmatch(normalized_id):
            raise ValueError("token_id must be 32 lowercase hexadecimal characters")
        actor = None if disabled_by is None else _validate_label("disabled_by", disabled_by, 64)
        normalized_reason = None if reason is None else _validate_label("reason", reason, 255)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE aiia_finance_access_token
                    SET disabled_at = %s, disabled_by = %s, disabled_reason = %s
                    WHERE token_id = %s AND disabled_at IS NULL
                    """,
                    (_utc_datetime(int(self._now_provider())), actor, normalized_reason, normalized_id),
                )
                changed = cursor.rowcount == 1
            connection.commit()
            return changed
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise ManagedTokenStoreUnavailable("Managed access-token storage is unavailable.") from exc
        finally:
            connection.close()
