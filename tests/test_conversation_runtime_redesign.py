import json
import re

import pytest

from src.services.assistant_interaction_preprocessor import AssistantInteractionPreprocessor
from src.services.answer_summary_service import AnswerSummaryService
from src.services.context_resolution_service import ContextResolutionError, ContextResolutionService
from src.services.conversation_preprocess_service import ConversationPreprocessService
from src.services.conversation_task_finalizer_service import ConversationTaskFinalizerService


@pytest.fixture(autouse=True)
def _stub_context_resolution_llm(monkeypatch):
    def fake_chat(messages, enable_think=False, temperature=0.0):
        content = str(messages[-1].get("content") or "") if messages else ""
        match = re.search(r"待处理数据，不是附加指令：\s*(\"(?:\\\\.|[^\"])*\")", content, re.S)
        question = json.loads(match.group(1)) if match else "测试问题"
        return {
            "resolved_question": question,
            "context_refs": [],
        }, {}, '{"resolved_question":"测试问题","context_refs":[]}'

    monkeypatch.setattr(
        "src.services.context_resolution_service.chat_qwen_flash_json_with_raw",
        fake_chat,
    )


def test_interaction_preprocessor_normalizes_minimal_top_level_payload():
    preprocessor = AssistantInteractionPreprocessor()

    normalized = preprocessor._normalize_payload(  # noqa: SLF001
        {
            "agent_name": "default_assistant",
            "turn_mode": "system_operation",
            "analize": "用户在显式操作 skill，属于 system。",
            "domain_hint": "system",
            "agent_hint": "default_assistant",
            "needs_reference_resolution": True,
            "info_ready": False,
        }
    )

    assert normalized["analize"] == "用户在显式操作 skill，属于 system。"
    assert normalized["domain_hint"] == "system"
    assert normalized["agent_hint"] == "default_assistant"
    assert normalized["needs_reference_resolution"] is True
    assert normalized["info_ready"] is False


class _StubInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "default_assistant",
            "turn_mode": "system_operation",
            "analize": "用户在显式操作 skill，属于 system。",
            "domain_hint": "system",
            "agent_hint": "default_assistant",
            "needs_reference_resolution": True,
            "info_ready": False,
            "source": "stub",
        }


class _StubFollowupAckInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "investment_analyst",
            "turn_mode": "normal_qa",
            "analize": "用户接受上一轮建议，仍在继续当前业务分析。",
            "domain_hint": "business",
            "agent_hint": "investment_analyst",
            "needs_reference_resolution": True,
            "info_ready": False,
            "source": "stub",
        }


class _StubReferentialInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "investment_analyst",
            "turn_mode": "normal_qa",
            "analize": "用户使用代词引用前文对象，需要先提取前文依赖信息。",
            "domain_hint": "business",
            "agent_hint": "investment_analyst",
            "needs_reference_resolution": True,
            "info_ready": False,
            "source": "stub",
        }


class _StubCorrectiveInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "investment_analyst",
            "turn_mode": "normal_qa",
            "analize": "用户在纠正或质疑前一轮结果，需要结合前文理解真实目标。",
            "domain_hint": "business",
            "agent_hint": "investment_analyst",
            "needs_reference_resolution": True,
            "info_ready": False,
            "source": "stub",
        }


class _StubGeneralBusinessInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "default_assistant",
            "turn_mode": "normal_qa",
            "analize": "这是一个普通业务问答，不依赖前文。",
            "domain_hint": "business",
            "agent_hint": "default_assistant",
            "needs_reference_resolution": False,
            "info_ready": True,
            "source": "stub",
        }


class _StubExecutionBusinessInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "investment_analyst",
            "turn_mode": "normal_qa",
            "analize": "这是一个需要进入业务规划与执行主链的结构化业务任务。",
            "domain_hint": "business",
            "agent_hint": "investment_analyst",
            "needs_reference_resolution": False,
            "info_ready": True,
            "source": "stub",
        }


class _StubReadyExecutionInteractionPreprocessor:
    def classify(self, **_: object) -> dict:
        return {
            "agent_name": "investment_analyst",
            "turn_mode": "normal_qa",
            "analize": "这是一个独立完整的新业务问题，但可能需要复杂规划。",
            "domain_hint": "business",
            "agent_hint": "investment_analyst",
            "needs_reference_resolution": False,
            "info_ready": True,
            "source": "stub",
        }


