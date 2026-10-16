from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import re
import secrets
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import pymysql

from src.utils.system_db_utils import SystemDbUtils


logger = logging.getLogger(__name__)

AUTH_ATTEMPT_TABLE = "aiia_auth_attempt"
_ACTION_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_ATTEMPT_ID_PATTERN = re.compile(r"ara_[A-Za-z0-9_-]{16,60}")

DbFactory = Callable[[], Any]
Clock = Callable[[], datetime]
AttemptIdFactory = Callable[[], str]


class AuthRateLimitError(RuntimeError):
    """Stable public error raised by the shared authentication limiter."""

    def __init__(
        self,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "auth_rate_limit_error")
        self.message = str(message or "认证服务暂时不可用，请稍后再试。")
        self.public_message = self.message
        self.retry_after = (
            max(1, int(retry_after)) if retry_after is not None else None
        )


class AuthRateLimitService:
    """Database-backed authentication limiter shared by all web workers.

    `begin_attempt` completes its transaction and releases all advisory locks
    before returning. Password hashing and any provider/network operation must
    happen only after it returns. Failure budgets count only rows whose
    `succeeded_at` is still NULL. Coarser per-IP and global request budgets
    count every row, so a valid password cannot bypass the CPU/session guard by
    immediately marking each attempt successful.
    """

    def __init__(
        self,
        *,
        secret: str | None = None,
        mobile_limit: int = 8,
        ip_limit: int = 30,
        window_seconds: int = 10 * 60,
        request_ip_limit: int = 20,
        request_global_limit: int = 120,
        request_window_seconds: int = 60,
        lock_timeout_seconds: int = 3,
        db_factory: DbFactory | None = None,
        clock: Clock | None = None,
        attempt_id_factory: AttemptIdFactory | None = None,
    ) -> None:
        self._secret = str(secret or "").encode("utf-8")
        self._mobile_limit = _bounded_int(
            mobile_limit,
            default=8,
            minimum=1,
            maximum=1_000,
        )
        self._ip_limit = _bounded_int(
            ip_limit,
            default=30,
            minimum=1,
            maximum=10_000,
        )
        self._window_seconds = _bounded_int(
            window_seconds,
            default=10 * 60,
            minimum=60,
            maximum=24 * 60 * 60,
        )
        self._request_ip_limit = _bounded_int(
            request_ip_limit,
            default=20,
            minimum=1,
            maximum=100_000,
        )
        self._request_global_limit = _bounded_int(
            request_global_limit,
            default=120,
            minimum=1,
            maximum=1_000_000,
        )
        self._request_window_seconds = _bounded_int(
            request_window_seconds,
            default=60,
            minimum=60,
            maximum=24 * 60 * 60,
        )
        self._lock_timeout_seconds = _bounded_int(
            lock_timeout_seconds,
            default=3,
            minimum=1,
            maximum=10,
        )
        self._db_factory = db_factory or SystemDbUtils
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._attempt_id_factory = attempt_id_factory or (
            lambda: f"ara_{secrets.token_urlsafe(24)}"
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        db_factory: DbFactory | None = None,
        clock: Clock | None = None,
        attempt_id_factory: AttemptIdFactory | None = None,
    ) -> AuthRateLimitService:
        source = os.environ if env is None else env
        # An explicitly configured auth secret is authoritative. Falling back
        # is only for deployments that intentionally share the existing phone
        # challenge secret, not for hiding a malformed auth-specific value.
        secret = source.get("FIN_AGENT_AUTH_RATE_SECRET")
        if secret is None or not str(secret).strip():
            secret = source.get("FIN_AGENT_PHONE_CHALLENGE_SECRET")
        return cls(
            secret=secret,
            mobile_limit=_env_int(
                source,
                "FIN_AGENT_AUTH_LOGIN_MOBILE_LIMIT",
                8,
            ),
            ip_limit=_env_int(
                source,
                "FIN_AGENT_AUTH_LOGIN_IP_LIMIT",
                30,
            ),
            window_seconds=_env_int(
                source,
                "FIN_AGENT_AUTH_LOGIN_WINDOW_SECONDS",
                10 * 60,
            ),
            request_ip_limit=_env_int(
                source,
                "FIN_AGENT_AUTH_REQUEST_IP_LIMIT",
                20,
            ),
            request_global_limit=_env_int(
                source,
                "FIN_AGENT_AUTH_REQUEST_GLOBAL_LIMIT",
                120,
            ),
            request_window_seconds=_env_int(
                source,
                "FIN_AGENT_AUTH_REQUEST_WINDOW_SECONDS",
                60,
            ),
            lock_timeout_seconds=_env_int(
                source,
                "FIN_AGENT_AUTH_RATE_LOCK_TIMEOUT_SECONDS",
                3,
            ),
            db_factory=db_factory,
            clock=clock,
            attempt_id_factory=attempt_id_factory,
        )

    def status(self) -> dict[str, Any]:
        secret_configured = len(self._secret) >= 32
        return {
            "enabled": secret_configured,
            "configured": secret_configured,
            "secret_configured": secret_configured,
            "table": AUTH_ATTEMPT_TABLE,
            "login": {
                "mobile_limit": self._mobile_limit,
                "ip_limit": self._ip_limit,
                "window_seconds": self._window_seconds,
            },
            "request_budget": {
                "ip_limit": self._request_ip_limit,
                "global_limit": self._request_global_limit,
                "window_seconds": self._request_window_seconds,
            },
        }

    def schema_ready(self) -> bool:
        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor() as cursor:
                cursor.execute("SHOW TABLES LIKE %s", (AUTH_ATTEMPT_TABLE,))
                return bool(cursor.fetchone())
        except Exception:
            logger.warning("Auth rate-limit schema readiness check failed")
            return False
        finally:
            if db is not None:
                db.close_db()

    def begin_attempt(
        self,
        action: str,
        subject: str,
        remote_addr: str,
    ) -> str:
        self._require_configured()
        normalized_action = _validate_action(action)
        normalized_subject = _validate_dimension(
            subject,
            code="invalid_auth_subject",
        )
        normalized_remote_addr = _validate_dimension(
            remote_addr,
            code="invalid_auth_remote_addr",
        )
        now = self._now()
        failure_cutoff = now - timedelta(seconds=self._window_seconds)
        request_cutoff = now - timedelta(
            seconds=self._request_window_seconds
        )
        subject_hash = self._hmac_hex(
            f"v1:{normalized_action}:subject:{normalized_subject}"
        )
        remote_addr_hash = self._hmac_hex(
            f"v1:{normalized_action}:remote:{normalized_remote_addr}"
        )
        attempt_id = _validate_attempt_id(self._attempt_id_factory())
        global_lock_hash = self._hmac_hex(
            f"v1:{normalized_action}:global"
        )
        lock_names = sorted(
            {
                f"fin_auth_g:{global_lock_hash[:32]}",
                f"fin_auth_i:{remote_addr_hash[:32]}",
                f"fin_auth_s:{subject_hash[:32]}",
            }
        )

        db = None
        acquired_locks: list[str] = []
        try:
            db = self._db_factory()
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                self._require_schema(cursor)
                for lock_name in lock_names:
                    cursor.execute(
                        "SELECT GET_LOCK(%s, %s) AS acquired",
                        (lock_name, self._lock_timeout_seconds),
                    )
                    if _row_int(cursor.fetchone(), "acquired") != 1:
                        raise AuthRateLimitError(
                            "auth_rate_limit_busy",
                            "认证请求正在处理中，请稍后重试。",
                            retry_after=1,
                        )
                    acquired_locks.append(lock_name)

                self._enforce_all_attempts(
                    cursor,
                    action=normalized_action,
                    column=None,
                    value=None,
                    cutoff=request_cutoff,
                    limit=self._request_global_limit,
                    now=now,
                )
                self._enforce_all_attempts(
                    cursor,
                    action=normalized_action,
                    column="remote_addr_hash",
                    value=remote_addr_hash,
                    cutoff=request_cutoff,
                    limit=self._request_ip_limit,
                    now=now,
                )
                self._enforce_dimension(
                    cursor,
                    action=normalized_action,
                    column="subject_hash",
                    value=subject_hash,
                    cutoff=failure_cutoff,
                    limit=self._mobile_limit,
                    now=now,
                )
                self._enforce_dimension(
                    cursor,
                    action=normalized_action,
                    column="remote_addr_hash",
                    value=remote_addr_hash,
                    cutoff=failure_cutoff,
                    limit=self._ip_limit,
                    now=now,
                )
                cursor.execute(
                    f"""
                    INSERT INTO {AUTH_ATTEMPT_TABLE} (
                      attempt_id, action, subject_hash, remote_addr_hash,
                      created_at, succeeded_at
                    ) VALUES (%s, %s, %s, %s, %s, NULL)
                    """,
                    (
                        attempt_id,
                        normalized_action,
                        subject_hash,
                        remote_addr_hash,
                        now,
                    ),
                )
            db.conn.commit()
            return attempt_id
        except AuthRateLimitError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Auth rate-limit begin failed type=%s",
                type(exc).__name__,
            )
            raise _storage_error() from None
        finally:
            if db is not None:
                self._release_locks(db, acquired_locks)
                db.close_db()

    def mark_succeeded(self, attempt_id: str) -> None:
        normalized_attempt_id = _validate_attempt_id(attempt_id)
        now = self._now()
        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor() as cursor:
                self._require_schema(cursor)
                cursor.execute(
                    f"""
                    UPDATE {AUTH_ATTEMPT_TABLE}
                    SET succeeded_at = %s
                    WHERE attempt_id = %s AND succeeded_at IS NULL
                    """,
                    (now, normalized_attempt_id),
                )
            db.conn.commit()
        except AuthRateLimitError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Auth rate-limit success update failed type=%s",
                type(exc).__name__,
            )
            raise _storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def cleanup_old_attempts(
        self,
        retention_days: int = 30,
        limit: int = 5_000,
    ) -> int:
        """Delete one bounded batch of old attempts in a short transaction.

        This method is deliberately never called from the authentication path.
        Operators may invoke it from an external maintenance job until it
        returns fewer rows than `limit`.
        """

        normalized_retention_days = _strict_bounded_int(
            retention_days,
            minimum=1,
            maximum=3_650,
            code="invalid_auth_cleanup_request",
        )
        normalized_limit = _strict_bounded_int(
            limit,
            minimum=1,
            maximum=50_000,
            code="invalid_auth_cleanup_request",
        )
        cutoff = self._now() - timedelta(days=normalized_retention_days)
        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor() as cursor:
                self._require_schema(cursor)
                deleted = int(
                    cursor.execute(
                        f"""
                        DELETE FROM {AUTH_ATTEMPT_TABLE}
                        WHERE created_at < %s
                        ORDER BY created_at ASC
                        LIMIT %s
                        """,
                        (cutoff, normalized_limit),
                    )
                    or 0
                )
            db.conn.commit()
            return deleted
        except AuthRateLimitError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Auth rate-limit cleanup failed type=%s",
                type(exc).__name__,
            )
            raise _storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def _enforce_all_attempts(
        self,
        cursor: Any,
        *,
        action: str,
        column: str | None,
        value: str | None,
        cutoff: datetime,
        limit: int,
        now: datetime,
    ) -> None:
        if column is None:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS request_count,
                       MIN(created_at) AS first_request_at
                FROM {AUTH_ATTEMPT_TABLE}
                WHERE action = %s
                  AND created_at >= %s
                """,
                (action, cutoff),
            )
        else:
            # `column` is selected exclusively by a hard-coded call site.
            cursor.execute(
                f"""
                SELECT COUNT(*) AS request_count,
                       MIN(created_at) AS first_request_at
                FROM {AUTH_ATTEMPT_TABLE}
                WHERE action = %s
                  AND {column} = %s
                  AND created_at >= %s
                """,
                (action, value, cutoff),
            )
        row = cursor.fetchone()
        count = _row_int(row, "request_count")
        if count < limit:
            return
        retry_after = _retry_after(
            first_at=_as_datetime(
                _row_value(row, "first_request_at")
            ),
            now=now,
            window_seconds=self._request_window_seconds,
        )
        raise AuthRateLimitError(
            "auth_rate_limited",
            "认证请求过于频繁，请稍后再试。",
            retry_after=retry_after,
        )

    def _enforce_dimension(
        self,
        cursor: Any,
        *,
        action: str,
        column: str,
        value: str,
        cutoff: datetime,
        limit: int,
        now: datetime,
    ) -> None:
        # `column` is selected exclusively by hard-coded call sites.
        cursor.execute(
            f"""
            SELECT COUNT(*) AS failed_count, MIN(created_at) AS first_failed_at
            FROM {AUTH_ATTEMPT_TABLE}
            WHERE action = %s
              AND {column} = %s
              AND succeeded_at IS NULL
              AND created_at >= %s
            """,
            (action, value, cutoff),
        )
        row = cursor.fetchone()
        count = _row_int(row, "failed_count")
        if count < limit:
            return
        retry_after = _retry_after(
            first_at=_as_datetime(
                _row_value(row, "first_failed_at")
            ),
            now=now,
            window_seconds=self._window_seconds,
        )
        raise AuthRateLimitError(
            "auth_rate_limited",
            "登录尝试次数较多，请稍后再试。",
            retry_after=retry_after,
        )

    @staticmethod
    def _require_schema(cursor: Any) -> None:
        cursor.execute("SHOW TABLES LIKE %s", (AUTH_ATTEMPT_TABLE,))
        if not cursor.fetchone():
            raise AuthRateLimitError(
                "auth_rate_limit_storage_unavailable",
                "认证服务暂时不可用，请稍后再试。",
            )

    def _require_configured(self) -> None:
        if len(self._secret) < 32:
            raise AuthRateLimitError(
                "auth_rate_limit_not_configured",
                "认证服务尚未完成安全配置。",
            )

    def _hmac_hex(self, value: str) -> str:
        return hmac.new(
            self._secret,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _release_locks(self, db: Any, lock_names: list[str]) -> None:
        if not lock_names:
            return
        try:
            with db.conn.cursor() as cursor:
                for lock_name in reversed(lock_names):
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        except Exception as exc:
            logger.warning(
                "Failed to release auth rate-limit lock type=%s",
                type(exc).__name__,
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


def _validate_action(value: object) -> str:
    action = str(value or "").strip().lower()
    if not _ACTION_PATTERN.fullmatch(action):
        raise AuthRateLimitError(
            "invalid_auth_action",
            "认证操作无效。",
        )
    return action


def _validate_dimension(value: object, *, code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized.encode("utf-8")) > 512:
        raise AuthRateLimitError(code, "认证请求信息无效。")
    return normalized


def _validate_attempt_id(value: object) -> str:
    attempt_id = str(value or "").strip()
    if not _ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
        raise AuthRateLimitError(
            "invalid_auth_attempt_id",
            "认证尝试标识无效。",
        )
    return attempt_id


def _storage_error() -> AuthRateLimitError:
    return AuthRateLimitError(
        "auth_rate_limit_storage_unavailable",
        "认证服务暂时不可用，请稍后再试。",
    )


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _strict_bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    code: str,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AuthRateLimitError(code, "认证记录清理参数无效。") from None
    if parsed < minimum or parsed > maximum:
        raise AuthRateLimitError(code, "认证记录清理参数无效。")
    return parsed


def _env_int(
    source: Mapping[str, str],
    key: str,
    default: int,
) -> int:
    try:
        return int(str(source.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def _row_value(row: object, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    if isinstance(row, (tuple, list)) and row:
        return row[0]
    return None


def _row_int(row: object, key: str) -> int:
    value = _row_value(row, key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _retry_after(
    *,
    first_at: datetime | None,
    now: datetime,
    window_seconds: int,
) -> int:
    if first_at is None:
        return window_seconds
    return max(
        1,
        math.ceil(
            (
                first_at
                + timedelta(seconds=window_seconds)
                - now
            ).total_seconds()
        ),
    )
