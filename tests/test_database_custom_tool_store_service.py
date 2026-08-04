import json

import pytest

from src.services.database_custom_tool_store_service import DatabaseCustomToolStoreService


class _MemoryDatabase:
    def __init__(self) -> None:
        self.artifact = {
            "artifact_id": 7,
            "name": "ct_threshold",
            "status": "active",
            "enabled": 1,
            "display_name": "阈值工具",
            "description": "阈值为 10。",
            "owner": "user_a",
            "current_revision_no": 1,
            "source_manifest_json": json.dumps(
                {"visibility": "personal", "runtime": {"timeout_ms": 1000}}
            ),
        }
        self.revisions = {
            1: {
                "revision_id": 101,
                "artifact_id": 7,
                "revision_no": 1,
                "definition_json": json.dumps(
                    {
                        "tool_name": "ct_threshold",
                        "display_name": "阈值工具",
                        "description": "阈值为 10。",
                        "runtime": {"timeout_ms": 1000},
                        "code_hash": "old-hash",
                    }
                ),
                "schema_json": json.dumps(
                    {
                        "input": {"type": "object"},
                        "output": {"type": "object"},
                    }
                ),
                "spec_json": json.dumps(
                    {
                        "implementation": {
                            "entry_module": "main",
                            "modules": [
                                {
                                    "module_id": "main",
                                    "source_code": "def run(inputs): return {'limit': 10}",
                                }
                            ],
                        }
                    }
                ),
                "content_hash": "old-hash",
            }
        }
        self.commits = 0
        self.rollbacks = 0

    def connect(self):
        return _MemoryConnection(self)


class _MemoryConnection:
    def __init__(self, database: _MemoryDatabase) -> None:
        self.database = database

    def cursor(self):
        return _MemoryCursor(self.database)

    def commit(self) -> None:
        self.database.commits += 1

    def rollback(self) -> None:
        self.database.rollbacks += 1

    def close(self) -> None:
        return None


class _MemoryCursor:
    def __init__(self, database: _MemoryDatabase) -> None:
        self.database = database
        self._result = None
        self.rowcount = 0
        self.lastrowid = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        self.rowcount = 0
        if normalized.startswith("select * from aiia_runtime_artifact "):
            name = params[0]
            self._result = (
                dict(self.database.artifact)
                if name == self.database.artifact["name"]
                else None
            )
        elif normalized.startswith("select coalesce(max(revision_no)"):
            self._result = {
                "max_revision_no": max(self.database.revisions, default=0)
            }
        elif normalized.startswith("insert into aiia_runtime_artifact_revision"):
            revision_no = int(params[1])
            self.database.revisions[revision_no] = {
                "revision_id": 100 + revision_no,
                "artifact_id": int(params[0]),
                "revision_no": revision_no,
                "definition_json": params[2],
                "schema_json": params[3],
                "spec_json": params[4],
                "content_hash": params[5],
            }
            self.rowcount = 1
            self._result = None
        elif normalized.startswith("select * from aiia_runtime_artifact_revision"):
            self._result = dict(self.database.revisions.get(int(params[1])) or {}) or None
        elif normalized.startswith("update aiia_runtime_artifact set status='active'"):
            expected_active = int(params[9])
            if int(self.database.artifact["current_revision_no"]) == expected_active:
                self.database.artifact.update(
                    {
                        "status": "active",
                        "enabled": 1,
                        "display_name": params[0],
                        "description": params[1],
                        "implementation_target": params[3],
                        "source_manifest_json": params[4],
                        "current_revision_no": int(params[6]),
                    }
                )
                self.rowcount = 1
            self._result = None
        else:
            raise AssertionError(f"unexpected SQL in fake database: {normalized}")

    def fetchone(self):
        return self._result


def test_owner_scoped_tool_name_is_stable_and_within_storage_limit() -> None:
    first = DatabaseCustomToolStoreService._owner_scoped_name(
        "ct_abu_market_buy_decision",
        "guest-a",
    )
    same = DatabaseCustomToolStoreService._owner_scoped_name(
        "ct_abu_market_buy_decision",
        "guest-a",
    )
    other = DatabaseCustomToolStoreService._owner_scoped_name(
        "ct_abu_market_buy_decision",
        "guest-b",
    )

    assert first == same
    assert first != other
    assert first.startswith("ct_abu_market_buy_decision_")
    assert len(first) <= 64


