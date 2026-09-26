from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.assistant_interaction_preprocessor import (
    AssistantInteractionPreprocessError,
    AssistantInteractionPreprocessor,
)
from src.services.context_resolution_service import ContextResolutionError, ContextResolutionService
from src.services.conversation_preprocess_service import ConversationPreprocessService


APPLICATION_CONTEXT = {
    "application_name": "thought_experiment",
    "default_agent": {"agent_name": "investment_analyst"},
    "available_agents": [
        {"agent_name": "default_assistant", "role": "fallback"},
        {"agent_name": "investment_analyst", "role": "finance"},
        {"agent_name": "education_research_agent", "role": "education"},
    ],
}


class _ContextResolverStub:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        text = str(kwargs.get("user_text") or "")
        if text == "那么五粮液呢？":
            return {
                "ori_question": text,
                "resolved_question": "查询五粮液昨日的开盘价。",
                "context_refs": ["turn:1"],
                "source": "stub",
            }
        if text == "打开第二个":
            return {
                "ori_question": text,
                "resolved_question": "打开上一轮 /tools 返回的工具列表中的第二个工具。",
                "context_refs": ["turn:1"],
                "source": "stub",
            }
        return {
            "ori_question": text,
            "resolved_question": text,
            "context_refs": [],
            "source": "stub",
        }


class _TopIntentStub:
    def __init__(self) -> None:
        self.calls = []

    def classify(self, **kwargs):
        self.calls.append(kwargs)
        text = str((kwargs.get("context_resolution") or {}).get("resolved_question") or "")
        command = kwargs.get("command_signal") or {}
        if str(command.get("action") or "") == "custom_tool":
            agent_name, turn_mode = "investment_analyst", "tool_development"
        elif "运行一下行情工具" in text or ("打开" in text and "工具列表" in text):
            agent_name, turn_mode = "default_assistant", "system_operation"
        elif "流程图" in text:
            agent_name, turn_mode = "investment_analyst", "tool_development"
        elif "工具" in text:
            agent_name, turn_mode = "investment_analyst", "tool_development"
        elif "二次函数" in text:
            agent_name, turn_mode = "education_research_agent", "normal_qa"
        else:
            agent_name, turn_mode = "investment_analyst", "normal_qa"
        return {
            "agent_name": agent_name,
            "turn_mode": turn_mode,
            "agent_hint": agent_name,
            "domain_hint": "business",
            "needs_reference_resolution": False,
            "info_ready": True,
            "source": "stub",
        }


class _RulePlannerStub:
    def build_agent_route_plan(self, **kwargs):
        return {
            "selected_path": {
                "type": "agent_route",
                "target": {"type": "agent", "name": kwargs.get("target_agent")},
            },
            "work_items": [],
        }

    def build_business_dialog_plan(self, **kwargs):
        return {
            "selected_path": {
                "type": "tool_plan_run",
                "target": {"type": "tool_group", "name": "direct_tools"},
            },
            "work_items": [],
        }


class _LlmPlannerStub:
    def build_plan(self, **kwargs):
        return {
            "source": "stub",
            "execution_plan": {
                "selected_path": {
                    "type": "tool_plan_run",
                    "target": {"type": "tool_group", "name": "direct_tools"},
                },
                "work_items": [],
            },
        }


def _service():
    context = _ContextResolverStub()
    intent = _TopIntentStub()
    service = ConversationPreprocessService(
        context_resolution_service=context,
        interaction_preprocessor=intent,
        agent_runtime_planner=_RulePlannerStub(),
        agent_runtime_llm_planner_service=_LlmPlannerStub(),
    )
    return service, context, intent