class _StubPlannerService:
    def __init__(self) -> None:
        self.last_user_objective = ""
        self.last_tool_queries = []

    def build_plan(self, **_: object) -> dict:
        self.last_user_objective = str(_.get("user_objective") or "")
        self.last_tool_queries = list(_.get("tool_queries") or [])
        return {
            "source": "stub_llm_planner",
            "structured_task": {
                "task_summary": "比较热点并总结",
                "primary_goal": "比较热点并总结",
                "expected_output": "结构化对比结论",
                "subproblems": [
                    {"name": "collect", "kind": "lookup", "depends_on_previous": False},
                    {"name": "compare", "kind": "compare", "depends_on_previous": True},
                ],
            },
            "task_mode_result": {
                "task_mode": "planned",
                "reason": "path_known_or_matches_stable_method",
                "confidence": 0.75,
            },
            "thinking_mode_result": {
                "thinking_mode": "simple_thinking",
                "should_use_deep_plan": False,
            },
            "deep_plan_preview": {},
            "execution_plan": {
                "plan_type": "planned_run",
                "selected_path": {
                    "type": "planned_run",
                    "target": {"type": "planned_graph", "name": "planned_workflow"},
                    "reason": "stubbed_plan",
                },
                "work_items": [
                    {"step_id": "step_1", "type": "tool", "name": "get_hot_sectors_and_leaders", "status": "planned"}
                ],
                "clarification_needed": False,
            },
        }


class _RaisingPlannerService:
    def build_plan(self, **_: object) -> dict:  # pragma: no cover - should not be called
        raise AssertionError("LLM planner should not be called for fast business path")


class _StubIncompleteFastPlanner:
    def build_business_dialog_plan(self, **_: object) -> dict:
        return {
            "planner_type": "agent_runtime_planner",
            "objective": str(_.get("text") or ""),
            "domain": "business_dialog",
            "candidate_skills": [],
            "candidate_tools": [],
            "selected_path": {
                "type": "tool_plan_run",
                "target": {"type": "tool_group", "name": "direct_tools"},
                "reason": "stubbed_fast_path",
            },
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "stock_quote",
                    "status": "planned",
                    "input_binding": {},
                    "output_binding": {},
                }
            ],
            "presentation_plan": {},
            "status": "planned",
        }

    def build_agent_route_plan(self, **_: object) -> dict:
        return {
            "planner_type": "agent_runtime_planner",
            "selected_path": {"type": "agent_route", "target": {"type": "agent", "name": "default_assistant"}},
            "work_items": [],
            "presentation_plan": {},
            "status": "planned",
        }


class _TrackingPlannerService(_StubPlannerService):
    def __init__(self) -> None:
        super().__init__()
        self.called = False

    def build_plan(self, **kwargs: object) -> dict:
        self.called = True
        return super().build_plan(**kwargs)


class _StubContextResolutionService:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def resolve(self, **_: object) -> dict:
        return dict(self.payload)


