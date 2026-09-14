"""Search requests are per-method metadata, not an authorization bypass."""
import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.scenarios.financial_qa.service import FinancialQaCcService


SEARCH_SKILLS = (
    "market-overview", "stock-research", "sector-theme-analysis",
    "fund-analysis", "bond-analysis", "stock-comparison", "equity-report-analysis",
)


def test_search_capability_is_derived_from_each_method(tmp_path):
    catalog = FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots")
    for entry in catalog.public_entries():
        skill_id = entry["id"]
        controls = catalog.studio_detail(skill_id)["controls"]
        assert controls["web_search_enabled"] == (skill_id in SEARCH_SKILLS)
        assert ("mcp__finance__general_search" in controls["supplemental_tools"]) == (skill_id in SEARCH_SKILLS)


@pytest.mark.parametrize("authorized", [False, True])
def test_explicit_method_search_request_respects_agent_authorization(tmp_path, authorized):
    catalog = FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots")
    service = FinancialQaCcService(enabled=True, business_skill_catalog=catalog, session_service=object())
    context = service._runtime_context(
        application_context={"default_agent": {"tools": ["general_search"] if authorized else []}},
        owner_id="test", explicit_skill_ids=["fund-analysis"],
    )
    assert context["_finance_explicit_skill_ids"] == ["fund-analysis"]
    for skill_id in SEARCH_SKILLS:
        assert context["skill_tool_access"][skill_id] == (["general_search"] if authorized else [])
