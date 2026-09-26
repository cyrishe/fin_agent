"""Profile projection keeps dynamic discovery distinct from explicit limits."""
from copy import deepcopy

import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.services.agent_execution_service import AgentExecutionService
from src.services.agent_runtime_service import AgentRuntimeError, AgentRuntimeService
from src.services.agent_studio_service import AgentStudioService
from src.services.application_runtime_service import ApplicationRuntimeService
from src.services.skill_candidate_store_service import InMemorySkillCandidateStoreService
from src.services.skill_hub_catalog_service import SkillHubCatalogService
from src.skill_runtime.intent_router import IntentRouter


def finance_service(tmp_path):
    store = InMemorySkillCandidateStoreService()
    hub = SkillHubCatalogService(
        business_catalog=FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots"),
        candidate_store=store,
    )
    name = "my-report-policy-test"
    store.create_candidate({
        "skill_id": name, "revision_no": 1, "display_name": "My report method",
        "description": "My report method", "content_hash": "fixture",
        "skill_markdown": f"---\nname: {name}\ndescription: My report method\n---\nUse report evidence.",
    }, owner_id="alice")
    hub.activate_candidate(name, owner_id="alice", expected_candidate_revision=1, expected_active_revision=0)
    return FinancialQaCcService(enabled=False, skill_hub_catalog_service=hub, root_dir=tmp_path / "sessions", log_path=tmp_path / "events.jsonl")


def application_with_skill_config(value):
    # Keep the real application and agent config/SOUL projection; override only
    # the policy under test, without modifying the on-disk shared application.
    studio = AgentStudioService()
    original_load = studio.load_agent_bundle

    def load(name):
        bundle = deepcopy(original_load(name))
        bundle["files"]["agent_config"]["skills"] = value
        return bundle

    studio.load_agent_bundle = load
    return ApplicationRuntimeService(agent_runtime_service=AgentRuntimeService(agent_studio_service=studio))


def test_real_default_application_discovers_new_system_and_owned_methods(tmp_path):
    application = ApplicationRuntimeService()
    context = application.get_application_context("investment_workbench")
    assert "skills" not in context["default_agent"]
    assert "skills" not in context["default_agent"]["runtime_profile"]
    assert "allowed_skills" not in application.build_route_context("investment_workbench")
    service = finance_service(tmp_path)
    runtime = service._runtime_context(application_context=context, owner_id="alice", explicit_skill_ids=["equity-report-analysis", "my-report-policy-test"])
    assert "allowed_finance_skills" not in runtime
    assert set(runtime["_finance_explicit_skill_ids"]) == {"equity-report-analysis", "my-report-policy-test"}
    assert "my-report-policy-test" in runtime["_finance_skill_snapshot"]["skills"]
    # Dynamic discovery preserves registry ownership restrictions.
    other = service._runtime_context(application_context=context, owner_id="bob")
    assert "my-report-policy-test" not in other["_finance_skill_snapshot"]["skills"]


@pytest.mark.parametrize("allowed", [[], ["equity-report-analysis"]])
def test_explicit_application_allowlist_survives_all_projections(tmp_path, allowed):
    application = application_with_skill_config(allowed)
    context = application.get_application_context("investment_workbench")
    assert context["default_agent"]["skills"] == allowed
    assert context["default_agent"]["runtime_profile"]["skills"] == allowed
    assert application.build_route_context("investment_workbench")["allowed_skills"] == allowed
    service = finance_service(tmp_path)
    runtime = service._runtime_context(application_context=context, owner_id="alice")
    assert set(runtime["_finance_skill_snapshot"]["skills"]) == set(allowed)
    with pytest.raises(ValueError, match="无权"):
        service._runtime_context(application_context=context, owner_id="alice", explicit_skill_ids=["my-report-policy-test"])


def test_null_policy_is_unconfigured_and_invalid_type_does_not_grant_access():
    application = application_with_skill_config(None)
    assert "skills" not in application.get_application_context("investment_workbench")["default_agent"]
    assert "allowed_skills" not in application.build_route_context("investment_workbench")
    with pytest.raises(AgentRuntimeError, match="列表"):
        AgentRuntimeService().build_runtime_profile(agent_name="any-agent", config={"skills": "all"}, soul_md="")


def test_execution_projection_preserves_incoming_empty_restriction():
    for application in [ApplicationRuntimeService(), application_with_skill_config(["equity-report-analysis"])]:
        execution = AgentExecutionService(application_runtime_service=application)
        projected = execution.build_execution_context(application_name="investment_workbench", context={"allowed_skills": []})
        assert projected["route_context"]["allowed_skills"] == []


def test_legacy_router_treats_empty_allowlist_as_a_restriction():
    route = {"route_type": "single_skill", "selected_skill": "stock_deep_dive", "candidate_skills": ["stock_deep_dive"]}
    assert IntentRouter._apply_skill_constraints(route=route, context={}) == route
    restricted = IntentRouter._apply_skill_constraints(route=route, context={"allowed_skills": []})
    assert restricted["selected_skill"] is None
    assert restricted["candidate_skills"] == []