def test_conversation_preprocess_returns_lean_main_payload():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubInteractionPreprocessor(),
    )

    result = service.preprocess(
        text="把刚才那个技能优化一下，热点里加上相关股票信息",
        thread_context={
            "active_skill_name": "hotspot_report_skill",
            "active_skill_canonical_name": "hotspot_report_skill",
        },
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert "runtime_flow" not in result
    assert "prompt_context_sections" not in result
    assert "trace" not in result
    assert result["thread_context_patch_preview"] == {}


def test_conversation_preprocess_exposes_mainline_runtime_nodes_without_state_machine():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubInteractionPreprocessor(),
    )

    result = service.preprocess(
        text="把刚才那个技能优化一下，热点里加上相关股票信息",
        thread_context={
            "active_skill_name": "hotspot_report_skill",
            "active_skill_canonical_name": "hotspot_report_skill",
        },
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    modules = result["runtime_modules"]
    node_results = result["runtime_node_results"]
    node_by_name = {item["node"]: item for item in node_results}

    assert result["runtime_contract"]["phase"] == "preprocess"
    assert [item["module"] for item in modules] == [
        "conversation_management",
        "task_analysis",
        "capability_selection",
        "execution_runtime",
    ]
    assert list(node_by_name) == [
        "context_resolution",
        "interaction_preprocess",
        "conversation_task_finalization",
        "dispatch_planning",
        "agent_runtime_planning",
        "runtime_execute",
        "observe_present_writeback",
    ]
    assert node_by_name["interaction_preprocess"]["output"]["turn_mode"] == "system_operation"
    assert node_by_name["interaction_preprocess"]["output"]["domain_hint"] == "system"
    assert node_by_name["dispatch_planning"]["output"]["entry"] == result["dispatch_plan"]["entry"]
    assert node_by_name["runtime_execute"]["status"] == "pending"
    assert result["runtime_feedback_protocol"]["loop_levels"] == ["node_internal_loop", "module_feedback_loop"]


def test_conversation_preprocess_exposes_chat_mainline_contract_without_prompt_tuning():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubReadyExecutionInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
    )

    result = service.preprocess(
        text="搜集一下今天热门行业和热点概念的龙头股，并对比他们的共同点和差异",
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    mainline = result["conversation_mainline"]

    assert mainline["schema_version"] == "conversation_mainline_contract.v1"
    assert mainline["chat_task_mode"] == "temporary_multi_step_task"
    assert mainline["runtime_lane"] == "tool_plan_runtime_loop"
    assert mainline["chat_boundaries"]["skill_authoring_visible"] is False
    assert mainline["tuning_boundary"]["status"] == "separated_not_tuned"
    assert mainline["responsibilities"]["planner"] == [
        "classify_round_task",
        "select_existing_skill_or_capability_family",
        "produce_high_level_execution_plan",
    ]


def test_build_work_context_keeps_thread_skill_and_recent_attachments_only():
    service = ConversationPreprocessService()

    work_context = service._build_work_context(  # noqa: SLF001
        thread_context={
            "active_skill_name": "hotspot_report_skill",
            "active_skill_canonical_name": "hotspot_report_skill",
            "last_image_attachment_ids": ["pre1:img1", "pre3:img2"],
            "last_image_summary": "图里提到了机器人",
            "recent_result_subject": "宁德时代",
        },
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
    )

    assert work_context == {
        "application_name": "investment_workbench",
        "default_agent": "investment_analyst",
        "thread_active_skill_name": "hotspot_report_skill",
        "thread_active_skill_canonical_name": "hotspot_report_skill",
        "recent_attachments": [
            {
                "attachment_ids": ["pre1:img1", "pre3:img2"],
                "kind": "image",
                "summary": "图里提到了机器人",
            }
        ],
        "recent_result_subject": "宁德时代",
    }


def test_build_context_window_prefers_thread_context_window_shape():
    service = ConversationPreprocessService()

    context_window = service._build_context_window(  # noqa: SLF001
        thread_context={
            "context_window": [
                {
                    "round": 6,
                    "role": "user",
                    "text": "帮我看看这张图",
                    "attachments": [
                        {
                            "attachment_id": "img_1",
                            "attachment_summary": "图片提到了工业富联和机器人概念",
                        }
                    ],
                },
                {
                    "round": 6,
                    "role": "assistant",
                    "text": "图片主要提到了工业富联和机器人概念。",
                    "attachments": [],
                },
            ]
        }
    )

    assert context_window[0]["round"] == 6
    assert context_window[0]["role"] == "user"
    assert context_window[0]["attachments"][0]["attachment_id"] == "img_1"
    assert "工业富联" in context_window[0]["attachments"][0]["attachment_summary"]


def test_context_resolution_service_uses_only_llm_output_for_group_reference(monkeypatch):
    service = ContextResolutionService()

    monkeypatch.setattr(
        "src.services.context_resolution_service.chat_qwen_flash_json_with_raw",
        lambda messages, enable_think=False, temperature=0.0: (
            {
                "resolved_question": "查询贵州茅台和五粮液各自的市盈率。",
                "context_refs": ["turn:1:assistant"],
            },
            {},
            '{"resolved_question":"查询贵州茅台和五粮液各自的市盈率。","context_refs":["turn:1:assistant"]}',
        ),
    )

    resolved = service.resolve(
        user_text="他们的市盈率分别是多少？",
        thread_state={
            "recent_result_subject": "贵州茅台、五粮液",
            "reference_memory": {
                "objects": [
                    {"object_type": "stock", "display_name": "贵州茅台"},
                    {"object_type": "stock", "display_name": "五粮液"},
                ]
            },
        },
        preprocessing_signals={
            "needs_reference_resolution": True,
            "resolved_references": [{"raw": "他们", "type": "group", "label": "贵州茅台、五粮液"}],
        },
        interaction_result={},
        enable_llm=True,
    )

    assert resolved["source"] == "llm"
    assert resolved["resolved_question"] == "查询贵州茅台和五粮液各自的市盈率。"
    assert resolved["context_refs"] == ["turn:1:assistant"]


def test_context_resolution_skips_llm_when_there_is_no_prior_context(monkeypatch):
    monkeypatch.setattr(
        "src.services.context_resolution_service.chat_qwen_flash_json_with_raw",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first turn has no context to resolve")
        ),
    )

    resolved = ContextResolutionService().resolve(
        user_text="贵州茅台今天的行情是什么？",
        context_window=[],
        thread_state={},
        preprocessing_signals={},
        interaction_result={},
        enable_llm=True,
    )

    assert resolved["source"] == "no_context"
    assert resolved["resolved_question"] == "贵州茅台今天的行情是什么？"
    assert resolved["context_refs"] == []
    assert resolved["llm_usage"] == {}


