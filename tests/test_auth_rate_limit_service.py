from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.services.auth_rate_limit_service import (
    AUTH_ATTEMPT_TABLE,
    AuthRateLimitError,
    AuthRateLimitService,
)


_SECRET = "test-auth-rate-secret-that-is-at-least-32-bytes"
_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
_MOBILE = "13800138000"
_IP = "203.0.113.9"


class _Store:
    def __init__(self, *, table_exists: bool = True) -> None:
        self.table_exists = table_exists
        self.rows: list[dict[str, Any]] = []
        self.locks: set[str] = set()
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.connections: list[_Connection] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.fail_insert = False
        self.fail_delete = False


class _Cursor:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.result: Any = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        normalized = " ".join(sql.lower().split())
        self.store.executed.append((normalized, tuple(params)))
        if normalized.startswith("show tables like"):
            self.result = (AUTH_ATTEMPT_TABLE,) if self.store.table_exists else None
            return 1 if self.result else 0
        if "get_lock(" in normalized:
            lock_name = str(params[0])
            acquired = lock_name not in self.store.locks
            if acquired:
                self.store.locks.add(lock_name)
            self.result = {"acquired": 1 if acquired else 0}
            return 1
        if "release_lock(" in normalized:
            lock_name = str(params[0])
            released = lock_name in self.store.locks
            self.store.locks.discard(lock_name)
            self.result = (1 if released else 0,)
            return 1
        if normalized.startswith("select count(*) as failed_count"):
            action, digest, cutoff = params
            dimension = (
                "subject_hash"
                if "and subject_hash = %s" in normalized
                else "remote_addr_hash"
            )
            matching = [
                row
                for row in self.store.rows
                if row["action"] == action
                and row[dimension] == digest
                and row["succeeded_at"] is None
                and row["created_at"] >= cutoff
            ]
            self.result = {
                "failed_count": len(matching),
                "first_failed_at": (
                    min(row["created_at"] for row in matching)
                    if matching
                    else None
                ),
            }
            return 1
        if normalized.startswith("select count(*) as request_count"):
            if "and remote_addr_hash = %s" in normalized:
                action, digest, cutoff = params
                matching = [
                    row
                    for row in self.store.rows
                    if row["action"] == action
                    and row["remote_addr_hash"] == digest
                    and row["created_at"] >= cutoff
                ]
            else:
                action, cutoff = params
                matching = [
                    row
                    for row in self.store.rows
                    if row["action"] == action
                    and row["created_at"] >= cutoff
                ]
            self.result = {
                "request_count": len(matching),
                "first_request_at": (
                    min(row["created_at"] for row in matching)
                    if matching
                    else None
                ),
            }
            return 1
        if normalized.startswith(f"insert into {AUTH_ATTEMPT_TABLE}"):
            if self.store.fail_insert:
                raise RuntimeError("simulated insert failure")
            (
                attempt_id,
                action,
                subject_hash,
                remote_addr_hash,
                created_at,
            ) = params
            self.store.rows.append(
                {
                    "attempt_id": attempt_id,
                    "action": action,
                    "subject_hash": subject_hash,
                    "remote_addr_hash": remote_addr_hash,
                    "created_at": created_at,
                    "succeeded_at": None,
                }
            )
            return 1
        if normalized.startswith(f"update {AUTH_ATTEMPT_TABLE} set succeeded_at"):
            succeeded_at, attempt_id = params
            changed = 0
            for row in self.store.rows:
                if row["attempt_id"] == attempt_id and row["succeeded_at"] is None:
                    row["succeeded_at"] = succeeded_at
                    changed += 1
            return changed
        if normalized.startswith(f"delete from {AUTH_ATTEMPT_TABLE}"):
            if self.store.fail_delete:
                raise RuntimeError("simulated delete failure")
            cutoff, limit = params
            candidates = sorted(
                (
                    row
                    for row in self.store.rows
                    if row["created_at"] < cutoff
                ),
                key=lambda row: row["created_at"],
            )[: int(limit)]
            deleted_ids = {row["attempt_id"] for row in candidates}
            self.store.rows = [
                row
                for row in self.store.rows
                if row["attempt_id"] not in deleted_ids
            ]
            return len(candidates)
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self) -> Any:
        return self.result


