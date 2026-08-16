from __future__ import annotations

from src.scenarios.financial_qa.research_mode import (
    normalize_research_mode,
    research_mode_metadata,
    research_mode_prompt,
)
from src.scenarios.financial_qa.service import FinancialQaCcService
from src.services.finance_claude_session_service import FinanceClaudeSessionService


def test_research_mode_defaults_to_skill_owned_intelligent_analysis() -> None:
    assert normalize_research_mode(None) == "auto"
    assert research_mode_metadata("auto") == {
        "requested": "auto",
        "label": "智能分析",
        "decision_owner": "skill",
    }
    prompt = research_mode_prompt("auto")
    assert "由匹配的业务 Skill" in prompt
    assert "用户完整语义" in prompt
    assert "深度分析、完整研究报告" in prompt
    assert "不是根据孤立关键词机械分类" in prompt
    assert "不要新增独立分类轮次" in prompt


def test_explicit_research_modes_are_user_owned_hard_constraints() -> None:
    assert research_mode_metadata("fast")["decision_owner"] == "user"
    assert research_mode_metadata("deep")["decision_owner"] == "user"
    assert "显式约束" in research_mode_prompt("fast")
    assert "显式约束" in research_mode_prompt("deep")


def test_invalid_research_mode_is_rejected() -> None:
    try:
        normalize_research_mode("standard")
    except ValueError as exc:
        assert "fast、auto 或 deep" in str(exc)
    else:
        raise AssertionError("invalid research mode must be rejected")


def test_chat_api_rejects_an_invalid_research_mode_before_starting_work() -> None:
    from src.web import flask_app as web

    client = web.app.test_client()
    dispatch = client.post(
        "/api/chat/dispatch",
        json={"text": "分析贵州茅台", "research_mode": "standard"},
    )
    stream = client.post(
        "/api/chat/stream/start",
        json={"text": "分析贵州茅台", "research_mode": "standard"},
    )

    assert dispatch.status_code == 400
    assert stream.status_code == 400
    assert "fast、auto 或 deep" in dispatch.get_json()["error"]
    assert "fast、auto 或 deep" in stream.get_json()["error"]


def test_finance_cc_prompt_receives_the_per_turn_mode_without_changing_system_options() -> None:
    service = FinancialQaCcService(enabled=True)
    try:
        context = service._runtime_context(
            application_context={},
            research_mode="fast",
        )
        user_prompt = FinanceClaudeSessionService.build_user_prompt(
            "分析贵州茅台",
            context,
        )
        options = service.session_service._runtime_options(context)

        assert context["_finance_research_mode"] == "fast"
        assert "[系统记录的本轮研究模式]" in user_prompt
        assert "快速回答" in user_prompt
        assert "快速回答" not in options["system_prompt"]
    finally:
        service.close()


def test_loaded_skill_can_upgrade_runtime_budget_without_extending_normal_qa(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FINANCE_CC_TURN_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("FINANCE_CC_LONG_TURN_TIMEOUT_SECONDS", "420")
    service = FinancialQaCcService(enabled=True)
    try:
        context = service._runtime_context(application_context={})

        assert context["_finance_skill_execution_budget"]["stock-research"] == "long"
        assert FinanceClaudeSessionService._turn_timeout_seconds(context) == 180
        assert (
            FinanceClaudeSessionService._turn_timeout_seconds(
                context,
                activated_skill_id="earnings-analysis",
            )
            == 180
        )
        assert (
            FinanceClaudeSessionService._turn_timeout_seconds(
                context,
                activated_skill_id="stock-research",
            )
            == 420
        )
    finally:
        service.close()


class _Session:
    def __init__(self) -> None:
        self.calls = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "session_id": "cc-session",
            "resumed": False,
            "duration_ms": 10,
            "result": "分析完成。",
            "error": "",
            "result_refs": [],
        }

    def close(self) -> None:
        return None


def test_financial_qa_answer_echoes_the_mode_and_passes_it_to_the_skill_context() -> None:
    session = _Session()
    service = FinancialQaCcService(enabled=True, session_service=session)
    result = service.answer(
        thread_id=7,
        turn_id=8,
        owner_id="owner-a",
        user_text="分析贵州茅台",
        research_mode="deep",
        dispatch_plan={
            "selected_agent": "investment_analyst",
            "turn_mode": "normal_qa",
            "entry": "agent_route",
            "semantic_turn": {"resolved_question": "分析贵州茅台"},
        },
    )

    assert result["research_mode"] == {
        "requested": "deep",
        "label": "深度研究",
        "decision_owner": "user",
    }
    assert session.calls[0]["context"]["_finance_research_mode"] == "deep"
    assert "深度研究" in session.calls[0]["context"]["_finance_research_mode_prompt"]
