from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from typing import Any

import pymysql

from src.utils.system_db_utils import SystemDbUtils


logger = logging.getLogger(__name__)

CHALLENGE_TABLE = "aiia_phone_verification_challenge"
_PURPOSE = "registration"
_SUPPORTED_PROVIDERS = frozenset(
    {"disabled", "mock", "aliyun", "aliyun_pnvs"}
)
_MOBILE_PATTERN = re.compile(r"1[3-9][0-9]{9}")
_CODE_PATTERN = re.compile(r"[0-9]{6}")
_CHALLENGE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}")


class PhonePossessionVerificationError(ValueError):
    """Stable public error raised by the SMS challenge service."""

    def __init__(
        self,
        code: str,
        public_message: str,
        retryable: bool,
        *,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class PhonePossessionProof:
    """A verified, not-yet-consumed phone-possession proof.

    `consumed_at` deliberately is not set here. The account service must lock
    this challenge and set `consumed_at` in the same transaction that creates
    the unique phone identity.
    """

    challenge_id: str
    mobile_hash: str
    provider: str
    provider_request_id: str | None
    verified_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "mobile_hash": self.mobile_hash,
            "provider": self.provider,
            "provider_request_id": self.provider_request_id,
            "verified_at": self.verified_at,
        }


AliyunSmsCaller = Callable[[str, str], object]
AliyunPnvsSendCaller = Callable[[str, str], object]
AliyunPnvsVerifyCaller = Callable[[str, str, str], object]
DbFactory = Callable[[], Any]
Clock = Callable[[], datetime]
CodeFactory = Callable[[], str]
ChallengeIdFactory = Callable[[], str]