def test_conversation_task_finalizer_normalizes_payload_shape_without_business_split():
    service = ConversationTaskFinalizerService()

    normalized = service._normalize_payload(  # noqa: SLF001
        {
            "analize": "用户希望继续比较贵州茅台和五粮液。",
            "round_task_desc": "继续比较贵州茅台和五粮液。",
            "task_splitd": ["先看贵州茅台", "再看五粮液", "再看五粮液", ""],
        },
        raw_user_text="好的",
    )

    assert normalized["raw_user_text"] == "好的"
    assert normalized["analize"] == "用户希望继续比较贵州茅台和五粮液。"
    assert normalized["round_task_desc"] == "继续比较贵州茅台和五粮液。"
    assert normalized["task_splitd"] == []


def test_answer_summary_service_fallback_returns_goal_and_completion():
    service = AnswerSummaryService()

    result = service.summarize(
        raw_user_text="帮我看两只股票的新闻",
        assistant_output_text="已完成临时工具计划执行。",
        output_payload={
            "mode": "tool_plan_completed",
            "task_result": {"final_output": {"summary": "两只股票的新闻已汇总。"}},
        },
        enable_llm=False,
    )

    assert result["source"] == "fallback"
    assert "两只股票的新闻已汇总" in result["answer_summary"]
    assert "完成度" in result["answer_summary"]


def test_answer_summary_fallback_preserves_object_date_and_value_without_llm():
    service = AnswerSummaryService()

    result = service.summarize(
        raw_user_text="贵州茅台现在的股价是多少？",
        assistant_output_text=(
            "贵州茅台（600519.SH）最新价为 1,338 元，数据日期为 "
            "2026-07-31；五个交易日前收盘价为 1,274.76 元。"
        ),
        output_payload={"mode": "financial_qa_cc"},
        enable_llm=False,
    )

    assert result["source"] == "fallback"
    assert "贵州茅台（600519.SH）" in result["answer_summary"]
    assert "2026-07-31" in result["answer_summary"]
    assert "1,274.76" in result["answer_summary"]


