from __future__ import annotations

import asyncio
import json

import pytest

from src.services.security_identity_service import SecurityIdentityService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge
from src.services.session_variable_store_service import SessionVariableStoreService


class Database:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.calls = []
        self.closed = False
        self.conn = self

    def cursor(self, *args):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        value = next(self.batches)
        if isinstance(value, Exception):
            raise value
        return value

    def close_db(self):
        self.closed = True


def test_identity_supports_names_codes_batch_and_retains_ambiguity():
    db = Database([[{"code": "300274.SZ", "name": "阳光电源"}],
                   [{"code": "300274.SZ", "name": "阳光电源"}],
                   [], [{"code": "600000.SH", "name": "浦发银行"},
                        {"code": "000001.SZ", "name": "平安银行"}]])
    result = SecurityIdentityService(db_factory=lambda: db).resolve(["阳光电源", "300274", "银行", "阳光电源"])
    assert len(result["items"]) == 3
    assert result["items"][0]["candidates"] == [{"code": "300274.SZ", "name": "阳光电源", "market": "SZ"}]
    assert len(result["items"][2]["candidates"]) == 2
    assert db.calls[1][1] == ("300274",)
    assert db.calls[-1][1] == ("%银行%",)
    assert all("list_date" not in sql and "industry" not in sql and "delist_date" not in sql for sql, _ in db.calls)
    assert db.closed


def test_identity_exact_full_code_empty_scope_and_literal_fuzzy_matching():
    db = Database([[], [], []])
    result = SecurityIdentityService(db_factory=lambda: db).resolve(["300274.sz", "a%_!' OR 1=1"])
    assert result["ok"] is True
    assert all(item["candidates"] == [] for item in result["items"])
    assert db.calls[0][1] == ("300274.SZ", "300274.sz")
    assert "OR 1=1" not in db.calls[-1][0]
    assert db.calls[-1][1] == ("%a!%!_!!' OR 1=1%",)
    assert db.closed


def test_identity_returns_bounded_candidates_and_closes_on_error():
    db = Database([[{"code": f"{i:06}.SH", "name": "同名"} for i in range(21)]])
    result = SecurityIdentityService(db_factory=lambda: db).resolve(["同名"])
    assert len(result["items"][0]["candidates"]) == 20
    assert result["items"][0]["truncated"] is True
    failing = Database([RuntimeError("private connection error")])
    with pytest.raises(RuntimeError):
        SecurityIdentityService(db_factory=lambda: failing).resolve(["同名"])
    assert failing.closed


@pytest.mark.parametrize("value", [None, "阳光电源", [], [""], [None], ["x" * 101], ["a"] * 21])
def test_identity_rejects_unexecutable_input_before_connecting(value):
    def connection():
        pytest.fail("invalid input must not open database")
    with pytest.raises(ValueError):
        SecurityIdentityService(db_factory=connection).resolve(value)