class _Connection:
    def __init__(self, store: _Store) -> None:
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
    def __init__(self, store: _Store) -> None:
        self.conn = _Connection(store)
        store.connections.append(self.conn)

    def close_db(self) -> None:
        self.conn.close()


def _service(
    store: _Store,
    *,
    mobile_limit: int = 8,
    ip_limit: int = 30,
    request_ip_limit: int = 20,
    request_global_limit: int = 120,
    request_window_seconds: int = 60,
    attempt_ids: list[str] | None = None,
) -> AuthRateLimitService:
    ids = attempt_ids or [
        f"ara_test_attempt_{index:04d}" for index in range(1, 100)
    ]
    iterator = iter(ids)
    return AuthRateLimitService(
        secret=_SECRET,
        mobile_limit=mobile_limit,
        ip_limit=ip_limit,
        request_ip_limit=request_ip_limit,
        request_global_limit=request_global_limit,
        request_window_seconds=request_window_seconds,
        db_factory=lambda: _Db(store),
        clock=lambda: _NOW,
        attempt_id_factory=lambda: next(iterator),
    )


def test_from_env_defaults_and_secret_fallback() -> None:
    missing = AuthRateLimitService.from_env({})
    assert missing.status()["enabled"] is False

    fallback = AuthRateLimitService.from_env(
        {"FIN_AGENT_PHONE_CHALLENGE_SECRET": _SECRET}
    )
    status = fallback.status()
    assert status["enabled"] is True
    assert status["login"] == {
        "mobile_limit": 8,
        "ip_limit": 30,
        "window_seconds": 600,
    }
    assert status["request_budget"] == {
        "ip_limit": 20,
        "global_limit": 120,
        "window_seconds": 60,
    }

    explicit_short_secret = AuthRateLimitService.from_env(
        {
            "FIN_AGENT_AUTH_RATE_SECRET": "short",
            "FIN_AGENT_PHONE_CHALLENGE_SECRET": _SECRET,
        }
    )
    assert explicit_short_secret.status()["enabled"] is False


def test_schema_ready_is_read_only_and_handles_missing_table() -> None:
    store = _Store(table_exists=True)
    assert _service(store).schema_ready() is True
    assert store.rows == []

    store.table_exists = False
    assert _service(store).schema_ready() is False


def test_begin_attempt_persists_only_hmacs_and_releases_resources() -> None:
    store = _Store()
    attempt_id = _service(store).begin_attempt("login", _MOBILE, _IP)

    assert attempt_id == "ara_test_attempt_0001"
    assert len(store.rows) == 1
    assert store.rows[0]["action"] == "login"
    assert len(store.rows[0]["subject_hash"]) == 64
    assert len(store.rows[0]["remote_addr_hash"]) == 64
    assert _MOBILE not in repr(store.rows)
    assert _IP not in repr(store.rows)
    assert _MOBILE not in repr(store.executed)
    assert _IP not in repr(store.executed)
    assert store.locks == set()
    assert all(connection.closed for connection in store.connections)


@pytest.mark.parametrize(
    ("dimension", "mobile_limit", "ip_limit"),
    [
        ("mobile", 2, 30),
        ("ip", 8, 2),
    ],
)
def test_failed_attempt_limits_are_shared_database_rows(
    dimension: str,
    mobile_limit: int,
    ip_limit: int,
) -> None:
    store = _Store()
    service = _service(
        store,
        mobile_limit=mobile_limit,
        ip_limit=ip_limit,
    )
    limit = mobile_limit if dimension == "mobile" else ip_limit
    for index in range(limit):
        subject = _MOBILE if dimension == "mobile" else f"13900138{index:03d}"
        remote_addr = _IP if dimension == "ip" else f"203.0.113.{index + 10}"
        service.begin_attempt("login", subject, remote_addr)

    with pytest.raises(AuthRateLimitError) as captured:
        service.begin_attempt("login", _MOBILE, _IP)

    assert captured.value.code == "auth_rate_limited"
    assert captured.value.retry_after == 600
    assert len(store.rows) == limit
    assert store.locks == set()