class PhonePossessionVerificationService:
    """Issue and verify durable SMS challenges without storing phone plaintext.

    Cross-worker rate limits and single-flight behavior use database records
    plus short-lived MySQL advisory locks. A committed pending row preserves
    single-flight after those locks and the DB connection are released; the
    external provider call therefore never holds database resources.
    """

    def __init__(
        self,
        *,
        provider: str = "disabled",
        challenge_secret: str | None = None,
        mock_enabled: bool = False,
        mock_code: str = "123456",
        aliyun_access_key_id: str | None = None,
        aliyun_access_key_secret: str | None = None,
        aliyun_sign_name: str | None = None,
        aliyun_template_code: str | None = None,
        aliyun_template_param_key: str = "code",
        aliyun_endpoint: str = "dysmsapi.aliyuncs.com",
        aliyun_region_id: str = "cn-hangzhou",
        pnvs_sign_name: str | None = None,
        pnvs_template_code: str = "100001",
        pnvs_scheme_name: str = "fin-agent-register",
        pnvs_endpoint: str = "dypnsapi.aliyuncs.com",
        connect_timeout_ms: int = 3_000,
        read_timeout_ms: int = 5_000,
        challenge_ttl_seconds: int = 600,
        resend_cooldown_seconds: int = 60,
        max_attempts: int = 5,
        rate_window_seconds: int = 3_600,
        mobile_rate_limit: int = 5,
        ip_rate_limit: int = 20,
        global_hourly_limit: int = 500,
        global_daily_limit: int = 2_000,
        pending_timeout_seconds: int = 30,
        lock_timeout_seconds: int = 3,
        aliyun_caller: AliyunSmsCaller | None = None,
        pnvs_send_caller: AliyunPnvsSendCaller | None = None,
        pnvs_verify_caller: AliyunPnvsVerifyCaller | None = None,
        db_factory: DbFactory | None = None,
        clock: Clock | None = None,
        code_factory: CodeFactory | None = None,
        challenge_id_factory: ChallengeIdFactory | None = None,
    ):
        self.provider = str(provider or "disabled").strip().lower()
        self._secret = str(challenge_secret or "").encode("utf-8")
        self._mock_enabled = bool(mock_enabled)
        self._mock_code = str(mock_code or "")
        self._aliyun_access_key_id = _clean(aliyun_access_key_id)
        self._aliyun_access_key_secret = _clean(aliyun_access_key_secret)
        self._aliyun_sign_name = _clean(aliyun_sign_name)
        self._aliyun_template_code = _clean(aliyun_template_code)
        self._aliyun_template_param_key = (
            _clean(aliyun_template_param_key) or "code"
        )
        self._aliyun_endpoint = (
            _clean(aliyun_endpoint) or "dysmsapi.aliyuncs.com"
        )
        self._aliyun_region_id = _clean(aliyun_region_id) or "cn-hangzhou"
        self._pnvs_sign_name = _clean(pnvs_sign_name)
        self._pnvs_template_code = _clean(pnvs_template_code) or "100001"
        self._pnvs_scheme_name = (
            _clean(pnvs_scheme_name) or "fin-agent-register"
        )[:20]
        self._pnvs_endpoint = (
            _clean(pnvs_endpoint) or "dypnsapi.aliyuncs.com"
        )
        self._connect_timeout_ms = _bounded_int(
            connect_timeout_ms, default=3_000, minimum=1, maximum=30_000
        )
        self._read_timeout_ms = _bounded_int(
            read_timeout_ms, default=5_000, minimum=1, maximum=30_000
        )
        minimum_pending_seconds = (
            math.ceil(
                (self._connect_timeout_ms + self._read_timeout_ms) / 1_000
            )
            + 10
        )
        self._pending_timeout_seconds = max(
            _bounded_int(
                pending_timeout_seconds,
                default=30,
                minimum=5,
                maximum=120,
            ),
            minimum_pending_seconds,
        )
        self._challenge_ttl_seconds = max(
            _bounded_int(
                challenge_ttl_seconds,
                default=600,
                minimum=60,
                maximum=1_800,
            ),
            self._pending_timeout_seconds + 60,
        )
        self._resend_cooldown_seconds = max(
            _bounded_int(
                resend_cooldown_seconds,
                default=60,
                minimum=10,
                maximum=600,
            ),
            self._pending_timeout_seconds,
        )
        self._max_attempts = _bounded_int(
            max_attempts, default=5, minimum=1, maximum=10
        )
        self._rate_window_seconds = _bounded_int(
            rate_window_seconds, default=3_600, minimum=60, maximum=86_400
        )
        self._mobile_rate_limit = _bounded_int(
            mobile_rate_limit, default=5, minimum=1, maximum=100
        )
        self._ip_rate_limit = _bounded_int(
            ip_rate_limit, default=20, minimum=1, maximum=1_000
        )
        self._global_hourly_limit = _bounded_int(
            global_hourly_limit,
            default=500,
            minimum=1,
            maximum=1_000_000,
        )
        self._global_daily_limit = _bounded_int(
            global_daily_limit,
            default=2_000,
            minimum=1,
            maximum=10_000_000,
        )
        self._lock_timeout_seconds = _bounded_int(
            lock_timeout_seconds, default=3, minimum=1, maximum=10
        )
        self._aliyun_caller = aliyun_caller
        self._pnvs_send_caller = pnvs_send_caller
        self._pnvs_verify_caller = pnvs_verify_caller
        self._db_factory = db_factory or SystemDbUtils
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._code_factory = code_factory or (
            lambda: f"{secrets.randbelow(1_000_000):06d}"
        )
        self._challenge_id_factory = challenge_id_factory or (
            lambda: f"pvc_{secrets.token_urlsafe(24)}"
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        aliyun_caller: AliyunSmsCaller | None = None,
        pnvs_send_caller: AliyunPnvsSendCaller | None = None,
        pnvs_verify_caller: AliyunPnvsVerifyCaller | None = None,
        db_factory: DbFactory | None = None,
        clock: Clock | None = None,
        code_factory: CodeFactory | None = None,
        challenge_id_factory: ChallengeIdFactory | None = None,
    ) -> PhonePossessionVerificationService:
        source = os.environ if env is None else env
        return cls(
            provider=source.get("FIN_AGENT_PHONE_CHALLENGE_PROVIDER", "disabled"),
            challenge_secret=source.get("FIN_AGENT_PHONE_CHALLENGE_SECRET"),
            mock_enabled=(
                source.get("FIN_AGENT_PHONE_CHALLENGE_MOCK_ENABLED", "").strip()
                == "1"
            ),
            mock_code=source.get(
                "FIN_AGENT_PHONE_CHALLENGE_MOCK_CODE", "123456"
            ),
            aliyun_access_key_id=_first_non_empty(
                source,
                "FIN_AGENT_SMS_ALIYUN_ACCESS_KEY_ID",
                "ALIYUN_ACCESS_KEY_ID",
                "ALIBABA_CLOUD_ACCESS_KEY_ID",
                "AccessKeyID",
            ),
            aliyun_access_key_secret=_first_non_empty(
                source,
                "FIN_AGENT_SMS_ALIYUN_ACCESS_KEY_SECRET",
                "ALIYUN_ACCESS_KEY_SECRET",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                "AccessKeySecret",
            ),
            aliyun_sign_name=source.get("FIN_AGENT_SMS_ALIYUN_SIGN_NAME"),
            aliyun_template_code=source.get(
                "FIN_AGENT_SMS_ALIYUN_TEMPLATE_CODE"
            ),
            aliyun_template_param_key=source.get(
                "FIN_AGENT_SMS_ALIYUN_TEMPLATE_PARAM_KEY", "code"
            ),
            aliyun_endpoint=source.get(
                "FIN_AGENT_SMS_ALIYUN_ENDPOINT", "dysmsapi.aliyuncs.com"
            ),
            aliyun_region_id=source.get(
                "FIN_AGENT_SMS_ALIYUN_REGION_ID", "cn-hangzhou"
            ),
            pnvs_sign_name=source.get("FIN_AGENT_PNVS_SIGN_NAME"),
            pnvs_template_code=source.get(
                "FIN_AGENT_PNVS_TEMPLATE_CODE", "100001"
            ),
            pnvs_scheme_name=source.get(
                "FIN_AGENT_PNVS_SCHEME_NAME", "fin-agent-register"
            ),
            pnvs_endpoint=source.get(
                "FIN_AGENT_PNVS_ENDPOINT", "dypnsapi.aliyuncs.com"
            ),
            connect_timeout_ms=_env_int(
                source, "FIN_AGENT_SMS_CONNECT_TIMEOUT_MS", 3_000
            ),
            read_timeout_ms=_env_int(
                source, "FIN_AGENT_SMS_READ_TIMEOUT_MS", 5_000
            ),
            challenge_ttl_seconds=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_TTL_SECONDS", 600
            ),
            resend_cooldown_seconds=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_RESEND_SECONDS", 60
            ),
            max_attempts=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_MAX_ATTEMPTS", 5
            ),
            rate_window_seconds=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_RATE_WINDOW_SECONDS", 3_600
            ),
            mobile_rate_limit=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_MOBILE_RATE_LIMIT", 5
            ),
            ip_rate_limit=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_IP_RATE_LIMIT", 20
            ),
            global_hourly_limit=_env_int(
                source,
                "FIN_AGENT_PHONE_CHALLENGE_GLOBAL_HOURLY_LIMIT",
                500,
            ),
            global_daily_limit=_env_int(
                source,
                "FIN_AGENT_PHONE_CHALLENGE_GLOBAL_DAILY_LIMIT",
                2_000,
            ),
            pending_timeout_seconds=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_PENDING_SECONDS", 30
            ),
            lock_timeout_seconds=_env_int(
                source, "FIN_AGENT_PHONE_CHALLENGE_LOCK_TIMEOUT_SECONDS", 3
            ),
            aliyun_caller=aliyun_caller,
            pnvs_send_caller=pnvs_send_caller,
            pnvs_verify_caller=pnvs_verify_caller,
            db_factory=db_factory,
            clock=clock,
            code_factory=code_factory,
            challenge_id_factory=challenge_id_factory,
        )

    def status(self) -> dict[str, Any]:
        supported = self.provider in _SUPPORTED_PROVIDERS
        secret_configured = len(self._secret) >= 32
        provider_configured = False
        sdk_available = True

        if self.provider == "disabled":
            provider_configured = True
        elif self.provider == "mock":
            provider_configured = self._mock_enabled and bool(
                _CODE_PATTERN.fullmatch(self._mock_code)
            )
        elif self.provider == "aliyun":
            provider_configured = bool(
                self._aliyun_access_key_id
                and self._aliyun_access_key_secret
                and self._aliyun_sign_name
                and self._aliyun_template_code
            )
            sdk_available = self._aliyun_caller is not None or all(
                _module_available(module_name)
                for module_name in (
                    "alibabacloud_dysmsapi20170525",
                    "alibabacloud_tea_openapi",
                    "alibabacloud_tea_util",
                )
            )
        elif self.provider == "aliyun_pnvs":
            provider_configured = bool(
                self._aliyun_access_key_id
                and self._aliyun_access_key_secret
                and self._pnvs_sign_name
                and self._pnvs_template_code
            )
            sdk_available = (
                self._pnvs_send_caller is not None
                and self._pnvs_verify_caller is not None
            ) or all(
                _module_available(module_name)
                for module_name in (
                    "alibabacloud_dypnsapi20170525",
                    "alibabacloud_tea_openapi",
                    "alibabacloud_tea_util",
                )
            )

        configured = supported and secret_configured and provider_configured
        enabled = (
            self.provider != "disabled"
            and configured
            and sdk_available
        )
        return {
            "provider": self.provider,
            "supported": supported,
            "configured": configured,
            "enabled": enabled,
            "secret_configured": secret_configured,
            "provider_configured": provider_configured,
            "sdk_available": sdk_available,
            "purpose": _PURPOSE,
            "required_user_fields": ["mobile", "verification_code"],
        }

    def schema_ready(self) -> bool:
        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor() as cursor:
                cursor.execute("SHOW TABLES LIKE %s", (CHALLENGE_TABLE,))
                return bool(cursor.fetchone())
        except Exception:
            logger.warning("Phone challenge schema readiness check failed")
            return False
        finally:
            if db is not None:
                db.close_db()

    def cleanup_expired(self, retention_days: int = 30) -> int:
        """Delete one bounded batch of old expired challenges.

        This method is intentionally opt-in for an operations job. Each call
        removes at most 1,000 rows so cleanup never lengthens the registration
        request path or creates an unbounded transaction.
        """

        normalized_retention_days = _bounded_int(
            retention_days,
            default=30,
            minimum=1,
            maximum=3_650,
        )
        cutoff = self._now() - timedelta(days=normalized_retention_days)
        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor() as cursor:
                self._require_schema(cursor)
                cursor.execute(
                    f"""
                    DELETE FROM {CHALLENGE_TABLE}
                    WHERE expires_at < %s
                    ORDER BY expires_at
                    LIMIT 1000
                    """,
                    (cutoff,),
                )
                deleted_count = max(
                    0, int(getattr(cursor, "rowcount", 0) or 0)
                )
                db.conn.commit()
                return deleted_count
        except PhonePossessionVerificationError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Phone challenge cleanup failed type=%s",
                type(exc).__name__,
            )
            raise self._storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def hash_mobile(self, mobile: str) -> str:
        """Return the canonical HMAC used by the challenge/account transaction."""

        normalized_mobile = _validate_mobile(mobile)
        self._require_secret()
        return self._hmac_hex(f"mobile:{normalized_mobile}")

    def request_code(self, mobile: str, remote_addr: str) -> dict[str, Any]:
        self._require_operational()
        normalized_mobile = _validate_mobile(mobile)
        normalized_remote_addr = _validate_remote_addr(remote_addr)
        now = self._now()
        challenge_id = _validate_generated_challenge_id(
            self._challenge_id_factory()
        )
        code = self._issue_code()
        mobile_hash = self._hmac_hex(f"mobile:{normalized_mobile}")
        ip_hash = self._hmac_hex(f"ip:{normalized_remote_addr}")
        code_hash = self._code_hash(
            challenge_id=challenge_id,
            mobile_hash=mobile_hash,
            code=code,
        )
        expires_at = now + timedelta(seconds=self._challenge_ttl_seconds)
        resend_after = now + timedelta(
            seconds=self._resend_cooldown_seconds
        )
        pending_cutoff = now - timedelta(
            seconds=self._pending_timeout_seconds
        )
        rate_cutoff = now - timedelta(seconds=self._rate_window_seconds)
        hourly_budget_cutoff = now - timedelta(hours=1)
        daily_budget_cutoff = now - timedelta(hours=24)
        lock_names = sorted(
            {
                "fin_phone_global_budget:v1",
                f"fin_phone_i:{ip_hash[:32]}",
                f"fin_phone_m:{mobile_hash[:32]}",
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
                        raise PhonePossessionVerificationError(
                            "phone_code_request_busy",
                            "验证码请求正在处理中，请稍后重试。",
                            True,
                            retry_after_seconds=1,
                        )
                    acquired_locks.append(lock_name)

                cursor.execute(
                    f"""
                    SELECT challenge_id, created_at
                    FROM {CHALLENGE_TABLE}
                    WHERE mobile_hash = %s
                      AND send_succeeded_at IS NULL
                      AND send_failed_at IS NULL
                      AND created_at >= %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (mobile_hash, pending_cutoff),
                )
                if cursor.fetchone():
                    raise PhonePossessionVerificationError(
                        "phone_code_request_in_progress",
                        "验证码正在发送，请稍后再试。",
                        True,
                        retry_after_seconds=self._pending_timeout_seconds,
                    )

                cursor.execute(
                    f"""
                    SELECT resend_after
                    FROM {CHALLENGE_TABLE}
                    WHERE mobile_hash = %s
                      AND send_succeeded_at IS NOT NULL
                      AND send_failed_at IS NULL
                      AND consumed_at IS NULL
                      AND expires_at > %s
                      AND resend_after > %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (mobile_hash, now, now),
                )
                cooldown_row = cursor.fetchone()
                if cooldown_row:
                    retry_after = _seconds_until(
                        _row_value(cooldown_row, "resend_after"),
                        now,
                        default=self._resend_cooldown_seconds,
                    )
                    raise PhonePossessionVerificationError(
                        "phone_code_resend_too_soon",
                        f"验证码已发送，请在 {retry_after} 秒后重试。",
                        True,
                        retry_after_seconds=retry_after,
                    )

                self._enforce_global_budget(
                    cursor,
                    cutoff=hourly_budget_cutoff,
                    limit=self._global_hourly_limit,
                    window_seconds=3_600,
                    now=now,
                )
                self._enforce_global_budget(
                    cursor,
                    cutoff=daily_budget_cutoff,
                    limit=self._global_daily_limit,
                    window_seconds=86_400,
                    now=now,
                )
                self._enforce_rate_limit(
                    cursor,
                    column="mobile_hash",
                    value=mobile_hash,
                    cutoff=rate_cutoff,
                    limit=self._mobile_rate_limit,
                    now=now,
                )
                self._enforce_rate_limit(
                    cursor,
                    column="request_ip_hash",
                    value=ip_hash,
                    cutoff=rate_cutoff,
                    limit=self._ip_rate_limit,
                    now=now,
                )

                # Once a replacement code is allowed, older unconsumed codes
                # expire immediately. This keeps "latest SMS wins" deterministic
                # without adding another status enum.
                cursor.execute(
                    f"""
                    UPDATE {CHALLENGE_TABLE}
                    SET expires_at = %s, updated_at = %s
                    WHERE mobile_hash = %s
                      AND consumed_at IS NULL
                      AND expires_at > %s
                    """,
                    (now, now, mobile_hash, now),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {CHALLENGE_TABLE} (
                      challenge_id, mobile_hash, code_hash, purpose,
                      provider, provider_request_id, request_ip_hash,
                      expires_at, resend_after, max_attempts, attempt_count,
                      send_succeeded_at, send_failed_at, verified_at,
                      consumed_at, created_at, updated_at
                    ) VALUES (
                      %s, %s, %s, %s, %s, NULL, %s,
                      %s, %s, %s, 0,
                      NULL, NULL, NULL, NULL, %s, %s
                    )
                    """,
                    (
                        challenge_id,
                        mobile_hash,
                        code_hash,
                        _PURPOSE,
                        self.provider,
                        ip_hash,
                        expires_at,
                        resend_after,
                        self._max_attempts,
                        now,
                        now,
                    ),
                )
                db.conn.commit()
        except PhonePossessionVerificationError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Phone challenge request storage failed type=%s",
                type(exc).__name__,
            )
            raise self._storage_error() from None
        finally:
            if db is not None:
                self._release_locks(db, acquired_locks)
                db.close_db()

        # The committed pending row is now the durable single-flight marker.
        # Release the database connection and advisory locks before entering
        # the external provider's latency domain; the provider must never hold
        # scarce DB resources hostage.
        try:
            provider_request_id = self._send_code(
                normalized_mobile, code, challenge_id
            )
        except PhonePossessionVerificationError:
            self._persist_send_outcome(
                challenge_id=challenge_id,
                succeeded=False,
                provider_request_id=None,
            )
            raise
        self._persist_send_outcome(
            challenge_id=challenge_id,
            succeeded=True,
            provider_request_id=provider_request_id,
        )

        result: dict[str, Any] = {
            "challenge_id": challenge_id,
            "provider": self.provider,
            "expires_at": _iso_utc(expires_at),
            "resend_after": _iso_utc(resend_after),
        }
        if self.provider == "mock":
            result["debug_code"] = code
        return result

    def _persist_send_outcome(
        self,
        *,
        challenge_id: str,
        succeeded: bool,
        provider_request_id: str | None,
    ) -> None:
        """Persist the provider outcome in a new, short DB transaction."""

        db = None
        try:
            db = self._db_factory()
            recorded_at = self._now()
            with db.conn.cursor() as cursor:
                self._require_schema(cursor)
                if succeeded:
                    cursor.execute(
                        f"""
                        UPDATE {CHALLENGE_TABLE}
                        SET provider_request_id = %s,
                            send_succeeded_at = %s,
                            updated_at = %s
                        WHERE challenge_id = %s
                          AND send_succeeded_at IS NULL
                          AND send_failed_at IS NULL
                          AND consumed_at IS NULL
                          AND expires_at > %s
                        """,
                        (
                            provider_request_id,
                            recorded_at,
                            recorded_at,
                            challenge_id,
                            recorded_at,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""
                        UPDATE {CHALLENGE_TABLE}
                        SET send_failed_at = %s, updated_at = %s
                        WHERE challenge_id = %s
                          AND send_succeeded_at IS NULL
                          AND send_failed_at IS NULL
                        """,
                        (recorded_at, recorded_at, challenge_id),
                    )
                if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                    raise self._storage_error()
                db.conn.commit()
        except PhonePossessionVerificationError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Failed to persist SMS provider outcome type=%s",
                type(exc).__name__,
            )
            raise self._storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def verify_code(
        self,
        challenge_id: str,
        mobile: str,
        code: str,
    ) -> PhonePossessionProof:
        self._require_operational()
        normalized_challenge_id = _validate_challenge_id(challenge_id)
        normalized_mobile = _validate_mobile(mobile)
        normalized_code = _validate_code(code)
        mobile_hash = self._hmac_hex(f"mobile:{normalized_mobile}")
        if self.provider == "aliyun_pnvs":
            return self._verify_pnvs_code(
                challenge_id=normalized_challenge_id,
                mobile=normalized_mobile,
                mobile_hash=mobile_hash,
                code=normalized_code,
            )
        candidate_hash = self._code_hash(
            challenge_id=normalized_challenge_id,
            mobile_hash=mobile_hash,
            code=normalized_code,
        )
        now = self._now()

        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                self._require_schema(cursor)
                cursor.execute(
                    f"""
                    SELECT
                      challenge_id, mobile_hash, code_hash, purpose, provider,
                      provider_request_id, expires_at, max_attempts,
                      attempt_count, send_succeeded_at, send_failed_at,
                      verified_at, consumed_at
                    FROM {CHALLENGE_TABLE}
                    WHERE challenge_id = %s AND mobile_hash = %s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (normalized_challenge_id, mobile_hash),
                )
                row = cursor.fetchone()
                if not row or _row_value(row, "purpose") != _PURPOSE:
                    raise PhonePossessionVerificationError(
                        "phone_code_invalid",
                        "验证码无效，请重新获取。",
                        False,
                    )
                if _row_value(row, "consumed_at") is not None:
                    raise PhonePossessionVerificationError(
                        "phone_code_consumed",
                        "该验证码已经使用，请重新获取。",
                        False,
                    )
                if (
                    _row_value(row, "send_succeeded_at") is None
                    or _row_value(row, "send_failed_at") is not None
                ):
                    raise PhonePossessionVerificationError(
                        "phone_code_not_ready",
                        "验证码尚未成功发送，请重新获取。",
                        True,
                    )
                expires_at = _as_datetime(_row_value(row, "expires_at"))
                if expires_at is None or expires_at <= now:
                    raise PhonePossessionVerificationError(
                        "phone_code_expired",
                        "验证码已过期，请重新获取。",
                        False,
                    )
                max_attempts = _coerce_int(
                    _row_value(row, "max_attempts"),
                    default=self._max_attempts,
                )
                attempt_count = _coerce_int(
                    _row_value(row, "attempt_count"), default=0
                )
                if attempt_count >= max_attempts:
                    raise PhonePossessionVerificationError(
                        "phone_code_attempts_exceeded",
                        "验证码尝试次数过多，请重新获取。",
                        False,
                    )
                if not hmac.compare_digest(
                    str(_row_value(row, "code_hash") or ""),
                    candidate_hash,
                ):
                    next_attempt_count = attempt_count + 1
                    cursor.execute(
                        f"""
                        UPDATE {CHALLENGE_TABLE}
                        SET attempt_count = attempt_count + 1,
                            updated_at = %s
                        WHERE challenge_id = %s
                        """,
                        (now, normalized_challenge_id),
                    )
                    db.conn.commit()
                    if next_attempt_count >= max_attempts:
                        raise PhonePossessionVerificationError(
                            "phone_code_attempts_exceeded",
                            "验证码尝试次数过多，请重新获取。",
                            False,
                        )
                    raise PhonePossessionVerificationError(
                        "phone_code_invalid",
                        "验证码不正确，请检查后重试。",
                        False,
                    )

                verified_at = _as_datetime(_row_value(row, "verified_at"))
                if verified_at is None:
                    verified_at = now
                    cursor.execute(
                        f"""
                        UPDATE {CHALLENGE_TABLE}
                        SET verified_at = %s, updated_at = %s
                        WHERE challenge_id = %s
                          AND verified_at IS NULL
                        """,
                        (verified_at, now, normalized_challenge_id),
                    )
                    db.conn.commit()

                return PhonePossessionProof(
                    challenge_id=normalized_challenge_id,
                    mobile_hash=mobile_hash,
                    provider=str(_row_value(row, "provider") or ""),
                    provider_request_id=_optional_text(
                        _row_value(row, "provider_request_id")
                    ),
                    verified_at=_iso_utc(verified_at),
                )
        except PhonePossessionVerificationError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "Phone challenge verification storage failed type=%s",
                type(exc).__name__,
            )
            raise self._storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def _enforce_rate_limit(
        self,
        cursor: Any,
        *,
        column: str,
        value: str,
        cutoff: datetime,
        limit: int,
        now: datetime,
    ) -> None:
        # `column` is selected only from hard-coded call sites, never user input.
        cursor.execute(
            f"""
            SELECT COUNT(*) AS request_count, MIN(created_at) AS first_request_at
            FROM {CHALLENGE_TABLE}
            WHERE {column} = %s AND created_at >= %s
            """,
            (value, cutoff),
        )
        row = cursor.fetchone()
        count = _row_int(row, "request_count")
        if count < limit:
            return
        first_request_at = _as_datetime(
            _row_value(row, "first_request_at")
        )
        retry_after = self._rate_window_seconds
        if first_request_at is not None:
            retry_after = max(
                1,
                math.ceil(
                    (
                        first_request_at
                        + timedelta(seconds=self._rate_window_seconds)
                        - now
                    ).total_seconds()
                ),
            )
        raise PhonePossessionVerificationError(
            "phone_code_rate_limited",
            "验证码请求过于频繁，请稍后再试。",
            True,
            retry_after_seconds=retry_after,
        )

    def _enforce_global_budget(
        self,
        cursor: Any,
        *,
        cutoff: datetime,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> None:
        cursor.execute(
            f"""
            SELECT COUNT(*) AS request_count, MIN(created_at) AS first_request_at
            FROM {CHALLENGE_TABLE}
            WHERE created_at >= %s
            """,
            (cutoff,),
        )
        row = cursor.fetchone()
        count = _row_int(row, "request_count")
        if count < limit:
            return
        first_request_at = _as_datetime(
            _row_value(row, "first_request_at")
        )
        retry_after = window_seconds
        if first_request_at is not None:
            retry_after = max(
                1,
                math.ceil(
                    (
                        first_request_at
                        + timedelta(seconds=window_seconds)
                        - now
                    ).total_seconds()
                ),
            )
        logger.warning(
            "Global SMS challenge budget exhausted "
            "window_seconds=%s limit=%s count=%s",
            window_seconds,
            limit,
            count,
        )
        raise PhonePossessionVerificationError(
            "phone_code_budget_exhausted",
            "验证码发送服务当前繁忙，请稍后再试。",
            True,
            retry_after_seconds=retry_after,
        )

    def _require_schema(self, cursor: Any) -> None:
        cursor.execute("SHOW TABLES LIKE %s", (CHALLENGE_TABLE,))
        if not cursor.fetchone():
            raise PhonePossessionVerificationError(
                "phone_challenge_storage_unavailable",
                "手机号验证服务尚未就绪。",
                True,
            )

    def _require_operational(self) -> None:
        status = self.status()
        if self.provider == "disabled":
            raise PhonePossessionVerificationError(
                "phone_challenge_disabled",
                "短信验证码服务当前未启用。",
                False,
            )
        if self.provider not in _SUPPORTED_PROVIDERS:
            raise PhonePossessionVerificationError(
                "phone_challenge_provider_unsupported",
                "短信验证码服务配置不受支持。",
                False,
            )
        if not status["secret_configured"]:
            raise PhonePossessionVerificationError(
                "phone_challenge_secret_missing",
                "短信验证码服务尚未完成安全配置。",
                False,
            )
        if not status["provider_configured"] or not status["sdk_available"]:
            raise PhonePossessionVerificationError(
                "phone_challenge_not_configured",
                "短信验证码服务尚未配置。",
                False,
            )

    def _require_secret(self) -> None:
        if len(self._secret) < 32:
            raise PhonePossessionVerificationError(
                "phone_challenge_secret_missing",
                "短信验证码服务尚未完成安全配置。",
                False,
            )

    def _issue_code(self) -> str:
        code = self._mock_code if self.provider == "mock" else self._code_factory()
        if not _CODE_PATTERN.fullmatch(str(code or "")):
            raise PhonePossessionVerificationError(
                "phone_challenge_configuration_invalid",
                "短信验证码服务配置无效。",
                False,
            )
        return str(code)

    def _hmac_hex(self, payload: str) -> str:
        return hmac.new(
            self._secret,
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _code_hash(
        self,
        *,
        challenge_id: str,
        mobile_hash: str,
        code: str,
    ) -> str:
        return self._hmac_hex(
            f"code:{challenge_id}:{mobile_hash}:{code}"
        )

    def _send_code(
        self,
        mobile: str,
        code: str,
        challenge_id: str,
    ) -> str | None:
        if self.provider == "mock":
            return f"mock_{challenge_id}"
        if self.provider == "aliyun_pnvs":
            try:
                response = (
                    self._pnvs_send_caller(mobile, challenge_id)
                    if self._pnvs_send_caller is not None
                    else self._call_aliyun_pnvs_send(mobile, challenge_id)
                )
            except PhonePossessionVerificationError:
                raise
            except Exception as exc:
                logger.warning(
                    "Aliyun PNVS send failed type=%s", type(exc).__name__
                )
                raise PhonePossessionVerificationError(
                    "phone_code_send_unavailable",
                    "验证码发送服务暂时不可用，请稍后重试。",
                    True,
                ) from None
            body = _read_value(response, "body") or response
            provider_code = str(_read_value(body, "code", "Code") or "")
            request_id = _optional_text(
                _read_value(body, "request_id", "requestId", "RequestId")
            )
            if provider_code.upper() != "OK":
                logger.warning(
                    "Aliyun PNVS send returned code=%s request_id=%s",
                    provider_code or "missing",
                    request_id or "missing",
                )
                raise PhonePossessionVerificationError(
                    "phone_code_send_unavailable",
                    "验证码发送服务暂时不可用，请稍后重试。",
                    True,
                )
            model = _read_value(body, "model", "Model")
            return _optional_text(
                _read_value(model, "biz_id", "bizId", "BizId")
            ) or request_id
        if self.provider != "aliyun":
            raise PhonePossessionVerificationError(
                "phone_challenge_provider_unsupported",
                "短信验证码服务配置不受支持。",
                False,
            )
        try:
            response = (
                self._aliyun_caller(mobile, code)
                if self._aliyun_caller is not None
                else self._call_aliyun_sdk(mobile, code)
            )
        except PhonePossessionVerificationError:
            raise
        except Exception as exc:
            # Provider exceptions may embed the request (including mobile/code),
            # so intentionally log only the exception type.
            logger.warning(
                "Aliyun SMS request failed type=%s", type(exc).__name__
            )
            raise PhonePossessionVerificationError(
                "phone_code_send_unavailable",
                "验证码发送服务暂时不可用，请稍后重试。",
                True,
            ) from None

        body = _read_value(response, "body") or response
        provider_code = str(_read_value(body, "code", "Code") or "")
        request_id = _optional_text(
            _read_value(body, "request_id", "requestId", "RequestId")
        )
        if provider_code.upper() != "OK":
            logger.warning(
                "Aliyun SMS returned provider error code=%s request_id=%s",
                provider_code or "missing",
                request_id or "missing",
            )
            raise PhonePossessionVerificationError(
                "phone_code_send_unavailable",
                "验证码发送服务暂时不可用，请稍后重试。",
                True,
            )
        return request_id

    def _verify_pnvs_code(
        self,
        *,
        challenge_id: str,
        mobile: str,
        mobile_hash: str,
        code: str,
    ) -> PhonePossessionProof:
        # First establish that the challenge can still be attempted, then close
        # the connection before calling the provider. Registration consumption
        # remains protected by the account service's transaction.
        self._load_verifiable_row(challenge_id, mobile_hash, for_update=False)
        try:
            response = (
                self._pnvs_verify_caller(mobile, code, challenge_id)
                if self._pnvs_verify_caller is not None
                else self._call_aliyun_pnvs_verify(mobile, code, challenge_id)
            )
        except PhonePossessionVerificationError:
            raise
        except Exception as exc:
            logger.warning(
                "Aliyun PNVS verification failed type=%s",
                type(exc).__name__,
            )
            raise PhonePossessionVerificationError(
                "phone_code_verify_unavailable",
                "验证码校验服务暂时不可用，请稍后重试。",
                True,
            ) from None

        body = _read_value(response, "body") or response
        provider_code = str(_read_value(body, "code", "Code") or "")
        request_id = _optional_text(
            _read_value(body, "request_id", "requestId", "RequestId")
        )
        if provider_code.upper() != "OK":
            logger.warning(
                "Aliyun PNVS verify returned code=%s request_id=%s",
                provider_code or "missing",
                request_id or "missing",
            )
            raise PhonePossessionVerificationError(
                "phone_code_verify_unavailable",
                "验证码校验服务暂时不可用，请稍后重试。",
                True,
            )
        model = _read_value(body, "model", "Model")
        verify_result = str(
            _read_value(
                model,
                "verify_result",
                "verifyResult",
                "VerifyResult",
            )
            or ""
        ).upper()

        db = None
        try:
            db = self._db_factory()
            now = self._now()
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                row = self._select_verifiable_row(
                    cursor, challenge_id, mobile_hash, now, for_update=True
                )
                max_attempts = _coerce_int(
                    _row_value(row, "max_attempts"),
                    default=self._max_attempts,
                )
                attempt_count = _coerce_int(
                    _row_value(row, "attempt_count"), default=0
                )
                if verify_result != "PASS":
                    next_attempt_count = attempt_count + 1
                    cursor.execute(
                        f"""
                        UPDATE {CHALLENGE_TABLE}
                        SET attempt_count = attempt_count + 1,
                            updated_at = %s
                        WHERE challenge_id = %s
                        """,
                        (now, challenge_id),
                    )
                    db.conn.commit()
                    if next_attempt_count >= max_attempts:
                        raise PhonePossessionVerificationError(
                            "phone_code_attempts_exceeded",
                            "验证码尝试次数过多，请重新获取。",
                            False,
                        )
                    raise PhonePossessionVerificationError(
                        "phone_code_invalid",
                        "验证码不正确，请检查后重试。",
                        False,
                    )
                verified_at = _as_datetime(_row_value(row, "verified_at"))
                if verified_at is None:
                    verified_at = now
                    cursor.execute(
                        f"""
                        UPDATE {CHALLENGE_TABLE}
                        SET verified_at = %s, updated_at = %s
                        WHERE challenge_id = %s
                          AND verified_at IS NULL
                        """,
                        (verified_at, now, challenge_id),
                    )
                    db.conn.commit()
                return PhonePossessionProof(
                    challenge_id=challenge_id,
                    mobile_hash=mobile_hash,
                    provider=str(_row_value(row, "provider") or ""),
                    provider_request_id=_optional_text(
                        _row_value(row, "provider_request_id")
                    ),
                    verified_at=_iso_utc(verified_at),
                )
        except PhonePossessionVerificationError:
            if db is not None:
                db.conn.rollback()
            raise
        except Exception as exc:
            if db is not None:
                db.conn.rollback()
            logger.warning(
                "PNVS verification storage failed type=%s",
                type(exc).__name__,
            )
            raise self._storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def _load_verifiable_row(
        self,
        challenge_id: str,
        mobile_hash: str,
        *,
        for_update: bool,
    ) -> object:
        db = None
        try:
            db = self._db_factory()
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                return self._select_verifiable_row(
                    cursor,
                    challenge_id,
                    mobile_hash,
                    self._now(),
                    for_update=for_update,
                )
        except PhonePossessionVerificationError:
            raise
        except Exception as exc:
            logger.warning(
                "Phone challenge lookup failed type=%s", type(exc).__name__
            )
            raise self._storage_error() from None
        finally:
            if db is not None:
                db.close_db()

    def _select_verifiable_row(
        self,
        cursor: Any,
        challenge_id: str,
        mobile_hash: str,
        now: datetime,
        *,
        for_update: bool,
    ) -> object:
        self._require_schema(cursor)
        lock_clause = " FOR UPDATE" if for_update else ""
        cursor.execute(
            f"""
            SELECT
              challenge_id, mobile_hash, code_hash, purpose, provider,
              provider_request_id, expires_at, max_attempts,
              attempt_count, send_succeeded_at, send_failed_at,
              verified_at, consumed_at
            FROM {CHALLENGE_TABLE}
            WHERE challenge_id = %s AND mobile_hash = %s
            LIMIT 1{lock_clause}
            """,
            (challenge_id, mobile_hash),
        )
        row = cursor.fetchone()
        if not row or _row_value(row, "purpose") != _PURPOSE:
            raise PhonePossessionVerificationError(
                "phone_code_invalid", "验证码无效，请重新获取。", False
            )
        if _row_value(row, "consumed_at") is not None:
            raise PhonePossessionVerificationError(
                "phone_code_consumed", "该验证码已经使用，请重新获取。", False
            )
        if (
            _row_value(row, "send_succeeded_at") is None
            or _row_value(row, "send_failed_at") is not None
        ):
            raise PhonePossessionVerificationError(
                "phone_code_not_ready",
                "验证码尚未成功发送，请重新获取。",
                True,
            )
        expires_at = _as_datetime(_row_value(row, "expires_at"))
        if expires_at is None or expires_at <= now:
            raise PhonePossessionVerificationError(
                "phone_code_expired", "验证码已过期，请重新获取。", False
            )
        if _coerce_int(
            _row_value(row, "attempt_count"), default=0
        ) >= _coerce_int(
            _row_value(row, "max_attempts"), default=self._max_attempts
        ):
            raise PhonePossessionVerificationError(
                "phone_code_attempts_exceeded",
                "验证码尝试次数过多，请重新获取。",
                False,
            )
        return row

    def _call_aliyun_sdk(self, mobile: str, code: str) -> object:
        from alibabacloud_dysmsapi20170525 import models as sms_models
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_tea_util import models as util_models

        config = open_api_models.Config(
            access_key_id=self._aliyun_access_key_id,
            access_key_secret=self._aliyun_access_key_secret,
        )
        config.endpoint = self._aliyun_endpoint
        config.region_id = self._aliyun_region_id
        client = Client(config)
        request = sms_models.SendSmsRequest(
            phone_numbers=mobile,
            sign_name=self._aliyun_sign_name,
            template_code=self._aliyun_template_code,
            template_param=json.dumps(
                {self._aliyun_template_param_key: code},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        runtime = util_models.RuntimeOptions(
            autoretry=False,
            max_attempts=1,
            connect_timeout=self._connect_timeout_ms,
            read_timeout=self._read_timeout_ms,
        )
        return client.send_sms_with_options(request, runtime)

    def _pnvs_client(self) -> object:
        from alibabacloud_dypnsapi20170525.client import Client
        from alibabacloud_tea_openapi import models as open_api_models

        config = open_api_models.Config(
            access_key_id=self._aliyun_access_key_id,
            access_key_secret=self._aliyun_access_key_secret,
        )
        config.endpoint = self._pnvs_endpoint
        return Client(config)

    def _pnvs_runtime(self) -> object:
        from alibabacloud_tea_util import models as util_models

        return util_models.RuntimeOptions(
            autoretry=False,
            max_attempts=1,
            connect_timeout=self._connect_timeout_ms,
            read_timeout=self._read_timeout_ms,
        )

    def _call_aliyun_pnvs_send(
        self, mobile: str, challenge_id: str
    ) -> object:
        from alibabacloud_dypnsapi20170525 import models as pnvs_models

        request = pnvs_models.SendSmsVerifyCodeRequest(
            phone_number=mobile,
            country_code="86",
            sign_name=self._pnvs_sign_name,
            template_code=self._pnvs_template_code,
            template_param=json.dumps(
                {"code": "##code##", "min": "5"},
                separators=(",", ":"),
            ),
            scheme_name=self._pnvs_scheme_name,
            out_id=challenge_id,
            code_length=6,
            valid_time=self._challenge_ttl_seconds,
            interval=self._resend_cooldown_seconds,
            duplicate_policy=1,
            code_type=1,
            return_verify_code=False,
            auto_retry=1,
        )
        return self._pnvs_client().send_sms_verify_code_with_options(
            request, self._pnvs_runtime()
        )

    def _call_aliyun_pnvs_verify(
        self, mobile: str, code: str, challenge_id: str
    ) -> object:
        from alibabacloud_dypnsapi20170525 import models as pnvs_models

        request = pnvs_models.CheckSmsVerifyCodeRequest(
            phone_number=mobile,
            country_code="86",
            verify_code=code,
            scheme_name=self._pnvs_scheme_name,
            out_id=challenge_id,
            case_auth_policy=2,
        )
        return self._pnvs_client().check_sms_verify_code_with_options(
            request, self._pnvs_runtime()
        )

    def _release_locks(self, db: Any, lock_names: list[str]) -> None:
        if not lock_names:
            return
        try:
            with db.conn.cursor() as cursor:
                for lock_name in reversed(lock_names):
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        except Exception as exc:
            logger.warning(
                "Failed to release phone challenge lock type=%s",
                type(exc).__name__,
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @staticmethod
    def _storage_error() -> PhonePossessionVerificationError:
        return PhonePossessionVerificationError(
            "phone_challenge_storage_unavailable",
            "手机号验证服务暂时不可用，请稍后重试。",
            True,
        )


def _clean(value: object) -> str:
    return str(value or "").strip()


def _first_non_empty(source: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = _clean(source.get(key))
        if value:
            return value
    return None


def _env_int(source: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(_clean(source.get(key)) or default)
    except (TypeError, ValueError):
        return default


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


def _module_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _validate_mobile(mobile: object) -> str:
    value = _clean(mobile)
    if not _MOBILE_PATTERN.fullmatch(value):
        raise PhonePossessionVerificationError(
            "invalid_mobile",
            "请输入有效的中国大陆手机号。",
            False,
        )
    return value


def _validate_remote_addr(remote_addr: object) -> str:
    value = _clean(remote_addr)
    if not value or len(value) > 255:
        raise PhonePossessionVerificationError(
            "invalid_request_context",
            "当前请求无法发送验证码，请刷新后重试。",
            False,
        )
    return value


def _validate_challenge_id(challenge_id: object) -> str:
    value = _clean(challenge_id)
    if not _CHALLENGE_ID_PATTERN.fullmatch(value):
        raise PhonePossessionVerificationError(
            "phone_code_invalid",
            "验证码无效，请重新获取。",
            False,
        )
    return value


def _validate_generated_challenge_id(challenge_id: object) -> str:
    value = _clean(challenge_id)
    if not _CHALLENGE_ID_PATTERN.fullmatch(value):
        raise PhonePossessionVerificationError(
            "phone_challenge_configuration_invalid",
            "短信验证码服务配置无效。",
            False,
        )
    return value


def _validate_code(code: object) -> str:
    value = _clean(code)
    if not _CODE_PATTERN.fullmatch(value):
        raise PhonePossessionVerificationError(
            "phone_code_invalid",
            "请输入 6 位数字验证码。",
            False,
        )
    return value


def _read_value(value: object, *names: str) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _row_value(row: object, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    return None


def _coerce_int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _row_int(row: object, key: str) -> int:
    if isinstance(row, Mapping):
        return _coerce_int(row.get(key), default=0)
    if isinstance(row, (tuple, list)) and row:
        return _coerce_int(row[0], default=0)
    return 0


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def _seconds_until(
    target: object,
    now: datetime,
    *,
    default: int,
) -> int:
    target_datetime = _as_datetime(target)
    if target_datetime is None:
        return max(1, default)
    return max(1, math.ceil((target_datetime - now).total_seconds()))


def _optional_text(value: object) -> str | None:
    text = _clean(value)
    return text or None


def _iso_utc(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds")
