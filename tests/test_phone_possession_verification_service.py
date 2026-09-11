from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from src.services.phone_possession_verification_service import (
    CHALLENGE_TABLE,
    PhonePossessionProof,
    PhonePossessionVerificationError,
    PhonePossessionVerificationService,
)


_NOW = datetime(2026, 7, 30, 12, 0, 0)
_SECRET = "test-only-secret-with-at-least-32-bytes"
_MOBILE = "13800138000"
_IP = "127.0.0.1"


@dataclass
class _Store:
    rows: list[dict[str, Any]] = field(default_factory=list)
    table_exists: bool = True
    locks: set[str] = field(default_factory=set)
    executed: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    connections: list[Any] = field(default_factory=list)
    commit_count: int = 0
    rollback_count: int = 0


class _Cursor:
    def __init__(self, store: _Store):
        self.store = store
        self.result: Any = None
        self.rowcount = 0

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        normalized = " ".join(sql.lower().split())
        self.store.executed.append((normalized, params))
        self.result = None
        self.rowcount = 0

        if normalized.startswith("show tables like"):
            self.result = (CHALLENGE_TABLE,) if self.store.table_exists else None
            return
        if "get_lock(" in normalized:
            lock_name = str(params[0])
            if lock_name in self.store.locks:
                self.result = {"acquired": 0}
            else:
                self.store.locks.add(lock_name)
                self.result = {"acquired": 1}
            return
        if "release_lock(" in normalized:
            self.store.locks.discard(str(params[0]))
            self.result = {"released": 1}
            return
        if normalized.startswith("select challenge_id, created_at"):
            mobile_hash, cutoff = params
            matches = [
                row
                for row in self.store.rows
                if row["mobile_hash"] == mobile_hash
                and row["send_succeeded_at"] is None
                and row["send_failed_at"] is None
                and row["created_at"] >= cutoff
            ]
            self.result = max(matches, key=lambda row: row["created_at"]) if matches else None
            return
        if normalized.startswith("select resend_after"):
            mobile_hash, expires_now, resend_now = params
            matches = [
                row
                for row in self.store.rows
                if row["mobile_hash"] == mobile_hash
                and row["send_succeeded_at"] is not None
                and row["send_failed_at"] is None
                and row["consumed_at"] is None
                and row["expires_at"] > expires_now
                and row["resend_after"] > resend_now
            ]
            self.result = max(matches, key=lambda row: row["created_at"]) if matches else None
            return
        if normalized.startswith("select count(*) as request_count"):
            if "where created_at >=" in normalized:
                (cutoff,) = params
                rows = [
                    row
                    for row in self.store.rows
                    if row["created_at"] >= cutoff
                ]
            else:
                value, cutoff = params
                column = (
                    "request_ip_hash"
                    if "where request_ip_hash =" in normalized
                    else "mobile_hash"
                )
                rows = [
                    row
                    for row in self.store.rows
                    if row[column] == value and row["created_at"] >= cutoff
                ]
            self.result = {
                "request_count": len(rows),
                "first_request_at": min(
                    (row["created_at"] for row in rows),
                    default=None,
                ),
            }
            return
        if normalized.startswith(f"update {CHALLENGE_TABLE} set expires_at"):
            expires_at, updated_at, mobile_hash, now = params
            for row in self.store.rows:
                if (
                    row["mobile_hash"] == mobile_hash
                    and row["consumed_at"] is None
                    and row["expires_at"] > now
                ):
                    row["expires_at"] = expires_at
                    row["updated_at"] = updated_at
                    self.rowcount += 1
            return
        if normalized.startswith(f"insert into {CHALLENGE_TABLE}"):
            (
                challenge_id,
                mobile_hash,
                code_hash,
                purpose,
                provider,
                request_ip_hash,
                expires_at,
                resend_after,
                max_attempts,
                created_at,
                updated_at,
            ) = params
            self.store.rows.append(
                {
                    "challenge_id": challenge_id,
                    "mobile_hash": mobile_hash,
                    "code_hash": code_hash,
                    "purpose": purpose,
                    "provider": provider,
                    "provider_request_id": None,
                    "request_ip_hash": request_ip_hash,
                    "expires_at": expires_at,
                    "resend_after": resend_after,
                    "max_attempts": max_attempts,
                    "attempt_count": 0,
                    "send_succeeded_at": None,
                    "send_failed_at": None,
                    "verified_at": None,
                    "consumed_at": None,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )
            self.rowcount = 1
            return
        if normalized.startswith(f"delete from {CHALLENGE_TABLE}"):
            (cutoff,) = params
            expired = sorted(
                (
                    row
                    for row in self.store.rows
                    if row["expires_at"] < cutoff
                ),
                key=lambda row: row["expires_at"],
            )[:1000]
            expired_ids = {row["challenge_id"] for row in expired}
            self.store.rows[:] = [
                row
                for row in self.store.rows
                if row["challenge_id"] not in expired_ids
            ]
            self.rowcount = len(expired_ids)
            return
        if "set send_failed_at =" in normalized:
            failed_at, updated_at, challenge_id = params
            row = next(
                (
                    item
                    for item in self.store.rows
                    if item["challenge_id"] == challenge_id
                ),
                None,
            )
            if row is None:
                return
            if row["send_succeeded_at"] is None:
                row["send_failed_at"] = failed_at
                row["updated_at"] = updated_at
                self.rowcount = 1
            return
        if "set provider_request_id =" in normalized:
            request_id, sent_at, updated_at, challenge_id, valid_at = params
            row = next(
                (
                    item
                    for item in self.store.rows
                    if item["challenge_id"] == challenge_id
                ),
                None,
            )
            if row is None:
                return
            if (
                row["send_succeeded_at"] is None
                and row["send_failed_at"] is None
                and row["consumed_at"] is None
                and row["expires_at"] > valid_at
            ):
                row["provider_request_id"] = request_id
                row["send_succeeded_at"] = sent_at
                row["updated_at"] = updated_at
                self.rowcount = 1
            return
        if normalized.startswith("select challenge_id, mobile_hash, code_hash"):
            challenge_id, mobile_hash = params
            self.result = next(
                (
                    row
                    for row in self.store.rows
                    if row["challenge_id"] == challenge_id
                    and row["mobile_hash"] == mobile_hash
                ),
                None,
            )
            return
        if "set attempt_count = attempt_count + 1" in normalized:
            updated_at, challenge_id = params
            row = self._row(challenge_id)
            row["attempt_count"] += 1
            row["updated_at"] = updated_at
            self.rowcount = 1
            return
        if "set verified_at =" in normalized:
            verified_at, updated_at, challenge_id = params
            row = self._row(challenge_id)
            if row["verified_at"] is None:
                row["verified_at"] = verified_at
                row["updated_at"] = updated_at
                self.rowcount = 1
            return
        raise AssertionError(f"Unexpected SQL in fake DB: {normalized}")

    def fetchone(self) -> Any:
        return self.result

    def _row(self, challenge_id: str) -> dict[str, Any]:
        return next(
            row
            for row in self.store.rows
            if row["challenge_id"] == challenge_id
        )


class _Connection:
    def __init__(self, store: _Store):
        self.store = store
        self.closed = False

    def cursor(self, *_args: object) -> _Cursor:
        return _Cursor(self.store)

    def commit(self) -> None:
        self.store.commit_count += 1

    def rollback(self) -> None:
        self.store.rollback_count += 1

    def close(self) -> None:
        self.closed = True


class _Db:
    def __init__(self, store: _Store):
        self.conn = _Connection(store)
        store.connections.append(self.conn)

    def close_db(self) -> None:
        self.conn.close()


def _service(
    store: _Store,
    *,
    provider: str = "mock",
    mock_enabled: bool = True,
    mock_code: str = "654321",
    aliyun_caller=None,
    pnvs_send_caller=None,
    pnvs_verify_caller=None,
    mobile_rate_limit: int = 5,
    ip_rate_limit: int = 20,
    global_hourly_limit: int = 500,
    global_daily_limit: int = 2_000,
) -> PhonePossessionVerificationService:
    return PhonePossessionVerificationService(
        provider=provider,
        challenge_secret=_SECRET,
        mock_enabled=mock_enabled,
        mock_code=mock_code,
        aliyun_access_key_id="test-id",
        aliyun_access_key_secret="test-secret",
        aliyun_sign_name="测试签名",
        aliyun_template_code="SMS_TEST",
        aliyun_caller=aliyun_caller,
        pnvs_sign_name="系统验证码",
        pnvs_template_code="100001",
        pnvs_send_caller=pnvs_send_caller,
        pnvs_verify_caller=pnvs_verify_caller,
        db_factory=lambda: _Db(store),
        clock=lambda: _NOW,
        code_factory=lambda: "654321",
        challenge_id_factory=lambda: f"pvc_test_{len(store.rows) + 1}",
        mobile_rate_limit=mobile_rate_limit,
        ip_rate_limit=ip_rate_limit,
        global_hourly_limit=global_hourly_limit,
        global_daily_limit=global_daily_limit,
    )


def test_from_env_is_disabled_by_default_and_supports_existing_ak_names() -> None:
    default_service = PhonePossessionVerificationService.from_env({})
    assert default_service.status()["provider"] == "disabled"
    assert default_service.status()["enabled"] is False

    service = PhonePossessionVerificationService.from_env(
        {
            "FIN_AGENT_PHONE_CHALLENGE_PROVIDER": "aliyun",
            "FIN_AGENT_PHONE_CHALLENGE_SECRET": _SECRET,
            "AccessKeyID": "compatible-id",
            "AccessKeySecret": "compatible-secret",
            "FIN_AGENT_SMS_ALIYUN_SIGN_NAME": "测试签名",
            "FIN_AGENT_SMS_ALIYUN_TEMPLATE_CODE": "SMS_TEST",
            "FIN_AGENT_PHONE_CHALLENGE_GLOBAL_HOURLY_LIMIT": "321",
            "FIN_AGENT_PHONE_CHALLENGE_GLOBAL_DAILY_LIMIT": "1234",
        },
        aliyun_caller=lambda _mobile, _code: {"body": {"code": "OK"}},
    )
    status = service.status()
    assert status["configured"] is True
    assert status["enabled"] is True
    assert status["purpose"] == "registration"
    assert service._global_hourly_limit == 321  # noqa: SLF001
    assert service._global_daily_limit == 1234  # noqa: SLF001

    pnvs_service = PhonePossessionVerificationService.from_env(
        {
            "FIN_AGENT_PHONE_CHALLENGE_PROVIDER": "aliyun_pnvs",
            "FIN_AGENT_PHONE_CHALLENGE_SECRET": _SECRET,
            "AccessKeyID": "compatible-id",
            "AccessKeySecret": "compatible-secret",
            "FIN_AGENT_PNVS_SIGN_NAME": "系统验证码",
        },
        pnvs_send_caller=lambda _mobile, _challenge_id: {},
        pnvs_verify_caller=lambda _mobile, _code, _challenge_id: {},
    )
    assert pnvs_service.status()["enabled"] is True
    assert pnvs_service.status()["required_user_fields"] == [
        "mobile",
        "verification_code",
    ]


def test_secret_and_mock_both_require_explicit_configuration() -> None:
    missing_secret = PhonePossessionVerificationService(
        provider="mock",
        mock_enabled=True,
    )
    with pytest.raises(PhonePossessionVerificationError) as captured:
        missing_secret.request_code(_MOBILE, _IP)
    assert captured.value.code == "phone_challenge_secret_missing"

    disabled_mock = PhonePossessionVerificationService(
        provider="mock",
        challenge_secret=_SECRET,
        mock_enabled=False,
    )
    with pytest.raises(PhonePossessionVerificationError) as captured:
        disabled_mock.request_code(_MOBILE, _IP)
    assert captured.value.code == "phone_challenge_not_configured"


def test_pending_lease_covers_provider_timeouts_and_preserves_challenge_ttl() -> None:
    service = PhonePossessionVerificationService(
        provider="mock",
        challenge_secret=_SECRET,
        mock_enabled=True,
        connect_timeout_ms=30_000,
        read_timeout_ms=30_000,
        pending_timeout_seconds=5,
        resend_cooldown_seconds=10,
        challenge_ttl_seconds=60,
    )

    assert service._pending_timeout_seconds == 70  # noqa: SLF001
    assert service._resend_cooldown_seconds == 70  # noqa: SLF001
    assert service._challenge_ttl_seconds == 130  # noqa: SLF001


def test_schema_ready_uses_system_schema_without_provider_call() -> None:
    store = _Store(table_exists=True)
    assert _service(store).schema_ready() is True

    store.table_exists = False
    assert _service(store).schema_ready() is False


def test_cleanup_expired_is_explicit_bounded_storage_work() -> None:
    store = _Store()
    service = _service(store)
    mobile_hash = service.hash_mobile(_MOBILE)
    store.rows.extend(
        [
            _seed_row(
                challenge_id="pvc_delete",
                mobile_hash=mobile_hash,
                ip_hash="old-ip",
                created_at=_NOW - timedelta(days=40),
                expires_at=_NOW - timedelta(days=31),
            ),
            _seed_row(
                challenge_id="pvc_retain_recent",
                mobile_hash="other-mobile",
                ip_hash="recent-ip",
                created_at=_NOW - timedelta(days=35),
                expires_at=_NOW - timedelta(days=29),
            ),
            _seed_row(
                challenge_id="pvc_retain_active",
                mobile_hash="active-mobile",
                ip_hash="active-ip",
                expires_at=_NOW + timedelta(minutes=5),
            ),
        ]
    )

    deleted = service.cleanup_expired(retention_days=30)

    assert deleted == 1
    assert {row["challenge_id"] for row in store.rows} == {
        "pvc_retain_recent",
        "pvc_retain_active",
    }
    assert all(connection.closed for connection in store.connections)
    assert any(
        sql.startswith(f"delete from {CHALLENGE_TABLE}")
        and "limit 1000" in sql
        for sql, _ in store.executed
    )


def test_request_persists_only_hmacs_and_returns_mock_code() -> None:
    store = _Store()
    service = _service(store)

    result = service.request_code(_MOBILE, _IP)

    assert result == {
        "challenge_id": "pvc_test_1",
        "provider": "mock",
        "expires_at": "2026-07-30T12:10:00+00:00",
        "resend_after": "2026-07-30T12:01:00+00:00",
        "debug_code": "654321",
    }
    row = store.rows[0]
    assert row["purpose"] == "registration"
    assert row["mobile_hash"] == service.hash_mobile(_MOBILE)
    assert len(row["mobile_hash"]) == 64
    assert len(row["request_ip_hash"]) == 64
    assert len(row["code_hash"]) == 64
    assert _MOBILE not in repr(row)
    assert "654321" not in repr(row)
    assert row["send_succeeded_at"] == _NOW
    assert row["consumed_at"] is None
    assert store.locks == set()


def test_single_flight_and_cooldown_rejection_are_database_backed() -> None:
    store = _Store()
    service = _service(store)
    mobile_hash = service.hash_mobile(_MOBILE)
    store.rows.append(
        _seed_row(
            challenge_id="pvc_pending",
            mobile_hash=mobile_hash,
            ip_hash="ip-hash",
            send_succeeded_at=None,
        )
    )

    with pytest.raises(PhonePossessionVerificationError) as captured:
        service.request_code(_MOBILE, _IP)
    assert captured.value.code == "phone_code_request_in_progress"

    store.rows[0]["send_succeeded_at"] = _NOW
    for remote_addr in (_IP, "203.0.113.19"):
        with pytest.raises(PhonePossessionVerificationError) as cooldown:
            service.request_code(_MOBILE, remote_addr)
        assert cooldown.value.code == "phone_code_resend_too_soon"
        assert cooldown.value.retry_after_seconds == 60
        assert "pvc_pending" not in str(cooldown.value)
    assert len(store.rows) == 1
    assert any("get_lock(" in sql for sql, _ in store.executed)
    assert any(
        params and params[0] == "fin_phone_global_budget:v1"
        for sql, params in store.executed
        if "get_lock(" in sql
    )


@pytest.mark.parametrize(
    ("dimension", "limit"),
    [("mobile", 2), ("ip", 2)],
)
def test_mobile_and_ip_rate_limits_count_shared_database_rows(
    dimension: str,
    limit: int,
) -> None:
    store = _Store()
    service = _service(
        store,
        mobile_rate_limit=limit if dimension == "mobile" else 10,
        ip_rate_limit=limit if dimension == "ip" else 10,
    )
    mobile_hash = service.hash_mobile(_MOBILE)
    ip_hash = service._hmac_hex(f"ip:{_IP}")  # noqa: SLF001 - contract test
    for index in range(limit):
        row = _seed_row(
            challenge_id=f"pvc_old_{index}",
            mobile_hash=mobile_hash if dimension == "mobile" else f"other-{index}",
            ip_hash=ip_hash if dimension == "ip" else f"other-ip-{index}",
            created_at=_NOW - timedelta(minutes=10 + index),
            resend_after=_NOW - timedelta(seconds=1),
            expires_at=_NOW - timedelta(seconds=1),
            send_succeeded_at=_NOW - timedelta(minutes=10 + index),
        )
        store.rows.append(row)

    with pytest.raises(PhonePossessionVerificationError) as captured:
        service.request_code(_MOBILE, _IP)

    assert captured.value.code == "phone_code_rate_limited"
    assert captured.value.retryable is True
    assert len(store.rows) == limit


@pytest.mark.parametrize(
    ("window", "created_delta", "expected_retry_after"),
    [
        ("hourly", timedelta(minutes=20), 2_400),
        ("daily", timedelta(hours=2), 79_200),
    ],
)
def test_global_database_budget_stops_distributed_source_rotation(
    window: str,
    created_delta: timedelta,
    expected_retry_after: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _Store()
    service = _service(
        store,
        global_hourly_limit=2 if window == "hourly" else 100,
        global_daily_limit=2,
    )
    for index in range(2):
        created_at = _NOW - created_delta + timedelta(seconds=index)
        store.rows.append(
            _seed_row(
                challenge_id=f"pvc_distributed_{index}",
                mobile_hash=f"rotated-mobile-{index}",
                ip_hash=f"rotated-ip-{index}",
                created_at=created_at,
                resend_after=_NOW - timedelta(seconds=1),
                expires_at=_NOW - timedelta(seconds=1),
                send_succeeded_at=created_at,
            )
        )

    with caplog.at_level("WARNING"):
        with pytest.raises(PhonePossessionVerificationError) as captured:
            service.request_code(_MOBILE, _IP)

    assert captured.value.code == "phone_code_budget_exhausted"
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == expected_retry_after
    assert "Global SMS challenge budget exhausted" in caplog.text
    assert _MOBILE not in caplog.text
    assert len(store.rows) == 2


def test_verify_marks_verified_but_never_consumes_and_is_idempotent() -> None:
    store = _Store()
    service = _service(store)
    challenge = service.request_code(_MOBILE, _IP)

    proof = service.verify_code(
        challenge["challenge_id"],
        _MOBILE,
        challenge["debug_code"],
    )
    repeated = service.verify_code(
        challenge["challenge_id"],
        _MOBILE,
        challenge["debug_code"],
    )

    assert isinstance(proof, PhonePossessionProof)
    assert proof == repeated
    assert proof.challenge_id == "pvc_test_1"
    assert proof.mobile_hash == service.hash_mobile(_MOBILE)
    assert proof.verified_at == "2026-07-30T12:00:00+00:00"
    assert store.rows[0]["verified_at"] == _NOW
    assert store.rows[0]["consumed_at"] is None


def test_wrong_code_is_counted_atomically_and_locks_after_max_attempts() -> None:
    store = _Store()
    service = PhonePossessionVerificationService(
        provider="mock",
        challenge_secret=_SECRET,
        mock_enabled=True,
        mock_code="654321",
        max_attempts=2,
        db_factory=lambda: _Db(store),
        clock=lambda: _NOW,
        challenge_id_factory=lambda: "pvc_attempts",
    )
    service.request_code(_MOBILE, _IP)

    with pytest.raises(PhonePossessionVerificationError) as first:
        service.verify_code("pvc_attempts", _MOBILE, "000000")
    assert first.value.code == "phone_code_invalid"
    assert store.rows[0]["attempt_count"] == 1

    with pytest.raises(PhonePossessionVerificationError) as second:
        service.verify_code("pvc_attempts", _MOBILE, "111111")
    assert second.value.code == "phone_code_attempts_exceeded"
    assert store.rows[0]["attempt_count"] == 2

    with pytest.raises(PhonePossessionVerificationError) as correct_too_late:
        service.verify_code("pvc_attempts", _MOBILE, "654321")
    assert correct_too_late.value.code == "phone_code_attempts_exceeded"
    assert store.rows[0]["verified_at"] is None


def test_expired_consumed_and_unsent_challenges_have_stable_errors() -> None:
    scenarios = [
        ("expired", {"expires_at": _NOW}, "phone_code_expired"),
        ("consumed", {"consumed_at": _NOW}, "phone_code_consumed"),
        ("unsent", {"send_succeeded_at": None}, "phone_code_not_ready"),
    ]
    for name, changes, expected_code in scenarios:
        store = _Store()
        service = _service(store)
        challenge = service.request_code(_MOBILE, _IP)
        store.rows[0].update(changes)

        with pytest.raises(PhonePossessionVerificationError) as captured:
            service.verify_code(
                challenge["challenge_id"],
                _MOBILE,
                challenge["debug_code"],
            )
        assert captured.value.code == expected_code, name


def test_aliyun_success_and_failure_never_expose_provider_details() -> None:
    calls: list[tuple[str, str]] = []
    provider_observations: list[tuple[bool, bool]] = []
    store = _Store()

    def caller(mobile: str, code: str) -> dict[str, Any]:
        calls.append((mobile, code))
        provider_observations.append(
            (
                store.locks == set(),
                bool(store.connections)
                and all(connection.closed for connection in store.connections),
            )
        )
        return {
            "body": {
                "code": "OK",
                "message": "private provider detail",
                "request_id": "aliyun-request-1",
            }
        }

    service = _service(store, provider="aliyun", aliyun_caller=caller)
    result = service.request_code(_MOBILE, _IP)
    assert calls == [(_MOBILE, "654321")]
    assert provider_observations == [(True, True)]
    assert len(store.connections) == 2
    assert all(connection.closed for connection in store.connections)
    assert "debug_code" not in result
    assert store.rows[0]["provider_request_id"] == "aliyun-request-1"

    failed_store = _Store()
    failed = _service(
        failed_store,
        provider="aliyun",
        aliyun_caller=lambda _mobile, _code: {
            "body": {
                "code": "isv.BUSINESS_LIMIT_CONTROL",
                "message": "private provider diagnostic",
            }
        },
    )
    with pytest.raises(PhonePossessionVerificationError) as captured:
        failed.request_code(_MOBILE, _IP)
    assert captured.value.code == "phone_code_send_unavailable"
    assert "private" not in str(captured.value)
    assert failed_store.rows[0]["send_failed_at"] == _NOW


def test_pnvs_owns_code_and_verifies_without_holding_database_resources() -> None:
    store = _Store()
    send_calls: list[tuple[str, str]] = []
    verify_calls: list[tuple[str, str, str]] = []
    observations: list[bool] = []

    def send(mobile: str, challenge_id: str) -> dict[str, Any]:
        send_calls.append((mobile, challenge_id))
        observations.append(
            bool(store.connections)
            and all(connection.closed for connection in store.connections)
        )
        return {
            "body": {
                "code": "OK",
                "request_id": "pnvs-send-request",
                "model": {"biz_id": "pnvs-biz-id"},
            }
        }

    def verify(
        mobile: str, code: str, challenge_id: str
    ) -> dict[str, Any]:
        verify_calls.append((mobile, code, challenge_id))
        observations.append(
            bool(store.connections)
            and all(connection.closed for connection in store.connections)
        )
        return {
            "body": {
                "code": "OK",
                "request_id": "pnvs-check-request",
                "model": {"verify_result": "PASS"},
            }
        }

    service = _service(
        store,
        provider="aliyun_pnvs",
        pnvs_send_caller=send,
        pnvs_verify_caller=verify,
    )
    challenge = service.request_code(_MOBILE, _IP)
    assert "debug_code" not in challenge
    assert send_calls == [(_MOBILE, "pvc_test_1")]
    assert store.rows[0]["provider_request_id"] == "pnvs-biz-id"

    proof = service.verify_code("pvc_test_1", _MOBILE, "123456")
    assert proof.provider == "aliyun_pnvs"
    assert verify_calls == [(_MOBILE, "123456", "pvc_test_1")]
    assert observations == [True, True]
    assert store.rows[0]["verified_at"] == _NOW
    assert all(connection.closed for connection in store.connections)


def test_pnvs_rejection_counts_attempt_but_provider_failure_does_not() -> None:
    store = _Store()
    service = _service(
        store,
        provider="aliyun_pnvs",
        pnvs_send_caller=lambda _mobile, _challenge_id: {
            "body": {"code": "OK", "model": {"biz_id": "biz"}}
        },
        pnvs_verify_caller=lambda _mobile, _code, _challenge_id: {
            "body": {"code": "OK", "model": {"verify_result": "UNKNOWN"}}
        },
    )
    service.request_code(_MOBILE, _IP)
    with pytest.raises(PhonePossessionVerificationError) as rejected:
        service.verify_code("pvc_test_1", _MOBILE, "000000")
    assert rejected.value.code == "phone_code_invalid"
    assert store.rows[0]["attempt_count"] == 1

    service._pnvs_verify_caller = (  # noqa: SLF001 - provider contract test
        lambda _mobile, _code, _challenge_id: {
            "body": {
                "code": "ACCESS_DENIED",
                "message": "private provider details",
            }
        }
    )
    with pytest.raises(PhonePossessionVerificationError) as unavailable:
        service.verify_code("pvc_test_1", _MOBILE, "111111")
    assert unavailable.value.code == "phone_code_verify_unavailable"
    assert "private" not in str(unavailable.value)
    assert store.rows[0]["attempt_count"] == 1


def test_retry_during_cooldown_never_discloses_challenge_or_resends() -> None:
    calls: list[tuple[str, str]] = []
    store = _Store()

    def caller(mobile: str, code: str) -> dict[str, Any]:
        calls.append((mobile, code))
        return {"body": {"code": "OK", "request_id": "aliyun-once"}}

    service = _service(store, provider="aliyun", aliyun_caller=caller)
    first = service.request_code(_MOBILE, _IP)
    for remote_addr in (_IP, "198.51.100.27"):
        with pytest.raises(PhonePossessionVerificationError) as captured:
            service.request_code(_MOBILE, remote_addr)
        assert captured.value.code == "phone_code_resend_too_soon"
        assert captured.value.retry_after_seconds == 60
        assert first["challenge_id"] not in str(captured.value)

    assert calls == [(_MOBILE, "654321")]
    assert len(store.rows) == 1
    global_budget_queries = [
        sql
        for sql, _ in store.executed
        if sql.startswith("select count(*) as request_count")
        and "where created_at >=" in sql
    ]
    # Only the one request that entered the SMS send path consumes/checks the
    # send budgets; cooldown retries are rejected before any provider work.
    assert len(global_budget_queries) == 2


def test_provider_success_cannot_return_an_expired_or_missing_challenge() -> None:
    for mode in ("expired", "missing"):
        store = _Store()

        def caller(_mobile: str, _code: str, *, current_mode: str = mode):
            if current_mode == "expired":
                store.rows[0]["expires_at"] = _NOW
            else:
                store.rows.clear()
            return {"body": {"code": "OK", "request_id": "late-success"}}

        service = _service(
            store,
            provider="aliyun",
            aliyun_caller=caller,
        )
        with pytest.raises(PhonePossessionVerificationError) as captured:
            service.request_code(_MOBILE, _IP)

        assert captured.value.code == "phone_challenge_storage_unavailable", mode


def test_validation_rejects_noncanonical_mobile_code_and_request_context() -> None:
    service = _service(_Store())
    with pytest.raises(PhonePossessionVerificationError) as mobile:
        service.request_code("+86 13800138000", _IP)
    assert mobile.value.code == "invalid_mobile"

    with pytest.raises(PhonePossessionVerificationError) as remote:
        service.request_code(_MOBILE, "")
    assert remote.value.code == "invalid_request_context"

    with pytest.raises(PhonePossessionVerificationError) as code:
        service.verify_code("pvc_valid", _MOBILE, "１２３４５６")
    assert code.value.code == "phone_code_invalid"


def _seed_row(
    *,
    challenge_id: str,
    mobile_hash: str,
    ip_hash: str,
    created_at: datetime = _NOW,
    resend_after: datetime = _NOW + timedelta(seconds=60),
    expires_at: datetime = _NOW + timedelta(minutes=10),
    send_succeeded_at: datetime | None = _NOW,
) -> dict[str, Any]:
    return {
        "challenge_id": challenge_id,
        "mobile_hash": mobile_hash,
        "code_hash": "0" * 64,
        "purpose": "registration",
        "provider": "mock",
        "provider_request_id": None,
        "request_ip_hash": ip_hash,
        "expires_at": expires_at,
        "resend_after": resend_after,
        "max_attempts": 5,
        "attempt_count": 0,
        "send_succeeded_at": send_succeeded_at,
        "send_failed_at": None,
        "verified_at": None,
        "consumed_at": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
