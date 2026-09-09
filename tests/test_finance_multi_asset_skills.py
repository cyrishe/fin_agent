"""Discovery, explicit entry and progressive method access; no business queries."""
import asyncio
import json
import re
from pathlib import Path

import pytest
import yaml

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge
from src.scenarios.financial_qa.service import FinancialQaCcService


NEW = ("fund-analysis", "bond-analysis", "capital-flow-analysis")
ENHANCED = ("stock-comparison", "sector-theme-analysis")
ROOT = Path("src/skills/finance-business")


@pytest.fixture
def catalog(tmp_path):
    return FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots")


def test_discovery_extends_existing_identities_without_duplicate_entries(catalog):
    entries = catalog.public_entries()
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids)) == 15
    assert set(NEW + ENHANCED) <= set(ids)
    assert all(entry["owner"] == "system" and entry["visibility"] == "public" for entry in entries)
    for skill_id in NEW + ENHANCED:
        item = next(entry for entry in entries if entry["id"] == skill_id)
        assert f"- {skill_id}: {item['description']}" in catalog.turn_snapshot()["routing_summary"]
    assert all(catalog.allowed_tools_by_skill()[skill_id] == [] for skill_id in NEW)


@pytest.mark.parametrize("skill_id", NEW + ENHANCED)
def test_all_child_methods_load_after_parent_with_same_revision(catalog, tmp_path, skill_id):
    snapshot = catalog.method_snapshot(allowed_skill_ids=[skill_id])
    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context_path.write_text(json.dumps({"revision": "test", "owner_ids": ["test"], "tool_context": {
        "allowed_finance_skills": [skill_id], "_finance_skill_snapshot": snapshot,
        "_finance_skill_catalog_revision": snapshot["revision"],
    }}))
    bridge = FinanceDshMcpBridge(context_path=context_path, trace_path=trace_path)
    references = snapshot["skills"][skill_id]["references"]
    for reference in references:
        assert asyncio.run(bridge.call_tool("read_finance_skill_reference", {"skill_id": skill_id, "reference": reference})).get("error")
    asyncio.run(bridge.call_tool("read_finance_skill", {"skill_ids": [skill_id]}))
    for reference, expected in references.items():
        result = asyncio.run(bridge.call_tool("read_finance_skill_reference", {"skill_id": skill_id, "reference": reference}))
        assert result["content"] == expected["content"]
        assert result["content_hash"] == expected["content_hash"]
    trace = json.loads(trace_path.read_text())["tracker"]
    assert trace["active_skill_ids"] == [skill_id]
    assert not trace["result_refs"]


@pytest.mark.parametrize("skill_id", NEW)
def test_explicit_entry_reuses_authorized_method_and_default_discovery(catalog, skill_id):
    service = FinancialQaCcService(enabled=True, business_skill_catalog=catalog, session_service=object())
    context = service._runtime_context(application_context={}, owner_id="test", explicit_skill_ids=[skill_id])
    assert context["_finance_explicit_skill_ids"] == [skill_id]
    assert catalog.load(skill_id)["method"] in context["_finance_explicit_skill_prompt"]
    assert skill_id in context["_finance_skill_snapshot"]["skills"]
    ui = yaml.safe_load((ROOT / "skills" / skill_id / "agents/openai.yaml").read_text())
    assert f"${skill_id}" in ui["interface"]["default_prompt"]
    assert ui.get("policy", {}).get("allow_implicit_invocation", True)
    assert 25 <= len(ui["interface"]["short_description"]) <= 64


@pytest.mark.parametrize("skill_id", NEW + ENHANCED)
def test_published_child_resources_are_reachable_without_loading_other_skills(skill_id):
    root = ROOT / "skills" / skill_id
    text = (root / "SKILL.md").read_text()
    links = set(re.findall(r"\((references/[^)]+\.md)\)", text))
    shipped = {str(path.relative_to(root)) for path in (root / "references").glob("*.md") if not path.name.startswith("._")}
    assert shipped and links == shipped
    assert not (root / "schema.json").exists()
    assert not (root / "skill.json").exists()


def test_frozen_evaluation_inputs_are_separate_from_reviewer_expectations(catalog):
    from scripts.eval_finance_skill_methods import build_request
    data = json.loads(Path("tests/evals/finance_multi_asset_methods_v1.json").read_text())
    for case in data["cases"]:
        request = build_request({**case, "review_criteria": ["PRIVATE_REVIEW_SENTINEL"]}, catalog, "test-model")
        assert "PRIVATE_REVIEW_SENTINEL" not in json.dumps(request)
        assert "tools" not in request
        assert case["evidence"] in request["messages"][-1]["content"]
    variants = {case["id"]: case for case in data["cases"]}
    for first, second in (("fund_aligned", "fund_stale_nav"), ("bond_missing_terms", "bond_call_notice")):
        assert variants[first]["question"] == variants[second]["question"]
        assert variants[first]["evidence"] != variants[second]["evidence"]
    selection = json.loads(Path("tests/evals/finance_multi_asset_selection_v1.json").read_text())["cases"]
    assert set(NEW + ENHANCED) <= {case["primary"] for case in selection}
    for case in selection:
        assert set(case["acceptable"]) <= {item["id"] for item in catalog.public_entries()}
