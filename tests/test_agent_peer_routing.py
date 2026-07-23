import json

from src.services.application_runtime_service import ApplicationRuntimeService
from src.services.agent_direct_response_service import AgentDirectResponseService
from src.services import runtime_conversation_service as runtime_module
from src.web import flask_app as web_module


class _ContextCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _sql, _params):
        return None

    def fetchall(self):
        return [
            (
                7,
                "初二学生怎么理解一次函数的斜率？",
                json.dumps({}, ensure_ascii=False),
                "这是一段超过十个汉字的完整回答，不应全部进入上下文。",
                json.dumps({}, ensure_ascii=False),
            )
        ]


class _ContextConnection:
    def cursor(self):
        return _ContextCursor()


class _ContextDb:
    def __init__(self):
        self.conn = _ContextConnection()

    def close_db(self):
        return None


def test_context_window_keeps_full_question_and_ten_character_answer_preview(monkeypatch):
    monkeypatch.setattr(runtime_module, "SystemDbUtils", _ContextDb)

    window = runtime_module.RuntimeConversationService().get_context_window(thread_id=1, max_rounds=5)

    assert window[0]["role"] == "user"
    assert window[0]["text"] == "初二学生怎么理解一次函数的斜率？"
    assert window[1]["role"] == "assistant"
    assert window[1]["text"] == "这是一段超过十个汉字…"


def test_application_exposes_three_peer_agents_and_one_default():
    context = ApplicationRuntimeService().get_application_context("investment_workbench")

    assert "assistant_agent" not in context
    assert "execution_agent" not in context
    assert context["default_agent"]["agent_name"] == "investment_analyst"
    assert [item["agent_name"] for item in context["available_agents"]] == [
        "default_assistant",
        "investment_analyst",
        "education_research_agent",
    ]


def test_education_agent_returns_configured_placeholder_without_planning(monkeypatch):
    def fail_if_planned(**_kwargs):
        raise AssertionError("static education placeholder must not invoke agent planning")

    monkeypatch.setattr(web_module.agent_execution_service, "preview_route", fail_if_planned)
    application_context = ApplicationRuntimeService().get_application_context("investment_workbench")

    result = web_module._build_chat_dispatch_payload(
        "初二学生怎么理解一次函数？",
        application_context=application_context,
        thread_context={},
        precomputed_plan={
            "entry": "agent_route",
            "selected_agent": "education_research_agent",
            "turn_mode": "normal_qa",
        },
    )

    assert result["mode"] == "agent_static_response"
    assert result["message"] == "你好，我是教研 Agent，还未实现。"


def test_direct_agent_response_uses_agent_profile_without_rerouting():
    calls = []

    def fake_llm(messages, enable_think=False):
        calls.append((messages, enable_think))
        return "你好！", {"total_tokens": 12}

    result = AgentDirectResponseService(llm_call=fake_llm).answer(
        user_text="你好",
        agent={
            "agent_name": "default_assistant",
            "display_name": "Default Assistant",
            "runtime_profile": {"system_prompt_text": "回答通用问题，不负责路由。"},
        },
    )

    assert result["message"] == "你好！"
    assert calls[0][1] is False
    assert "回答通用问题，不负责路由" in calls[0][0][0]["content"]
    assert calls[0][0][1]["content"] == "你好\n"


def test_default_agent_direct_answer_does_not_enter_finance_planner(monkeypatch):
    def fail_if_planned(**_kwargs):
        raise AssertionError("general agent must not invoke finance planning")

    monkeypatch.setattr(web_module.agent_execution_service, "preview_route", fail_if_planned)
    monkeypatch.setattr(
        web_module.agent_direct_response_service,
        "answer",
        lambda **_kwargs: {"message": "你好！", "llm_usage": {"total_tokens": 8}},
    )
    application_context = ApplicationRuntimeService().get_application_context("investment_workbench")

    result = web_module._build_chat_dispatch_payload(
        "你好",
        application_context=application_context,
        thread_context={},
        precomputed_plan={
            "entry": "agent_route",
            "selected_agent": "default_assistant",
            "turn_mode": "normal_qa",
        },
    )

    assert result["mode"] == "agent_direct_response"
    assert result["message"] == "你好！"


def test_natural_language_tool_development_starts_a_first_round_without_active_state(monkeypatch):
    calls = []

    monkeypatch.setattr(
        web_module.custom_tool_agent_service,
        "start_create",
        lambda text, **kwargs: calls.append({"text": text, **kwargs}) or {
            "message": "已开始设计。",
            "state": {"status": "awaiting_design_confirmation", "design_round": 1},
        },
    )
    monkeypatch.setattr(web_module, "_apply_application_workspace_orchestration", lambda payload, _context: payload)
    application_context = ApplicationRuntimeService().get_application_context("investment_workbench")

    result = web_module._build_chat_dispatch_payload(
        "帮我创建一个选股工具，逻辑是近期放量上涨。",
        application_context=application_context,
        thread_context={},
        thread_id=12,
        turn_id=34,
        precomputed_plan={
            "entry": "custom_tool_flow",
            "selected_agent": "investment_analyst",
            "turn_mode": "tool_development",
        },
    )

    assert calls == [{
        "text": "帮我创建一个选股工具，逻辑是近期放量上涨。",
        "owner_id": "12",
        "thread_id": 12,
        "turn_id": 34,
    }]
    assert result["mode"] == "custom_tool_flow"
    assert result["thread_context_patch"]["custom_tool_state"]["design_round"] == 1