def test_identity_tool_and_skill_selection_through_shared_adapters(tmp_path):
    db = Database([[{"code": "300274.SZ", "name": "阳光电源"}],
                   [{"code": "300274.SZ", "name": "阳光电源"}]])
    service = FinanceDataQueryCcTools(
        security_identity=SecurityIdentityService(db_factory=lambda: db),
        result_store=SessionVariableStoreService(data_root=tmp_path / "results"))
    context = {"_finance_skill_catalog_revision": "v1", "_finance_skill_snapshot": {
        "revision": "v1", "skills": {"example": {"method": "使用已有证据分析。", "references": {}}}}}
    definitions, _, tracker = service.build_tools(owner_ids=["test"], tool_context=context)
    handlers = {t.name: t.handler for t in definitions}

    def call(name, args):
        return json.loads(asyncio.run(handlers[name](args))["content"][0]["text"])

    identity = call("resolve_security", {"identifiers": ["阳光电源"]})
    assert identity["items"][0]["candidates"][0]["code"] == "300274.SZ"
    assert tracker["calls"][-1]["row_count"] == 1
    assert tracker["calls"][-1]["duration_ms"] >= 0
    assert call("read_finance_skill", {"skill_ids": []})["skills"] == []
    assert tracker["active_skill_ids"] == []
    assert "error" in call("read_finance_skill", {"skill_ids": ["example", "not-authorized"]})
    assert tracker["active_skill_ids"] == []  # Failed batches have no partial activation.
    assert call("read_finance_skill", {"skill_ids": ["example"]})["skills"][0]["method"] == "使用已有证据分析。"
    assert tracker["active_skill_ids"] == ["example"]
    assert call("read_finance_skill", {"skill_id": "example"})["method"] == "使用已有证据分析。"
    assert "error" in call("read_finance_skill", {"skill_ids": "example"})
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps({"revision": "turn", "tool_context": context}))
    bridge = FinanceDshMcpBridge(context_path=context_path, trace_path=tmp_path / "trace.json", system_tools=service)
    schema = next(t for t in bridge.list_tools() if t.name == "read_finance_skill").inputSchema
    assert "skill_ids" in schema["properties"]
    assert "resolve_security" in {t.name for t in bridge.list_tools()}
    assert asyncio.run(bridge.call_tool("resolve_security", {"identifiers": ["阳光电源"]})) == identity
    selected = asyncio.run(bridge.call_tool("read_finance_skill", {"skill_ids": ["example"]}))
    assert selected["skills"][0]["skill_id"] == "example"


def test_identity_tool_keeps_connection_details_private(tmp_path):
    db = Database([RuntimeError("password=secret endpoint=private")])
    service = FinanceDataQueryCcTools(security_identity=SecurityIdentityService(db_factory=lambda: db),
        result_store=SessionVariableStoreService(data_root=tmp_path))
    definitions, _, _ = service.build_tools(owner_ids=["test"], tool_context={})
    tool = next(t for t in definitions if t.name == "resolve_security")
    result = asyncio.run(tool.handler({"identifiers": ["阳光电源"]}))
    assert "secret" not in json.dumps(result)
    assert json.loads(result["content"][0]["text"])["ok"] is False


def test_detail_keeps_method_selection_and_identity_inputs_without_secret_fields():
    from src.scenarios.financial_qa.dsh_service import _execution_timing_steps
    from src.finance_api.service import FinanceApiGateway
    events = [{"type": "tool/call", "time": 10, "data": {"turn": 1, "step": 1,
        "callId": "select", "name": "mcp__finance__read_finance_skill",
        "arguments": {"skill_ids": ["stock-research"], "secret": "private"}}},
        {"type": "tool/call", "time": 20, "data": {"turn": 1, "step": 2,
        "callId": "identity", "name": "mcp__finance__resolve_security",
        "arguments": {"identifiers": ["阳光电源"], "secret": "private"}}}]
    steps = _execution_timing_steps(events)
    assert steps[0]["arguments"] == {"skill_ids": ["stock-research"]}
    assert steps[1]["arguments"] == {"identifiers": ["阳光电源"]}
    detail = FinanceApiGateway._detail({}, {"execution_steps": steps, "tool_calls": [
        {"tool": "read_finance_skill", "skill_ids": ["stock-research"], "secret": "private"},
        {"tool": "resolve_security", "identifiers": ["阳光电源"], "duration_ms": 20}]})
    assert detail["tool_calls"][1]["identifiers"] == ["阳光电源"]
    assert "private" not in json.dumps(detail)


@pytest.mark.parametrize("payload,wire_error,expected", [({"ok": True}, False, False),
    ({"ok": False}, False, True), ({"error": "private detail"}, False, True), ({}, True, True)])
def test_detail_records_tool_failure_without_exposing_error_content(payload, wire_error, expected):
    from src.scenarios.financial_qa.dsh_service import _execution_timing_steps
    events = [{"type": "tool/call", "time": 0, "data": {"callId": "x", "name": "mcp__finance__finance_query"}},
        {"type": "tool/result", "time": 10, "data": {"message": {"source": {"callId": "x"},
            "content": [{"type": "tool-result", "isError": wire_error,
                         "content": [{"type": "text", "text": json.dumps(payload)}]}]}}}]
    span = _execution_timing_steps(events)[0]
    assert span["is_error"] is expected
    assert "private" not in json.dumps(span)