def test_successful_attempt_is_not_counted_as_failure() -> None:
    store = _Store()
    service = _service(store, mobile_limit=1)

    first = service.begin_attempt("login", _MOBILE, _IP)
    service.mark_succeeded(first)
    second = service.begin_attempt("login", _MOBILE, "203.0.113.10")

    assert first != second
    assert len(store.rows) == 2
    assert store.rows[0]["succeeded_at"] == _NOW.replace(tzinfo=None)
    assert store.rows[1]["succeeded_at"] is None
    count_queries = [
        sql
        for sql, _ in store.executed
        if sql.startswith("select count(*) as failed_count")
    ]
    assert count_queries
    assert all("succeeded_at is null" in sql for sql in count_queries)


def test_successful_attempt_still_counts_toward_ip_request_budget() -> None:
    store = _Store()
    service = _service(
        store,
        request_ip_limit=1,
        request_global_limit=100,
    )

    first = service.begin_attempt("login", _MOBILE, _IP)
    service.mark_succeeded(first)
    store.rows[0]["created_at"] = _NOW.replace(
        tzinfo=None
    ) - timedelta(seconds=25)

    with pytest.raises(AuthRateLimitError) as captured:
        service.begin_attempt("login", "13900139000", _IP)

    assert captured.value.code == "auth_rate_limited"
    assert captured.value.retry_after == 35
    assert len(store.rows) == 1
    request_queries = [
        sql
        for sql, _ in store.executed
        if sql.startswith("select count(*) as request_count")
    ]
    assert request_queries
    assert all("succeeded_at" not in sql for sql in request_queries)
    assert store.locks == set()


def test_successful_attempt_still_counts_toward_global_request_budget() -> None:
    store = _Store()
    service = _service(
        store,
        request_ip_limit=100,
        request_global_limit=2,
    )
    for index in range(2):
        attempt_id = service.begin_attempt(
            "login",
            f"13800138{index:03d}",
            f"203.0.113.{index + 20}",
        )
        service.mark_succeeded(attempt_id)

    with pytest.raises(AuthRateLimitError) as captured:
        service.begin_attempt(
            "login",
            "13900139000",
            "203.0.113.99",
        )

    assert captured.value.code == "auth_rate_limited"
    assert captured.value.retry_after == 60
    assert len(store.rows) == 2


def test_retry_after_tracks_oldest_failed_attempt_in_window() -> None:
    store = _Store()
    service = _service(store, mobile_limit=1)
    service.begin_attempt("login", _MOBILE, _IP)
    store.rows[0]["created_at"] = _NOW.replace(tzinfo=None) - timedelta(
        seconds=125
    )

    with pytest.raises(AuthRateLimitError) as captured:
        service.begin_attempt("login", _MOBILE, "203.0.113.10")

    assert captured.value.retry_after == 475


def test_lock_is_released_and_transaction_rolled_back_on_insert_failure() -> None:
    store = _Store()
    store.fail_insert = True

    with pytest.raises(AuthRateLimitError) as captured:
        _service(store).begin_attempt("login", _MOBILE, _IP)

    assert captured.value.code == "auth_rate_limit_storage_unavailable"
    assert store.rollback_count == 1
    assert store.locks == set()
    assert all(connection.closed for connection in store.connections)


def test_busy_lock_has_stable_error_and_releases_partial_acquisition() -> None:
    store = _Store()
    service = _service(store)
    subject_hash = service._hmac_hex(f"v1:login:subject:{_MOBILE}")  # noqa: SLF001
    remote_hash = service._hmac_hex(f"v1:login:remote:{_IP}")  # noqa: SLF001
    global_hash = service._hmac_hex("v1:login:global")  # noqa: SLF001
    lock_names = sorted(
        {
            f"fin_auth_g:{global_hash[:32]}",
            f"fin_auth_i:{remote_hash[:32]}",
            f"fin_auth_s:{subject_hash[:32]}",
        }
    )
    store.locks.add(lock_names[1])

    with pytest.raises(AuthRateLimitError) as captured:
        service.begin_attempt("login", _MOBILE, _IP)

    assert captured.value.code == "auth_rate_limit_busy"
    assert captured.value.retry_after == 1
    assert store.locks == {lock_names[1]}


