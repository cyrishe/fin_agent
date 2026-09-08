"""Registry -> finance turn -> method loading, without a model or database."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.finance_claude_session_service import FinanceClaudeSessionService
from src.services.session_variable_store_service import SessionVariableStoreService
from src.services.skill_candidate_store_service import InMemorySkillCandidateStoreService, SkillCandidateStoreError
from src.services.skill_hub_catalog_service import SkillHubCatalogService


class Session:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"result": "有来源的回答", "result_refs": [], "error": ""}

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)

    def close(self):
        pass


@pytest.fixture
def hub(tmp_path):
    catalog = FinanceBusinessSkillCatalog(snapshot_root=tmp_path / "snapshots")
    store = InMemorySkillCandidateStoreService()
    text = "---\nname: personal-report\ndescription: My report method\n---\nPRIVATE_METHOD_BODY"
    store.create_candidate({
        "skill_id": "personal-report", "display_name": "我的研报方法",
        "revision_no": 1, "description": "My report method", "skill_markdown": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "references": {"references/detail.md": "PRIVATE_REFERENCE"},
    }, owner_id="alice")
    registry = SkillHubCatalogService(
        business_catalog=catalog, candidate_store=store,
        legacy_skill_studio=SimpleNamespace(list_compiled_skills=lambda: []),
    )
    registry.activate_candidate("personal-report", owner_id="alice",
        expected_candidate_revision=1, expected_active_revision=0)
    return registry


def service(hub, session=None):
    return FinancialQaCcService(enabled=True, skill_hub_catalog_service=hub,
        session_service=session or Session(), dsh_session_service=Session())


def test_explicit_method_is_authorized_loaded_once_and_references_stay_lazy(hub):
    agent = service(hub)
    context = agent._runtime_context(application_context={}, owner_id="alice",
        explicit_skill_ids=["personal-report", "personal-report"])
    prompt = FinanceClaudeSessionService.build_user_prompt("机构怎么看？", context)
    assert context["_finance_explicit_skill_ids"] == ["personal-report"]
    assert prompt.count("PRIVATE_METHOD_BODY") == 1
    assert "PRIVATE_REFERENCE" not in prompt
    assert "产业链线索" not in prompt  # Unselected reference bodies remain outside the prompt.
    assert context["_finance_skill_snapshot"]["revision"] == hub.runtime_catalog(owner_ids=["alice"]).revision
    agent._validate_business_skill_runtime_binding(context["_finance_skill_runtime_binding"])


def test_private_and_application_restrictions_are_enforced_before_prompt(hub):
    agent = service(hub)
    bob = agent._runtime_context(application_context={}, owner_id="bob")
    assert "personal-report" not in bob["_finance_skill_snapshot"]["skills"]
    assert "PRIVATE_METHOD_BODY" not in json.dumps(bob, default=str)
    with pytest.raises(ValueError, match="无权"):
        agent._runtime_context(application_context={}, owner_id="bob", explicit_skill_ids=["personal-report"])
    with pytest.raises(ValueError, match="无权"):
        agent._runtime_context(application_context={"default_agent": {"skills": []}},
            owner_id="alice", explicit_skill_ids=["personal-report"])
    context = agent._runtime_context(application_context={"default_agent": {"skills": ["equity-report-analysis"]}},
        owner_id="alice")
    assert list(context["_finance_skill_snapshot"]["skills"]) == ["equity-report-analysis"]


def test_public_visibility_does_not_change_author_or_private_reference_identity(hub):
    hub.set_visibility("personal-report", owner_id="alice", visibility="public", expected_active_revision=1)
    agent = service(hub)
    context = agent._runtime_context(application_context={}, owner_id="bob", explicit_skill_ids=["personal-report"])
    assert "PRIVATE_METHOD_BODY" in context["_finance_explicit_skill_prompt"]
    hub.set_visibility("personal-report", owner_id="alice", visibility="private", expected_active_revision=1)
    with pytest.raises(ValueError, match="无权"):
        agent._runtime_context(application_context={}, owner_id="bob", explicit_skill_ids=["personal-report"])
    # Already authorized current-turn evidence is immutable, not silently rewritten.
    assert context["_finance_skill_snapshot"]["skills"]["personal-report"]["references"]["references/detail.md"]["content"] == "PRIVATE_REFERENCE"


def test_registry_failure_keeps_system_methods_but_never_guesses_personal_authorization(hub, monkeypatch):
    def unavailable(**kwargs):
        raise SkillCandidateStoreError("offline fixture")
    monkeypatch.setattr(hub.candidate_store, "list_available", unavailable)
    agent = service(hub)
    context = agent._runtime_context(application_context={}, owner_id="alice")
    assert "equity-report-analysis" in context["_finance_skill_snapshot"]["skills"]
    assert "personal-report" not in context["_finance_skill_snapshot"]["skills"]
    assert context["_finance_skill_registry_error"]
    with pytest.raises(ValueError, match="无权"):
        agent._runtime_context(application_context={}, owner_id="alice", explicit_skill_ids=["personal-report"])


def test_real_report_package_loads_before_its_selected_reference(hub, tmp_path):
    agent = service(hub)
    context = agent._runtime_context(application_context={}, owner_id="alice")
    tools_service = FinanceDataQueryCcTools(
        finance_runtime=SimpleNamespace(),
        finance_catalog=SimpleNamespace(catalog_revision=lambda: "", build_tree=lambda: {"subjects": []}),
        result_store=SessionVariableStoreService(data_root=tmp_path / "results"),
    )
    runtime = tools_service.create_runtime()
    definitions, _, tracker = tools_service.build_tools(owner_ids=["alice"], tool_context=context, runtime=runtime)
    tools = {tool.name: tool for tool in definitions}
    def call(name, args):
        return json.loads(asyncio.run(tools[name].handler(args))["content"][0]["text"])
    reference = {"skill_id": "equity-report-analysis", "reference": "references/consensus-disagreement.md"}
    assert "error" in call("read_finance_skill_reference", reference)
    method = call("read_finance_skill", {"skill_id": "equity-report-analysis"})
    assert "默认方法" in method["method"]
    assert "error" not in call("read_finance_skill_reference", reference)
    assert tracker["active_skill_ids"] == ["equity-report-analysis"]
    assert tracker["skill_entries"][0]["content_hash"] == method["content_hash"]
    assert "finance_query" in tools and "read_finance_catalog" in tools
    assert "error" in call("read_finance_skill", {"skill_id": "not-registered"})
    # A missing method leaves data tools and already loaded guidance available.
    assert tracker["active_skill_ids"] == ["equity-report-analysis"]


def test_empty_query_does_not_erase_skill_guided_conditional_answer(hub):
    session = Session({
        "result": "当前可读研报未覆盖管理层履职，已有预测不能支持管理层评价。",
        "error": "", "skill_entries": [{"skill_id": "equity-report-analysis"}],
        "result_refs": [{"row_count": 0, "sample": {"rows": []}, "data_type": "table"}],
    })
    agent = service(hub, session)
    result = agent.answer(thread_id=1, turn_id=1, owner_id="alice", user_text="管理层如何？",
        explicit_skill_ids=["equity-report-analysis"], dispatch_plan={
            "selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route",
        })
    assert result["message"] == session.result["result"]
    assert session.calls[0]["context"]["_finance_explicit_skill_ids"] == ["equity-report-analysis"]
