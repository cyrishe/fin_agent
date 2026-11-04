"""Real package/loader contracts, not a simulation of model method selection."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge


@pytest.fixture
def loaded_bridge(tmp_path):
    catalog = FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots")
    allowed = ["stock-research", "equity-report-analysis", "stock-comparison"]
    snapshot = catalog.method_snapshot(allowed_skill_ids=allowed)
    context = {
        "allowed_finance_skills": allowed,
        "_finance_skill_snapshot": snapshot,
        "_finance_skill_catalog_revision": snapshot["revision"],
        "_finance_explicit_skill_ids": [],
    }
    context_path, trace_path = tmp_path / "context.json", tmp_path / "trace.json"
    context_path.write_text(json.dumps({"revision": "test-turn", "owner_ids": ["evaluation"], "tool_context": context}), encoding="utf-8")
    bridge = FinanceDshMcpBridge(context_path=context_path, trace_path=trace_path)
    bridge.list_tools()
    return bridge, trace_path, snapshot


def call(bridge, name, **args):
    return asyncio.run(bridge.call_tool(name, args))


@pytest.mark.parametrize("reference", ["adaptive-paths.md", "method-composition.md", "company-archetypes.md", "catalyst-expectation-redteam.md"])
def test_stock_child_method_loads_from_frozen_package_only_after_parent(loaded_bridge, reference):
    bridge, trace, snapshot = loaded_bridge
    path = f"references/{reference}"
    assert call(bridge, "read_finance_skill_reference", skill_id="stock-research", reference=path).get("error")
    parent = call(bridge, "read_finance_skill", skill_id="stock-research")
    result = call(bridge, "read_finance_skill_reference", skill_id="stock-research", reference=path)
    assert parent["content_hash"] == snapshot["skills"]["stock-research"]["content_hash"]
    expected = snapshot["skills"]["stock-research"]["references"][path]
    assert result["content"] == expected["content"]
    assert result["content_hash"] == hashlib.sha256(result["content"].encode()).hexdigest()
    tracker = json.loads(trace.read_text())["tracker"]
    assert tracker["active_skill_ids"] == ["stock-research"]
    assert snapshot["skills"]["equity-report-analysis"]["method"] not in trace.read_text()


def test_stock_and_report_can_share_one_turn_without_implicit_dependency(loaded_bridge):
    bridge, trace, snapshot = loaded_bridge
    assert json.loads(trace.read_text())["tracker"]["active_skill_ids"] == []
    call(bridge, "read_finance_skill", skill_id="stock-research")
    # A composition hint must not grant access to the report's child before its parent loads.
    report_args = {"skill_id": "equity-report-analysis", "reference": "references/viewpoint-revisions.md"}
    assert call(bridge, "read_finance_skill_reference", **report_args).get("error")
    call(bridge, "read_finance_skill", skill_id="equity-report-analysis")
    result = call(bridge, "read_finance_skill_reference", **report_args)
    assert result["content_hash"] == snapshot["skills"]["equity-report-analysis"]["references"][report_args["reference"]]["content_hash"]
    tracker = json.loads(trace.read_text())["tracker"]
    assert tracker["active_skill_ids"] == ["stock-research", "equity-report-analysis"]
    assert [entry["skill_id"] for entry in tracker["skill_entries"]] == ["stock-research", "equity-report-analysis"]
    assert {"finance_query", "load_finance_result"} <= {tool.name for tool in bridge.list_tools()}
    assert call(bridge, "read_finance_skill", skill_id="valuation-analysis").get("error")
    assert call(bridge, "read_finance_skill_reference", skill_id="stock-research", reference="references/../../equity-report-analysis/SKILL.md").get("error")


def test_stock_eval_questions_are_separate_from_reviewer_and_synthetic_evidence():
    data = json.loads(Path("tests/evals/stock_research_adaptive_v2.json").read_text())
    cases = data["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["question"] and case["review_criteria"] for case in cases)
    assert all(not case.get("explicit_skill_ids") for case in cases)
    assert data["evidence_pairs"][0]["question"]
    for pair in data["evidence_pairs"]:
        assert set(pair["variants"]) == {"a", "b"}
        assert pair["synthetic"] is True
        assert pair["variants"]["a"]["evidence"] != pair["variants"]["b"]["evidence"]