def test_database_candidate_does_not_move_pointer_until_atomic_activation() -> None:
    database = _MemoryDatabase()
    store = DatabaseCustomToolStoreService(
        connection_factory=database.connect,
        error_type=ValueError,
    )
    candidate = store.save_candidate_revision(
        {
            "manifest": {
                "tool_name": "ct_threshold",
                "display_name": "阈值工具",
                "description": "阈值为 5。",
                "runtime": {"timeout_ms": 1000},
            },
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "code": "def run(inputs): return {'limit': 5}",
        },
        owner_id="user_a",
        tool_name="ct_threshold",
    )

    assert database.artifact["status"] == "active"
    assert database.artifact["current_revision_no"] == 1
    assert candidate["manifest"]["current_revision"] == 2
    assert candidate["manifest"]["active_revision"] == 1
    assert candidate["storage"]["is_active"] is False

    activated = store.activate_revision(
        "ct_threshold",
        2,
        expected_active_revision=1,
        owner_id="user_a",
    )

    assert activated["manifest"]["status"] == "active"
    assert activated["manifest"]["current_revision"] == 2
    assert activated["manifest"]["active_revision"] == 2
    assert "'limit': 5" in activated["code"]


def test_database_candidate_persists_revision_scoped_strategy_companions() -> None:
    database = _MemoryDatabase()
    store = DatabaseCustomToolStoreService(
        connection_factory=database.connect,
        error_type=ValueError,
    )
    runtime_profile = {
        "protocol": "strategy_runtime_profile.v1",
        "binding": {"field": "universe"},
        "required_history_sessions": 20,
        "default_run_sessions": 100,
        "default_universe_ref": {"type": "all_a_share"},
        "market_code": "CN_A",
    }
    selection_profile = {
        "candidate_path": "data.selected_stocks",
        "symbol_field": "stock_code",
        "output_date_path": "data.as_of_date",
    }
    finance_profile = {
        "protocol": "finance_tool_profile.v1",
        "family": "strategy",
        "execution_shape": "cross_sectional",
        "output_semantic": "ranked_selection",
        "summary": "返回完整有序选股结果。",
    }

    candidate = store.save_candidate_revision(
        {
            "manifest": {
                "tool_name": "ct_threshold",
                "display_name": "横截面策略",
                "description": "返回完整有序选股结果。",
                "runtime": {"timeout_ms": 1000},
                "capabilities": ["custom_tool", "strategy"],
            },
            "input_schema": {
                "type": "object",
                "properties": {
                    "universe": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
            },
            "output_schema": {"type": "object"},
            "code": "def run(inputs): return {'data': {'selected_stocks': []}}",
            "finance_tool_profile": finance_profile,
            "strategy_runtime_profile": runtime_profile,
            "selection_output_profile": selection_profile,
        },
        owner_id="user_a",
        tool_name="ct_threshold",
    )

    assert candidate["strategy_runtime_profile"] == runtime_profile
    assert candidate["selection_output_profile"] == selection_profile
    assert candidate["finance_tool_profile"] == finance_profile
    assert store.load_revision("ct_threshold", 2)[
        "strategy_runtime_profile"
    ] == runtime_profile


def test_database_store_rejects_executable_action_profile() -> None:
    database = _MemoryDatabase()
    store = DatabaseCustomToolStoreService(
        connection_factory=database.connect,
        error_type=ValueError,
    )

    with pytest.raises(ValueError, match="design-only"):
        store.save_candidate_revision(
            {
                "manifest": {"tool_name": "ct_threshold"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "code": "def run(inputs): return {'ok': True}",
                "finance_tool_profile": {
                    "family": "action",
                    "execution_shape": "portfolio_stateful",
                    "output_semantic": "action_receipt",
                },
            },
            owner_id="user_a",
            tool_name="ct_threshold",
        )

    assert max(database.revisions) == 1


def test_database_activation_rejects_stale_owner_and_missing_candidate() -> None:
    database = _MemoryDatabase()
    store = DatabaseCustomToolStoreService(
        connection_factory=database.connect,
        error_type=ValueError,
    )

    with pytest.raises(ValueError, match="not owned"):
        store.activate_revision(
            "ct_threshold",
            2,
            expected_active_revision=1,
            owner_id="user_b",
        )
    with pytest.raises(ValueError, match="revision not found"):
        store.activate_revision(
            "ct_threshold",
            999,
            expected_active_revision=1,
            owner_id="user_a",
        )
    database.artifact["current_revision_no"] = 2
    with pytest.raises(ValueError, match="revision changed"):
        store.activate_revision(
            "ct_threshold",
            1,
            expected_active_revision=1,
            owner_id="user_a",
        )

    assert database.artifact["current_revision_no"] == 2