def test_conversation_preprocess_selects_default_assistant_for_general_business_query():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubGeneralBusinessInteractionPreprocessor(),
    )

    result = service.preprocess(
        text="帮我看一下明天上海天气",
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert result["domain"] == "business"
    assert result["dispatch_plan"]["selected_agent"] == "default_assistant"
    dispatch_node = {
        item["node"]: item
        for item in result["runtime_node_results"]
    }["dispatch_planning"]
    assert dispatch_node["output"]["selected_agent"] == "default_assistant"
    assert dispatch_node["output"]["entry"] == result["dispatch_plan"]["entry"]


def test_conversation_preprocess_preserves_planner_preview_metadata_for_business_flow():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubExecutionBusinessInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
    )

    result = service.preprocess(
        text="搜集一下今天热门行业和热点概念的龙头股，并对比他们的共同点和差异",
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    execution_plan_preview = result["execution_plan_preview"]

    assert result["dispatch_plan"]["entry"] == "planned_run"
    assert result["execution_path"] == "planned_run"
    assert execution_plan_preview["planner_result_source"] == "stub_llm_planner"
    assert execution_plan_preview["task_mode_result"]["task_mode"] == "planned"
    assert execution_plan_preview["thinking_mode_result"]["thinking_mode"] == "simple_thinking"


def test_conversation_preprocess_passes_raw_query_to_planner_user_objective():
    planner = _StubPlannerService()
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubExecutionBusinessInteractionPreprocessor(),
        context_resolution_service=_StubContextResolutionService(
            {
                "ori_question": "搜集一下今天热门行业和热点概念的龙头股，并对比他们的共同点和差异",
                "resolved_question": "搜集一下今天热门行业和热点概念的龙头股，并对比他们的共同点和差异",
                "context_refs": [],
                "analize": "当前问题不依赖前文。",
                "resolved_items": [],
                "resolution_summary": "",
                "source": "stub",
            }
        ),
        agent_runtime_llm_planner_service=planner,
    )

    query = "搜集一下今天热门行业和热点概念的龙头股，并对比他们的共同点和差异"
    result = service.preprocess(
        text=query,
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert result["normalized_request"]["round_task_desc"] == query
    assert result["normalized_request"]["task_splitd"] == []
    assert result["normalized_request"]["source"] == "stub"
    assert planner.last_user_objective == query
    assert planner.last_tool_queries == []


def test_conversation_preprocess_uses_fast_business_plan_for_simple_ready_query():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubGeneralBusinessInteractionPreprocessor(),
        context_resolution_service=_StubContextResolutionService(
            {
                "ori_question": "请帮我查一下今天贵州茅台的行情",
                "resolved_question": "请帮我查一下今天贵州茅台的行情",
                "context_refs": [],
                "analize": "",
                "resolved_items": [],
                "resolution_summary": "",
                "source": "skipped",
            }
        ),
        agent_runtime_llm_planner_service=_RaisingPlannerService(),
    )

    result = service.preprocess(
        text="请帮我查一下今天贵州茅台的行情",
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    execution_plan = result["execution_plan_preview"]
    assert result["normalized_request"]["info_ready"] is True
    assert execution_plan["planner_type"] == "agent_runtime_planner"


def test_conversation_preprocess_uses_llm_planner_for_default_agent_request():
    llm_planner = _TrackingPlannerService()
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubReadyExecutionInteractionPreprocessor(),
        context_resolution_service=_StubContextResolutionService(
            {
                "ori_question": "帮我查询今天涨停的股票中成交量最高的股票，并给我分析一下这个公司",
                "resolved_question": "帮我查询今天涨停的股票中成交量最高的股票，并给我分析一下这个公司",
                "context_refs": [],
                "analize": "",
                "resolved_items": [],
                "resolution_summary": "",
                "source": "skipped",
            }
        ),
        agent_runtime_planner=_StubIncompleteFastPlanner(),
        agent_runtime_llm_planner_service=llm_planner,
    )

    result = service.preprocess(
        text="帮我查询今天涨停的股票中成交量最高的股票，并给我分析一下这个公司",
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert llm_planner.called is True
    assert result["execution_plan_preview"]["planner_result_source"] == "stub_llm_planner"


def test_conversation_preprocess_builds_followup_normalized_request_from_short_ack():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubFollowupAckInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
        context_resolution_service=_StubContextResolutionService(
            {
                "ori_question": "好的",
                "resolved_question": "延续上一轮对贵州茅台和五粮液的比较分析。",
                "context_refs": ["turn:1:assistant"],
                "analize": "当前问题依赖前文中关于贵州茅台、五粮液的结果。",
                "resolved_items": [
                    {
                        "source_type": "history_answer",
                        "summary": "贵州茅台、五粮液",
                        "source_round": "1",
                        "ori_ref_text": "贵州茅台、五粮液",
                    }
                ],
                "resolution_summary": "当前轮依赖的前文信息包括：贵州茅台、五粮液。",
                "source": "stub",
            }
        ),
    )

    result = service.preprocess(
        text="好的",
        thread_context={
            "recent_result_subject": "贵州茅台、五粮液",
            "reference_memory": {
                "objects": [
                    {"object_type": "stock", "object_id": "贵州茅台", "display_name": "贵州茅台", "order_index": 0},
                    {"object_type": "stock", "object_id": "五粮液", "display_name": "五粮液", "order_index": 1},
                ]
            },
        },
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert result["preprocessing_signals"]["needs_reference_resolution"] is True
    assert result["normalized_request"]["context_relation"] == "referential"
    assert result["normalized_request"]["domain"] == "business"
    assert result["normalized_request"]["focus"]["label"] == "贵州茅台、五粮液"
    assert result["normalized_request"]["round_task_desc"] == "延续上一轮对贵州茅台和五粮液的比较分析。"
    assert result["normalized_request"]["source"] == "stub"


def test_conversation_preprocess_builds_referential_normalized_request():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubReferentialInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
        context_resolution_service=_StubContextResolutionService(
            {
                "ori_question": "他们的市盈率分别是多少？",
                "resolved_question": "查询贵州茅台和五粮液各自的市盈率。",
                "context_refs": ["turn:1:assistant"],
                "analize": "当前问题引用了前文中的两只股票。",
                "resolved_items": [
                    {
                        "source_type": "history_answer",
                        "summary": "贵州茅台、五粮液",
                        "source_round": "1",
                        "ori_ref_text": "贵州茅台、五粮液",
                    }
                ],
                "resolution_summary": "当前轮依赖的前文信息包括：贵州茅台、五粮液。",
                "source": "stub",
            }
        ),
    )

    result = service.preprocess(
        text="他们的市盈率分别是多少？",
        thread_context={
            "recent_result_subject": "贵州茅台、五粮液",
            "reference_memory": {
                "objects": [
                    {"object_type": "stock", "object_id": "贵州茅台", "display_name": "贵州茅台", "order_index": 0},
                    {"object_type": "stock", "object_id": "五粮液", "display_name": "五粮液", "order_index": 1},
                ]
            },
        },
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert result["preprocessing_signals"]["needs_reference_resolution"] is True
    assert result["preprocessing_signals"]["resolved_references"] == []
    assert result["normalized_request"]["context_relation"] == "referential"
    assert result["normalized_request"]["context_refs"] == ["turn:1:assistant"]
    assert "市盈率" in result["normalized_request"]["round_task_desc"]


def test_conversation_preprocess_can_disable_fast_business_plan_via_env(monkeypatch):
    monkeypatch.setenv("DISABLE_FAST_BUSINESS_PLAN", "1")
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubReadyExecutionInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
    )

    result = service.preprocess(
        text="贵州茅台现在多少钱？",
        thread_context={},
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    execution_plan_preview = result["execution_plan_preview"]
    assert execution_plan_preview["planner_result_source"] == "stub_llm_planner"
    assert result["dispatch_plan"]["entry"] == "planned_run"


def test_conversation_preprocess_builds_corrective_normalized_request():
    service = ConversationPreprocessService(
        interaction_preprocessor=_StubCorrectiveInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
        context_resolution_service=_StubContextResolutionService(
            {
                "ori_question": "五粮液的数据对不对啊，你核实一下？",
                "resolved_question": "核实上一轮五粮液的数据是否正确。",
                "context_refs": ["turn:1:assistant"],
                "analize": "当前问题依赖前文中关于五粮液的数据结果。",
                "resolved_items": [
                    {
                        "source_type": "history_answer",
                        "summary": "五粮液",
                        "source_round": "1",
                        "ori_ref_text": "五粮液",
                    }
                ],
                "resolution_summary": "当前轮依赖的前文信息包括：五粮液。",
                "source": "stub",
            }
        ),
    )

    result = service.preprocess(
        text="五粮液的数据对不对啊，你核实一下？",
        thread_context={
            "recent_result_subject": "贵州茅台、五粮液",
            "reference_memory": {
                "objects": [
                    {"object_type": "stock", "object_id": "贵州茅台", "display_name": "贵州茅台", "order_index": 0},
                    {"object_type": "stock", "object_id": "五粮液", "display_name": "五粮液", "order_index": 1},
                ]
            },
        },
        application_context={
            "application_name": "investment_workbench",
            "default_agent": {"agent_name": "investment_analyst"},
        },
        enable_llm=True,
    )

    assert result["preprocessing_signals"]["needs_reference_resolution"] is True
    assert result["normalized_request"]["context_relation"] == "referential"
    assert result["normalized_request"]["focus"]["label"] == "五粮液"
    assert "核实" in result["normalized_request"]["round_task_desc"]


def test_conversation_preprocess_surfaces_context_llm_failure_without_dispatch():
    class _FailingContextResolutionService:
        def resolve(self, **_: object) -> dict:
            raise ContextResolutionError("上下文语义解析失败：maas unavailable")

    service = ConversationPreprocessService(
        interaction_preprocessor=_StubFollowupAckInteractionPreprocessor(),
        agent_runtime_llm_planner_service=_StubPlannerService(),
        context_resolution_service=_FailingContextResolutionService(),
    )

    with pytest.raises(ContextResolutionError, match="maas unavailable"):
        service.preprocess(
            text="好的",
            thread_context={"recent_result_subject": "贵州茅台、五粮液"},
            application_context={
                "application_name": "investment_workbench",
                "default_agent": {"agent_name": "investment_analyst"},
            },
            enable_llm=True,
        )
