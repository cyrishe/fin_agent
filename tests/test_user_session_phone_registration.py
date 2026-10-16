from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pymysql
import pytest

from src.services import user_session_service as module
from src.services.user_session_service import (
    PhoneChallengeConsumptionError,
    UserIdentityConflictError,
    UserSessionService,
    UserSessionStorageError,
)


@dataclass
class _Store:
    challenge: dict[str, Any] = field(
        default_factory=lambda: {
            "challenge_id": "pvc_atomic_test",
            "mobile_hash": "mobile-hmac",
            "consumed": False,
        }
    )
    users: list[tuple[Any, ...]] = field(default_factory=list)
    identities: list[tuple[Any, ...]] = field(default_factory=list)
    credentials: list[tuple[Any, ...]] = field(default_factory=list)
    sessions: list[tuple[Any, ...]] = field(default_factory=list)
    fail_on: str = ""
    commits: int = 0
    rollbacks: int = 0


class _Connection:
    def __init__(self, store: _Store):
        self.store = store
        self.tx = self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        return {
            "challenge": deepcopy(self.store.challenge),
            "users": deepcopy(self.store.users),
            "identities": deepcopy(self.store.identities),
            "credentials": deepcopy(self.store.credentials),
            "sessions": deepcopy(self.store.sessions),
        }

    def cursor(self, *_args: object):
        return _Cursor(self)

    def commit(self) -> None:
        self.store.challenge = deepcopy(self.tx["challenge"])
        self.store.users = deepcopy(self.tx["users"])
        self.store.identities = deepcopy(self.tx["identities"])
        self.store.credentials = deepcopy(self.tx["credentials"])
        self.store.sessions = deepcopy(self.tx["sessions"])
        self.store.commits += 1

    def rollback(self) -> None:
        self.tx = self._snapshot()
        self.store.rollbacks += 1

    def close(self) -> None:
        return None


class _Cursor:
    def __init__(self, connection: _Connection):
        self.connection = connection
        self.result: Any = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        normalized = " ".join(sql.lower().split())
        self.result = None
        self.rowcount = 0
        if normalized.startswith("show tables like"):
            self.result = (params[0],)
            return
        if normalized.startswith("select challenge_id"):
            challenge_id, mobile_hash = params
            challenge = self.connection.tx["challenge"]
            if (
                challenge["challenge_id"] == challenge_id
                and challenge["mobile_hash"] == mobile_hash
                and not challenge["consumed"]
            ):
                self.result = {"challenge_id": challenge_id}
            return
        if normalized.startswith(f"insert into {module.IDENTITY_TABLE} ("):
            if self.connection.store.fail_on == "identity_duplicate":
                raise pymysql.IntegrityError(1062, "duplicate phone")
            self.connection.tx["identities"].append(params)
            return
        if normalized.startswith(f"insert into {module.CREDENTIAL_TABLE} ("):
            self.connection.tx["credentials"].append(params)
            return
        if normalized.startswith(f"insert into {module.SESSION_TABLE} ("):
            if self.connection.store.fail_on == "session":
                raise RuntimeError("session insert failed")
            self.connection.tx["sessions"].append(params)
            return
        if normalized.startswith(f"insert into {module.USER_TABLE} ("):
            self.connection.tx["users"].append(params)
            return
        if normalized.startswith(f"update {module.PHONE_CHALLENGE_TABLE}"):
            challenge = self.connection.tx["challenge"]
            if not challenge["consumed"]:
                challenge["consumed"] = True
                self.rowcount = 1
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self) -> Any:
        return self.result


class _Db:
    def __init__(self, store: _Store):
        self.conn = _Connection(store)

    def close_db(self) -> None:
        self.conn.close()


def _create(service: UserSessionService) -> dict[str, Any]:
    return service.create_phone_member_with_session(
        mobile="13800138000",
        password_hash="pbkdf2_sha256$310000$salt$digest",
        verification_provider="sms:mock",
        challenge_id="pvc_atomic_test",
        challenge_mobile_hash="mobile-hmac",
        verification_request_id="request-1",
        verified_at="2026-07-30T12:00:00+00:00",
        user_agent="test-browser",
        remote_addr="127.0.0.1",
    )


def test_account_session_and_challenge_consumption_commit_atomically(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr(module, "SystemDbUtils", lambda: _Db(store))
    service = UserSessionService()

    result = _create(service)

    assert result["user_id"].startswith("user_")
    assert result["session_token"].startswith("ms_")
    assert store.challenge["consumed"] is True
    assert len(store.users) == 1
    assert len(store.identities) == 1
    assert len(store.credentials) == 1
    assert len(store.sessions) == 1
    assert store.commits == 1

    with pytest.raises(PhoneChallengeConsumptionError):
        _create(service)
    assert len(store.users) == 1


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("identity_duplicate", UserIdentityConflictError),
        ("session", UserSessionStorageError),
    ],
)
def test_registration_failure_rolls_back_without_consuming_challenge(
    monkeypatch,
    failure: str,
    expected_error: type[Exception],
) -> None:
    store = _Store(fail_on=failure)
    monkeypatch.setattr(module, "SystemDbUtils", lambda: _Db(store))

    with pytest.raises(expected_error):
        _create(UserSessionService())

    assert store.challenge["consumed"] is False
    assert store.users == []
    assert store.identities == []
    assert store.credentials == []
    assert store.sessions == []
    assert store.commits == 0
    assert store.rollbacks >= 1
