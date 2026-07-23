import json
import os
from pathlib import Path

import pytest

from src.services.application_runtime_service import ApplicationRuntimeService
from src.services.conversation_preprocess_service import ConversationPreprocessService


DATASET_PATH = Path("tests/evals/conversation_intent_v1.json")


def _dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


class _EvalPlannerStub:
    def build_plan(self, **kwargs):
        return {
            "source": "eval_stub",
            "execution_plan": {
                "plan_type": "tool_plan_run",
                "selected_path": {
                    "type": "tool_plan_run",
                    "target": {"type": "tool_group", "name": "eval_stub"},
                },
                "work_items": [],
            },
        }


def _assert_llm_eval_result(case, result):
    expected = case["expected"]
    dispatch = result["dispatch_plan"]
    resolved = result["normalized_request"]
    resolved_question = str(resolved.get("resolved_question") or "")
    context_refs = resolved.get("context_refs") if isinstance(resolved.get("context_refs"), list) else []

    if expected.get("agent_name"):
        assert dispatch["selected_agent"] == expected["agent_name"]
    if expected.get("agent_name_in"):
        assert dispatch["selected_agent"] in expected["agent_name_in"]
    if expected.get("turn_mode"):
        assert dispatch["turn_mode"] == expected["turn_mode"]
    if expected.get("forbidden_turn_modes"):
        assert dispatch["turn_mode"] not in expected["forbidden_turn_modes"]
    for fragment in expected.get("resolved_contains_all") or []:
        assert fragment in resolved_question
    for alternatives in expected.get("resolved_contains_any_groups") or []:
        assert any(fragment in resolved_question for fragment in alternatives)
    if isinstance(expected.get("max_context_refs"), int):
        assert len(context_refs) <= expected["max_context_refs"]
    for prefix in expected.get("context_ref_prefixes") or []:
        assert any(str(ref).startswith(prefix) for ref in context_refs)


def test_eval_dataset_has_required_coverage_and_unique_ids():
    payload = _dataset()
    cases = payload["cases"]
    case_ids = [item["case_id"] for item in cases]
    categories = {item["category"] for item in cases}

    assert len(cases) >= 12
    assert len(case_ids) == len(set(case_ids))
    assert {"happy_path", "context_resolution", "rule_path", "boundary", "missing_evidence"} <= categories
    assert all(item.get("input") and isinstance(item.get("expected"), dict) for item in cases)


@pytest.mark.parametrize(
    "case",
    [
        item
        for item in _dataset()["cases"]
        if (item.get("expected") or {}).get("llm_policy") == "forbidden"
    ],
    ids=lambda item: item["case_id"],
)
def test_rule_only_eval_cases_never_call_context_or_intent_llm(monkeypatch, case):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("slash rule case must not call an LLM")

    monkeypatch.setattr("src.services.context_resolution_service.chat_qwen_flash_json", fail_if_called)
    monkeypatch.setattr("src.services.assistant_interaction_preprocessor.chat_qwen_flash_json", fail_if_called)
    application_context = ApplicationRuntimeService().get_application_context("investment_workbench")

    result = ConversationPreprocessService().preprocess(
        text=case["input"],
        attachments=case.get("attachments") or [],
        thread_context=case.get("thread_context") or {},
        application_context=application_context,
        enable_llm=True,
    )

    expected = case["expected"]
    assert result["dispatch_plan"]["selected_agent"] == expected["agent_name"]
    assert result["dispatch_plan"]["turn_mode"] == expected["turn_mode"]
    assert result["interaction"]["source"] == "rule:slash_command"
    assert result["llm_usage"]["call_count"] == 0


@pytest.mark.skipif(
    os.getenv("RUN_LLM_INTENT_EVALS") != "1",
    reason="set RUN_LLM_INTENT_EVALS=1 to run real MaaS intent evaluations",
)
@pytest.mark.parametrize(
    "case",
    [
        item
        for item in _dataset()["cases"]
        if (item.get("expected") or {}).get("llm_policy") == "required"
    ],
    ids=lambda item: item["case_id"],
)
def test_real_llm_intent_eval_cases(case):
    application_context = ApplicationRuntimeService().get_application_context("investment_workbench")
    result = ConversationPreprocessService(
        agent_runtime_llm_planner_service=_EvalPlannerStub(),
    ).preprocess(
        text=case["input"],
        attachments=case.get("attachments") or [],
        thread_context=case.get("thread_context") or {},
        application_context=application_context,
        enable_llm=True,
    )

    _assert_llm_eval_result(case, result)