def test_context_protocol_keeps_large_attachment_by_reference():
    service = ContextResolutionService()
    result = service.resolve(
        user_text="请总结这份文件",
        context_window=[],
        thread_state={},
        preprocessing_signals={
            "attachment_signals": [
                {
                    "attachment_ids": ["report_1024"],
                    "kind": "document",
                    "summary": "新能源汽车行业深度研报.pdf",
                }
            ]
        },
        enable_llm=False,
    )

    assert result["ori_question"] == "请总结这份文件"
    assert result["resolved_question"] == "请总结这份文件"
    assert result["context_refs"] == ["attachment:report_1024"]
    assert "新能源汽车行业深度研报.pdf" not in result["resolved_question"]


def test_top_intent_uses_one_llm_call_for_both_orthogonal_dimensions(monkeypatch):
    calls = []

    def fake_chat(messages, enable_think=False):
        calls.append({"messages": messages, "enable_think": enable_think})
        return {"agent_name": "education_research_agent", "turn_mode": "normal_qa"}, {}

    monkeypatch.setattr("src.services.assistant_interaction_preprocessor.chat_qwen_flash_json", fake_chat)
    result = AssistantInteractionPreprocessor().classify(
        user_text="帮我出一道二次函数题",
        context_resolution={
            "ori_question": "帮我出一道二次函数题",
            "resolved_question": "帮我出一道二次函数题",
            "context_refs": [],
        },
        application_context=APPLICATION_CONTEXT,
    )

    assert len(calls) == 1
    assert result["agent_name"] == "education_research_agent"
    assert result["turn_mode"] == "normal_qa"


def test_top_intent_prompt_exposes_complete_soft_routing_context(monkeypatch):
    captured = []
    application_config = json.loads(
        Path("src/applications/investment_workbench/application.json").read_text(encoding="utf-8")
    )
    agent_configs = {
        name: json.loads(Path(f"src/agents/{name}/agent.json").read_text(encoding="utf-8"))
        for name in ("default_assistant", "investment_analyst")
    }
    application_context = {
        "application_name": application_config["name"],
        "display_name": application_config["display_name"],
        "application_config": application_config,
        "default_agent": {
            "agent_name": application_config["default_agent"],
        },
        "available_agents": [
            {
                "agent_name": name,
                "display_name": config["display_name"],
                "role": config["role"],
                "config": config,
            }
            for name, config in agent_configs.items()
        ],
    }

    def fake_chat(messages, enable_think=False):
        captured.extend(messages)
        return {"agent_name": "investment_analyst", "turn_mode": "normal_qa"}, {}

    monkeypatch.setattr("src.services.assistant_interaction_preprocessor.chat_qwen_flash_json", fake_chat)
    AssistantInteractionPreprocessor().classify(
        user_text="查询今天活跃的热点事件",
        context_resolution={
            "ori_question": "查询今天活跃的热点事件",
            "resolved_question": "查询今天活跃的热点事件",
            "context_refs": [],
        },
        application_context=application_context,
    )

    rendered = "\n".join(str(item.get("content") or "") for item in captured)
    assert "市场热点事件与热点概念" in rendered
    assert "基金与ETF" in rendered
    assert "债券与可转债" in rendered
    assert "最低优先级兜底" in rendered
    assert "用户直接想得到什么" in rendered
    assert "内部选择并调用一个或多个 Tool/Skill" in rendered
    assert "'domain': 'market'" in rendered or '"domain": "market"' in rendered
    assert "investment_analyst" in rendered