def test_begin_attempt_holds_global_ip_and_subject_locks_before_insert() -> None:
    store = _Store()

    _service(store).begin_attempt("login", _MOBILE, _IP)

    get_lock_indexes = [
        index
        for index, (sql, _) in enumerate(store.executed)
        if "get_lock(" in sql
    ]
    insert_index = next(
        index
        for index, (sql, _) in enumerate(store.executed)
        if sql.startswith(f"insert into {AUTH_ATTEMPT_TABLE}")
    )
    release_indexes = [
        index
        for index, (sql, _) in enumerate(store.executed)
        if "release_lock(" in sql
    ]
    assert len(get_lock_indexes) == 3
    assert max(get_lock_indexes) < insert_index < min(release_indexes)
    acquired_names = [
        str(params[0])
        for sql, params in store.executed
        if "get_lock(" in sql
    ]
    assert acquired_names == sorted(acquired_names)
    assert any(name.startswith("fin_auth_g:") for name in acquired_names)


def test_missing_secret_and_schema_fail_closed() -> None:
    with pytest.raises(AuthRateLimitError) as missing_secret:
        AuthRateLimitService(secret="short").begin_attempt(
            "login",
            _MOBILE,
            _IP,
        )
    assert missing_secret.value.code == "auth_rate_limit_not_configured"

    store = _Store(table_exists=False)
    with pytest.raises(AuthRateLimitError) as missing_schema:
        _service(store).begin_attempt("login", _MOBILE, _IP)
    assert missing_schema.value.code == "auth_rate_limit_storage_unavailable"


def test_cleanup_deletes_only_attempts_older_than_retention() -> None:
    store = _Store()
    service = _service(store)
    for index, age_days in enumerate((31, 30, 29), start=1):
        service.begin_attempt(
            "login",
            f"13800138{index:03d}",
            f"203.0.113.{index}",
        )
        store.rows[-1]["created_at"] = _NOW.replace(
            tzinfo=None
        ) - timedelta(days=age_days)

    deleted = service.cleanup_old_attempts()

    assert deleted == 1
    assert [row["attempt_id"] for row in store.rows] == [
        "ara_test_attempt_0002",
        "ara_test_attempt_0003",
    ]
    assert store.commit_count == 4
    assert all(connection.closed for connection in store.connections)


def test_cleanup_is_bounded_and_deletes_oldest_rows_first() -> None:
    store = _Store()
    service = _service(store)
    for index, age_days in enumerate((35, 40, 45), start=1):
        service.begin_attempt(
            "login",
            f"13800138{index:03d}",
            f"203.0.113.{index}",
        )
        store.rows[-1]["created_at"] = _NOW.replace(
            tzinfo=None
        ) - timedelta(days=age_days)

    deleted = service.cleanup_old_attempts(retention_days=30, limit=2)

    assert deleted == 2
    assert [row["attempt_id"] for row in store.rows] == [
        "ara_test_attempt_0001"
    ]


def test_cleanup_storage_failure_has_stable_error_and_rolls_back() -> None:
    store = _Store()
    store.fail_delete = True

    with pytest.raises(AuthRateLimitError) as captured:
        _service(store).cleanup_old_attempts()

    assert captured.value.code == "auth_rate_limit_storage_unavailable"
    assert captured.value.message == "认证服务暂时不可用，请稍后再试。"
    assert store.rollback_count == 1
    assert all(connection.closed for connection in store.connections)


@pytest.mark.parametrize(
    ("retention_days", "limit"),
    [(0, 5_000), (30, 0), ("invalid", 5_000)],
)
def test_cleanup_rejects_invalid_bounds(
    retention_days: object,
    limit: object,
) -> None:
    store = _Store()

    with pytest.raises(AuthRateLimitError) as captured:
        _service(store).cleanup_old_attempts(  # type: ignore[arg-type]
            retention_days=retention_days,
            limit=limit,
        )

    assert captured.value.code == "invalid_auth_cleanup_request"
    assert store.connections == []
