from __future__ import annotations

from pathlib import Path

import pytest

from src.finance_api.auth import FinanceApiAuthError, FinanceApiKeyAuth
from src.finance_api.token_store import (
    FinanceAccessTokenStore,
    ManagedTokenInvalid,
    ManagedTokenStoreUnavailable,
)


KEY = "test-finance-parent-key-01234567890123"


class MemoryDatabase:
    def __init__(self):
        self.rows = {}

    def connect(self):
        return MemoryConnection(self)


class MemoryConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return MemoryCursor(self.database)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class MemoryCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("INSERT"):
            (token_id, project, name, principal, digest, prefix, suffix,
             created_at, expires_at, created_by) = params
            self.database.rows[token_id] = {
                "token_id": token_id, "project_name": project, "token_name": name,
                "principal_id": principal, "token_digest": digest, "token_prefix": prefix,
                "token_suffix": suffix, "created_at": created_at, "expires_at": expires_at,
                "disabled_at": None, "disabled_reason": None, "created_by": created_by,
                "disabled_by": None,
            }
            self.rowcount = 1
        elif normalized.startswith("SELECT PRINCIPAL_ID"):
            row = self.database.rows.get(params[0])
            self.result = None if row is None else (
                row["principal_id"], row["token_digest"], row["expires_at"], row["disabled_at"],
            )
        elif normalized.startswith("SELECT TOKEN_ID"):
            project = params[0] if len(params) == 2 else None
            limit = params[-1]
            rows = [row for row in self.database.rows.values()
                    if project is None or row["project_name"] == project]
            rows.sort(key=lambda row: row["created_at"], reverse=True)
            self.result = [(
                row["token_id"], row["project_name"], row["token_name"], row["principal_id"],
                row["token_prefix"], row["token_suffix"], row["created_at"], row["expires_at"],
                row["disabled_at"], row["disabled_reason"], row["created_by"], row["disabled_by"],
            ) for row in rows[:limit]]
        elif normalized.startswith("UPDATE"):
            disabled_at, disabled_by, reason, token_id = params
            row = self.database.rows.get(token_id)
            if row is not None and row["disabled_at"] is None:
                row.update(disabled_at=disabled_at, disabled_by=disabled_by, disabled_reason=reason)
                self.rowcount = 1
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result


def test_never_expiring_token_persists_digest_and_mask_only_then_disables():
    database = MemoryDatabase()
    now = [1_000]
    store = FinanceAccessTokenStore(connection_factory=database.connect, now_provider=lambda: now[0])
    issued = store.issue(
        project_name="alpha", token_name="research-client", principal_id="eval",
        ttl_seconds=None, created_by="operator",
    )

    row = database.rows[issued["token_id"]]
    assert issued["never_expires"] is True
    assert issued["expires_at"] is None
    assert issued["access_token"].encode() != row["token_digest"]
    assert issued["access_token"] not in repr(row)
    assert row["token_prefix"] == issued["access_token"][:16]
    assert row["token_suffix"] == issued["access_token"][-8:]
    assert store.authenticate(issued["access_token"]) == "eval"

    listed = store.list_tokens(project_name="alpha")
    assert listed == [{
        "token_id": issued["token_id"], "project_name": "alpha",
        "token_name": "research-client", "principal_id": "eval",
        "masked_token": issued["masked_token"], "created_at": 1000,
        "expires_at": None, "never_expires": True, "state": "active",
        "disabled_at": None, "disabled_reason": None,
        "created_by": "operator", "disabled_by": None,
    }]
    assert "access_token" not in listed[0]
    assert "token_digest" not in listed[0]

    now[0] = 1_200
    assert store.disable(issued["token_id"], disabled_by="operator", reason="retired") is True
    assert store.disable(issued["token_id"]) is False
    assert store.list_tokens()[0]["state"] == "disabled"
    with pytest.raises(ManagedTokenInvalid):
        store.authenticate(issued["access_token"])


def test_managed_token_expiration_and_tampering():
    database = MemoryDatabase()
    now = [2_000]
    store = FinanceAccessTokenStore(connection_factory=database.connect, now_provider=lambda: now[0])
    issued = store.issue(
        project_name="alpha", token_name="short", principal_id="eval", ttl_seconds=60,
    )
    assert store.authenticate(issued["access_token"]) == "eval"
    with pytest.raises(ManagedTokenInvalid):
        store.authenticate(issued["access_token"] + "x")
    now[0] = 2_060
    with pytest.raises(ManagedTokenInvalid):
        store.authenticate(issued["access_token"])
    assert store.list_tokens()[0]["state"] == "expired"


def test_auth_accepts_managed_token_and_fails_closed_when_store_is_unavailable():
    database = MemoryDatabase()
    store = FinanceAccessTokenStore(connection_factory=database.connect, now_provider=lambda: 3_000)
    auth = FinanceApiKeyAuth({"eval": KEY}, managed_token_store=store)
    issued = auth.issue_managed_token(
        project_name="alpha", token_name="service", ttl_seconds=None,
    )
    assert auth.authenticate(x_api_key=issued["access_token"]).principal_id == "eval"
    store.disable(issued["token_id"])
    with pytest.raises(FinanceApiAuthError) as inactive:
        auth.authenticate(x_api_key=issued["access_token"])
    assert inactive.value.code == "invalid_access_token"
    assert inactive.value.status_code == 401

    unavailable = FinanceAccessTokenStore(
        connection_factory=lambda: (_ for _ in ()).throw(RuntimeError("database secret detail")),
    )
    unavailable_auth = FinanceApiKeyAuth({"eval": KEY}, managed_token_store=unavailable)
    with pytest.raises(FinanceApiAuthError) as failed_closed:
        unavailable_auth.authenticate(x_api_key=issued["access_token"])
    assert failed_closed.value.code == "access_token_service_unavailable"
    assert failed_closed.value.status_code == 503
    assert "secret detail" not in failed_closed.value.message


def test_auth_rejects_managed_token_if_its_parent_principal_was_removed():
    database = MemoryDatabase()
    store = FinanceAccessTokenStore(connection_factory=database.connect, now_provider=lambda: 3_000)
    issuing_auth = FinanceApiKeyAuth({"eval": KEY}, managed_token_store=store)
    issued = issuing_auth.issue_managed_token(
        project_name="alpha", token_name="service", ttl_seconds=None,
    )
    rotated_auth = FinanceApiKeyAuth({"other": KEY + "-other"}, managed_token_store=store)
    with pytest.raises(FinanceApiAuthError) as rejected:
        rotated_auth.authenticate(x_api_key=issued["access_token"])
    assert rejected.value.code == "invalid_access_token"


def test_managed_token_input_validation():
    store = FinanceAccessTokenStore(connection_factory=MemoryDatabase().connect)
    for kwargs in [
        {"project_name": "", "token_name": "name", "principal_id": "eval", "ttl_seconds": None},
        {"project_name": "project", "token_name": "", "principal_id": "eval", "ttl_seconds": None},
        {"project_name": "project", "token_name": "name", "principal_id": "bad space", "ttl_seconds": None},
        {"project_name": "project", "token_name": "name", "principal_id": "eval", "ttl_seconds": 0},
    ]:
        with pytest.raises(ValueError):
            store.issue(**kwargs)
    with pytest.raises(ValueError):
        store.disable("not-an-id")


def test_schema_has_no_plaintext_token_column():
    ddl = (Path(__file__).parents[1] / "docs/sql/create_aiia_finance_access_token.sql").read_text()
    assert "token_digest" in ddl
    assert "disabled_at" in ddl
    assert "expires_at" in ddl
    assert "`access_token`" not in ddl.lower()