def test_preprocess_does_not_count_context_resolution_usage_twice():
    service, context, intent = _service()
    original_resolve = context.resolve
    original_classify = intent.classify

    def resolve_with_usage(**kwargs):
        result = original_resolve(**kwargs)
        result["llm_usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "call_count": 1,
        }
        return result

    def classify_with_usage(**kwargs):
        result = original_classify(**kwargs)
        result["llm_usage"] = {
            "prompt_tokens": 200,
            "completion_tokens": 20,
            "total_tokens": 220,
            "call_count": 1,
        }
        return result

    context.resolve = resolve_with_usage
    intent.classify = classify_with_usage

    result = service.preprocess(
        text="贵州茅台今天的行情是什么？",
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert result["normalized_request"]["llm_usage"]["total_tokens"] == 110
    assert result["llm_usage"] == {
        "prompt_tokens": 300,
        "completion_tokens": 30,
        "total_tokens": 330,
        "call_count": 2,
    }


def test_top_intent_receives_compact_tool_facts_without_conversation_state(monkeypatch):
    calls = []

    def fake_chat(messages, enable_think=False):
        calls.append(messages)
        return {"agent_name": "investment_analyst", "turn_mode": "tool_development"}, {}

    monkeypatch.setattr("src.services.assistant_interaction_preprocessor.chat_qwen_flash_json", fake_chat)
    result = AssistantInteractionPreprocessor().classify(
        user_text="继续修复这个工具",
        thread_context={
            "conversation_state": {"state": "suspended"},
            "interaction_frame": {"interaction_mode": "resume_previous_task"},
            "custom_tool_state": {
                "status": "draft_needs_test",
                "tool_name": "golden_cross_30_60",
                "implementation_revision": 2,
                "design_contract": {
                    "display_name": "30/60 日金叉判断",
                    "flow": {"mermaid": "flowchart TD"},
                },
            },
        },
        context_resolution={
            "ori_question": "继续修复这个工具",
            "resolved_question": "继续修复当前的 30/60 日金叉判断工具。",
            "context_refs": ["custom_tool:golden_cross_30_60"],
        },
        application_context=APPLICATION_CONTEXT,
    )

    rendered = "\n".join(str(message.get("content") or "") for message in calls[0])
    assert result["turn_mode"] == "tool_development"
    assert "golden_cross_30_60" in rendered
    assert "has_design" in rendered
    assert "has_implementation" in rendered
    assert "has_failed_test" in rendered
    assert "conversation_state" not in rendered
    assert "interaction_frame" not in rendered


def test_tool_name_alone_does_not_advertise_code_or_tests() -> None:
    context = AssistantInteractionPreprocessor()._build_active_context(
        thread_context={
            "custom_tool_state": {
                "tool_name": "ct_design_only",
                "design_contract": {
                    "display_name": "仅完成设计的工具",
                    "flow": {"mermaid": "flowchart TD"},
                },
            }
        },
        application_context=APPLICATION_CONTEXT,
    )

    assert context is not None
    assert context["has_design"] is True
    assert context["has_implementation"] is False
    assert context["available_assets"] == ["design", "flow"]


def test_context_resolution_llm_failure_is_reported_without_fallback(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("maas unavailable")

    monkeypatch.setattr("src.services.context_resolution_service.chat_qwen_flash_json_with_raw", fail)

    with pytest.raises(ContextResolutionError, match="maas unavailable"):
        ContextResolutionService().resolve(
            user_text="那么五粮液呢？",
            context_window=[
                {"round": 1, "role": "user", "text": "查询贵州茅台昨日开盘价"},
                {"round": 1, "role": "assistant", "text": "已返回贵州茅台结果"},
            ],
            thread_state={},
            preprocessing_signals={},
            enable_llm=True,
        )


def test_context_resolution_protocol_error_is_reported_without_raw_text_fallback(monkeypatch):
    monkeypatch.setattr(
        "src.services.context_resolution_service.chat_qwen_flash_json_with_raw",
        lambda messages, enable_think=False, temperature=0.0: ({"context_refs": []}, {}, '{"context_refs": []}'),
    )

    with pytest.raises(ContextResolutionError, match="连续两次"):
        ContextResolutionService().resolve(
            user_text="那么五粮液呢？",
            context_window=[
                {"round": 1, "role": "user", "text": "查询贵州茅台昨日开盘价"},
                {"round": 1, "role": "assistant", "text": "已返回贵州茅台结果"},
            ],
            thread_state={},
            preprocessing_signals={},
            enable_llm=True,
        )


def test_context_resolution_retries_bad_json_and_keeps_original_text_in_system(monkeypatch):
    calls = []

    def fake_chat(messages, enable_think=False, temperature=0.0):
        calls.append(messages)
        if len(calls) == 1:
            return None, {}, '{"resolved_question": "关于「"前高"」继续设计"}'
        return {
            "resolved_question": "采用已确认的前高定义，继续完成当前工具设计。",
            "context_refs": ["custom_tool:current"],
        }, {}, '{"resolved_question":"采用已确认的前高定义，继续完成当前工具设计。","context_refs":["custom_tool:current"]}'

    monkeypatch.setattr(
        "src.services.context_resolution_service.chat_qwen_flash_json_with_raw",
        fake_chat,
    )
    original = '关于「"前高"如何定义？」，我的回答是：最近波段高点。'
    result = ContextResolutionService().resolve(
        user_text=original,
        context_window=[],
        thread_state={
            "active_workflow": {"type": "custom_tool_authoring"},
            "context_objects": [
                {
                    "context_ref": "custom_tool:current",
                    "summary": "当前工具设计",
                }
            ],
        },
        preprocessing_signals={},
        enable_llm=True,
    )

    assert len(calls) == 2
    assert result["ori_question"] == original
    assert result["resolved_question"] == "采用已确认的前高定义，继续完成当前工具设计。"
    rendered = "\n".join(str(item.get("content") or "") for item in calls[0])
    assert '\\"前高\\"' in rendered


def test_top_intent_llm_failure_is_reported_without_normal_qa_fallback(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("router unavailable")

    monkeypatch.setattr("src.services.assistant_interaction_preprocessor.chat_qwen_flash_json", fail)

    with pytest.raises(AssistantInteractionPreprocessError, match="router unavailable"):
        AssistantInteractionPreprocessor().classify(
            user_text="宁德时代现在股价是多少？",
            context_resolution={
                "ori_question": "宁德时代现在股价是多少？",
                "resolved_question": "宁德时代现在股价是多少？",
                "context_refs": [],
            },
            application_context=APPLICATION_CONTEXT,
        )


def test_top_intent_protocol_error_is_reported_without_default_mode(monkeypatch):
    monkeypatch.setattr(
        "src.services.assistant_interaction_preprocessor.chat_qwen_flash_json",
        lambda messages, enable_think=False: ({"agent_name": "investment_analyst"}, {}),
    )

    with pytest.raises(AssistantInteractionPreprocessError, match="turn_mode"):
        AssistantInteractionPreprocessor().classify(
            user_text="宁德时代现在股价是多少？",
            context_resolution={
                "ori_question": "宁德时代现在股价是多少？",
                "resolved_question": "宁德时代现在股价是多少？",
                "context_refs": [],
            },
            application_context=APPLICATION_CONTEXT,
        )


def test_context_resolution_runs_before_top_intent_and_supplies_resolved_question():
    service, context, intent = _service()
    result = service.preprocess(
        text="那么五粮液呢？",
        thread_context={
            "context_window": [
                {"round": 1, "role": "user", "text": "贵州茅台昨日开盘价是多少？"},
                {"round": 1, "role": "assistant", "text": "已查询贵州茅台昨日开盘价。"},
            ]
        },
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert len(context.calls) == 1
    assert len(intent.calls) == 1
    assert intent.calls[0]["context_resolution"]["resolved_question"] == "查询五粮液昨日的开盘价。"
    assert result["normalized_request"]["resolved_question"] == "查询五粮液昨日的开盘价。"
    assert result["dispatch_plan"]["selected_agent"] == "investment_analyst"
    assert result["dispatch_plan"]["turn_mode"] == "normal_qa"


def test_active_tool_context_does_not_lock_a_new_finance_question():
    service, _, intent = _service()
    result = service.preprocess(
        text="宁德时代现在股价是多少？",
        thread_context={
            "custom_tool_state": {
                "status": "awaiting_design_confirmation",
                "tool_name": "golden_cross_30_60",
            }
        },
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert len(intent.calls) == 1
    assert result["dispatch_plan"]["selected_agent"] == "investment_analyst"
    assert result["dispatch_plan"]["turn_mode"] == "normal_qa"
    assert result["dispatch_plan"]["entry"] == "tool_plan_run"


def test_active_tool_reference_routes_to_real_agent_tool_development_mode():
    service, _, _ = _service()
    result = service.preprocess(
        text="这个工具的流程图再给我看一下",
        thread_context={
            "custom_tool_state": {
                "status": "awaiting_design_confirmation",
                "tool_name": "golden_cross_30_60",
            }
        },
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert result["dispatch_plan"]["selected_agent"] == "investment_analyst"
    assert result["dispatch_plan"]["turn_mode"] == "tool_development"
    assert result["dispatch_plan"]["entry"] == "custom_tool_flow"


def test_available_agent_catalog_accepts_education_agent_without_code_enum():
    service, _, _ = _service()
    result = service.preprocess(
        text="帮我给高一学生出一道二次函数题",
        thread_context={},
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert result["dispatch_plan"]["selected_agent"] == "education_research_agent"
    assert result["dispatch_plan"]["turn_mode"] == "normal_qa"


def test_custom_tool_slash_command_uses_rule_without_top_intent_llm():
    service, context, intent = _service()
    result = service.preprocess(
        text="/custom_tool create 创建一个金叉判断工具",
        thread_context={},
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert len(intent.calls) == 0
    assert context.calls[0]["enable_llm"] is False
    assert result["interaction"]["source"] == "rule:slash_command"
    assert result["dispatch_plan"]["selected_agent"] == "investment_analyst"
    assert result["dispatch_plan"]["turn_mode"] == "tool_development"
    assert result["dispatch_plan"]["entry"] == "custom_tool_flow"


@pytest.mark.parametrize(
    ("text", "expected_mode"),
    [
        ("/custom_tool create 创建工具", "tool_development"),
        ("/custom_tool edit 调整阈值", "tool_development"),
        ("/custom_tool call demo {}", "system_operation"),
        ("/custom_tool commit demo", "system_operation"),
        ("/tools", "system_operation"),
        ("/run_skill demo", "system_operation"),
        ("/code 请用 Python 聚合表格", "system_operation"),
        ("/unknown_command", "system_operation"),
    ],
)
def test_slash_command_turn_mode_is_fully_rule_based(text, expected_mode):
    service, context, intent = _service()

    result = service.preprocess(
        text=text,
        thread_context={},
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert len(intent.calls) == 0
    assert context.calls[0]["enable_llm"] is False
    assert result["dispatch_plan"]["turn_mode"] == expected_mode


def test_natural_language_uses_top_intent_result_without_hidden_override():
    service, _, _ = _service()

    result = service.preprocess(
        text="帮我创建一个金叉判断工具",
        thread_context={},
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert result["interaction"]["turn_mode"] == "tool_development"
    assert result["dispatch_plan"]["turn_mode"] == "tool_development"
    assert result["dispatch_plan"]["entry"] == "custom_tool_flow"


def test_natural_language_can_continue_a_previous_system_slash_operation():
    service, _, intent = _service()

    result = service.preprocess(
        text="打开第二个",
        thread_context={
            "context_window": [
                {"round": 1, "role": "user", "text": "/tools"},
                {"round": 1, "role": "assistant", "text": "已展示工具列表。"},
            ]
        },
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert len(intent.calls) == 1
    assert result["normalized_request"]["resolved_question"] == "打开上一轮 /tools 返回的工具列表中的第二个工具。"
    assert result["dispatch_plan"]["turn_mode"] == "system_operation"


def test_natural_language_can_request_a_system_tool_operation():
    service, _, intent = _service()

    result = service.preprocess(
        text="运行一下行情工具查询宁德时代",
        thread_context={},
        application_context=APPLICATION_CONTEXT,
        enable_llm=True,
    )

    assert len(intent.calls) == 1
    assert result["dispatch_plan"]["turn_mode"] == "system_operation"
