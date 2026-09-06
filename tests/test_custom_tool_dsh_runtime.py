from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.scenarios.custom_tool.dsh_mcp_server import CustomToolDshMcpBridge
from src.scenarios.custom_tool.dsh_intent_router import CustomToolIntentDshRouter
from src.scenarios.custom_tool.dsh_service import (
    CustomToolDeepSeekHarnessSessionService,
    _custom_loop_observability,
)
from src.services.assistant_dispatch_planner import AssistantDispatchPlanner
from src.services.custom_tool_service import CustomToolAgentService
from src.services.finance_cc_system_tools import FinanceCcSystemTools


@pytest.mark.parametrize("service_type", [CustomToolIntentDshRouter, CustomToolDeepSeekHarnessSessionService])
def test_maas_workspace_endpoint_is_accepted(service_type, tmp_path, monkeypatch):
    endpoint = "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    monkeypatch.delenv("FINANCE_DSH_CUSTOM_TOOL_BASE_URL", raising=False)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", endpoint)
    service = service_type(enabled=False, root_dir=tmp_path / "runtime")
    assert service.base_url == endpoint
    service.close()


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_custom_tool_defaults_all_post_orchestration_work_to_codex(
    monkeypatch,
) -> None:
    for name in (
        "CUSTOM_TOOL_AGENT_PROVIDER",
        "CUSTOM_TOOL_DESIGN_PROVIDER",
        "CUSTOM_TOOL_CODING_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)

    service = CustomToolAgentService(use_codex=False)

    assert service.design_provider == "codex"
    assert service.coding_provider == "codex"


def test_custom_tool_loop_observability_keeps_stage_for_each_request() -> None:
    events = [
        {
            "type": "user/message",
            "data": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "[CUSTOM_TOOL_LOOP stage=requirement "
                            "reason=turn_started]\n需求 Skill"
                        ),
                    }
                ],
                "source": {
                    "kind": "plugin",
                    "plugin": "fin-agent-custom-tool-loop-policy",
                },
            },
        },
        {
            "type": "request/header",
            "data": {
                "header": {
                    "config": {"reasoningEffort": "low", "maxTokens": 4096},
                    "tools": [{"name": "mcp__finance__save_finance_artifact"}],
                }
            },
        },
        {
            "type": "request/header",
            "data": {
                "header": {
                    "config": {"reasoningEffort": "low", "maxTokens": 4096},
                    "tools": [{"name": "mcp__finance__request_user_interaction"}],
                }
            },
        },
    ]

    observed = _custom_loop_observability(events, config={"enabled": True})

    assert observed["request_count"] == 2
    assert [item["stage"] for item in observed["requests"]] == [
        "requirement",
        "requirement",
    ]
    assert all(item["prompt_injected"] for item in observed["requests"])
    assert observed["requests"][1]["max_tokens"] == 4096


def test_custom_tool_intent_router_uses_dsh_result_and_reuses_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    created: list[object] = []

    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        def run(self, prompt, *, session_id):
            assert "当前工具摘要" in prompt
            return SimpleNamespace(
                final_response=json.dumps(
                    {
                        "is_custom_tool": True,
                        "resolved_question": "继续修复当前金叉工具。",
                        "reason": "用户在继续已有工具流程。",
                    },
                    ensure_ascii=False,
                ),
                finish_reason="completed",
                events=[],
            )

        def close(self) -> None:
            self.closed = True

    router = CustomToolIntentDshRouter(
        enabled=True,
        root_dir=tmp_path / "intent",
        log_path=tmp_path / "intent.jsonl",
        worker_count=1,
        harness_factory=_Harness,
    )
    first = router.route(
        text="继续修复这个工具",
        thread_context={
            "custom_tool_state": {
                "tool_name": "ct_golden_cross",
                "requirement_brief": "判断均线金叉。",
            }
        },
    )
    second = router.route(text="创建一个估值提醒工具")

    assert first["is_custom_tool"] is True
    assert first["resolved_question"] == "继续修复当前金叉工具。"
    assert first["runtime"] == "dsh_opt"
    assert second["client_reused"] is True
    assert len(created) == 1
    router.close()
    assert created[0].closed is True


def test_custom_tool_intent_router_rejects_personal_deepseek_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "FINANCE_DSH_CUSTOM_TOOL_BASE_URL",
        "https://api.deepseek.com/v1",
    )

    with pytest.raises(ValueError, match="DashScope"):
        CustomToolIntentDshRouter(
            enabled=True,
            root_dir=tmp_path / "intent",
            worker_count=1,
        )


def test_custom_tool_dsh_route_bypasses_legacy_conversation_models() -> None:
    class _LegacyPreprocess:
        def preprocess(self, **kwargs):
            raise AssertionError("legacy direct LLM preprocessor must not run")

    class _Router:
        def route(self, **kwargs):
            return {
                "is_custom_tool": True,
                "resolved_question": "创建一个 MA5/MA20 金叉工具。",
                "reason": "用户明确要求创建个人工具。",
                "llm_usage": {"total_tokens": 100, "call_count": 1},
            }

    planner = AssistantDispatchPlanner(
        preprocess_service=_LegacyPreprocess(),
        custom_tool_router=_Router(),
    )
    plan = planner.plan_free_chat(
        text="帮我做一个金叉工具",
        application_context={
            "default_agent": {"agent_name": "investment_analyst"}
        },
    )

    assert plan["entry"] == "custom_tool_flow"
    assert plan["turn_mode"] == "tool_development"
    assert plan["semantic_turn"]["resolved_question"].startswith("创建一个")
    assert plan["source"] == "assistant_dispatch_planner:dsh_opt"


def test_disabled_or_text_only_router_does_not_block_normal_intake():
    class Preprocess:
        def preprocess(self, **kwargs):
            self.received = kwargs
            return {"dispatch_plan":{"turn_mode":"normal_qa"}}
    class Router:
        enabled = False
        def route(self, **kwargs):
            raise AssertionError("disabled/text-only router must not run")
    preprocessor = Preprocess()
    router = Router()
    planner = AssistantDispatchPlanner(preprocess_service=preprocessor, custom_tool_router=router)
    planner.plan_free_chat(text="贵州茅台行情")
    assert preprocessor.received["text"] == "贵州茅台行情"
    router.enabled = True
    planner.plan_free_chat(text="", attachments=[{"id":"image-1"}])
    assert preprocessor.received["attachments"] == [{"id":"image-1"}]


def test_negative_intent_usage_is_retained_once_in_planning_total():
    class Preprocess:
        def preprocess(self, **kwargs):
            return {"dispatch_plan":{"turn_mode":"normal_qa"}, "llm_usage": {"total_tokens":300,"call_count":2}}
    class Router:
        def route(self, **kwargs):
            return {"is_custom_tool":False,"llm_usage":{"total_tokens":100,"call_count":1}}
    result=AssistantDispatchPlanner(preprocess_service=Preprocess(),custom_tool_router=Router()).plan_free_chat(text="贵州茅台行情")
    assert result["llm_usage"]["total_tokens"]==400
    assert result["llm_usage"]["call_count"]==3
    assert result["preprocess_result"]["llm_usage"]["total_tokens"]==300


def test_custom_tool_dsh_bridge_reuses_system_tools_without_coding(
    tmp_path: Path,
) -> None:
    context_path = tmp_path / "context.json"
    trace_path = tmp_path / "trace.json"
    context_path.write_text(
        json.dumps(
            {
                "revision": "turn-1",
                "owner_ids": ["owner-a"],
                "tool_context": {
                    "_agent_runtime_scope": "custom-tool:test",
                    "custom_tool_state": {"custom_tool_flow_id": "flow-a"},
                },
            }
        ),
        encoding="utf-8",
    )
    system_tools = FinanceCcSystemTools()
    cc_tools, _, _ = system_tools.build_tools(
        owner_ids=["owner-a"],
        tool_context={"_agent_runtime_scope": "cc:test"},
    )
    cc_by_name = {item.name: item for item in cc_tools}
    bridge = CustomToolDshMcpBridge(
        context_path=context_path,
        trace_path=trace_path,
        system_tools=system_tools,
    )
    dsh_by_name = {item.name: item for item in bridge.list_tools()}

    assert set(dsh_by_name) == {
        "finance_query",
        "load_result",
        "read_finance_asset",
        "request_user_interaction",
        "save_finance_artifact",
        "run_dynamic_tool",
    }
    assert "implement_dynamic_tool" not in dsh_by_name
    for name, definition in dsh_by_name.items():
        assert definition.description == cc_by_name[name].description
        assert definition.inputSchema == cc_by_name[name].input_schema

    arguments = {
        "artifact_type": "requirement",
        "payload": {
            "requirement_brief": "判断指定股票最近交易日的收盘价。",
            "notice": [],
            "questions": [],
        },
    }
    cc_result = _payload(
        asyncio.run(cc_by_name["save_finance_artifact"].handler(arguments))
    )
    dsh_result = asyncio.run(
        bridge.call_tool("save_finance_artifact", arguments)
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert dsh_result == cc_result
    assert trace["revision"] == "turn-1"
    assert trace["tracker"]["artifact_updates"][0] == arguments
    assert trace["working_state"]["requirement_brief"].startswith("判断指定股票")

    # A second external turn must update the trace revision once; subsequent
    # tools in that turn must not erase its newly saved artifacts.
    context = json.loads(context_path.read_text())
    context["revision"] = "turn-2"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    bridge.list_tools()
    asyncio.run(bridge.call_tool("save_finance_artifact", arguments))
    bridge.list_tools()
    trace = json.loads(trace_path.read_text())
    assert trace["revision"] == "turn-2"
    assert trace["tracker"]["artifact_updates"][0] == arguments


def test_custom_tool_dsh_session_reuses_worker_and_requests_parent_coding(
    tmp_path: Path,
) -> None:
    created: list[object] = []
    observed_contexts: list[dict] = []

    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        def run(self, prompt, *, session_id, on_notification):
            context_path = Path(self.kwargs["env"]["FIN_AGENT_DSH_CONTEXT_PATH"])
            trace_path = Path(self.kwargs["env"]["FIN_AGENT_DSH_TRACE_PATH"])
            context = json.loads(context_path.read_text(encoding="utf-8"))
            observed_contexts.append(context)
            trace_path.write_text(
                json.dumps(
                    {
                        "revision": context["revision"],
                        "tracker": {
                            "calls": [
                                {
                                    "tool": "save_finance_artifact",
                                    "artifact_type": "flow",
                                }
                            ],
                            "artifact_updates": [
                                {
                                    "artifact_type": "requirement",
                                    "payload": {
                                        "requirement_brief": "旧的收益理解。",
                                        "questions": [],
                                    },
                                },
                                {
                                    "artifact_type": "requirement",
                                    "payload": {
                                        "requirement_brief": "识别均线金叉。",
                                        "questions": [],
                                    },
                                },
                                {
                                    "artifact_type": "design",
                                    "payload": {
                                        "design": "## 逻辑\n计算 MA5 与 MA20。"
                                    },
                                },
                                {
                                    "artifact_type": "flow",
                                    "payload": {
                                        "mermaid": "flowchart TD\nA --> B"
                                    },
                                },
                            ],
                            "interaction_requests": [],
                            "asset_reads": [],
                            "dynamic_runs": [],
                            "result_refs": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(
                final_response="",
                finish_reason="max-tokens",
                events=[],
            )

        def close(self) -> None:
            self.closed = True

    service = CustomToolDeepSeekHarnessSessionService(
        enabled=True,
        root_dir=tmp_path / "runtime",
        log_path=tmp_path / "events.jsonl",
        worker_count=1,
        harness_factory=_Harness,
    )
    context = {
        "entry": "custom_tool_flow",
        "custom_tool_flow_id": "flow-a",
        "custom_tool_state": {"custom_tool_flow_id": "flow-a"},
    }
    first = service.run_turn(
        thread_id=7,
        turn_id=1,
        owner_id="owner-a",
        user_text="做一个均线金叉工具",
        context=context,
    )
    second = service.run_turn(
        thread_id=7,
        turn_id=2,
        owner_id="owner-a",
        user_text="继续",
        context=context,
    )

    assert len(created) == 1
    assert first["ok"] is True
    assert first["diagnostic_warning"] == (
        "DeepSeek Harness turn ended with max-tokens"
    )
    assert first["runtime"] == "dsh_opt"
    assert first["implementation_requested"] is True
    assert first["implementation_runs"] == []
    assert first["artifact_updates"][-1]["artifact_type"] == "flow"
    assert first["resumed"] is False
    assert second["resumed"] is True
    assert observed_contexts[0]["tool_context"]["_agent_runtime_scope"].startswith(
        "custom_tool_dsh:"
    )
    assert first["prompt_assets"]["skills"]["design"]["sha256"]
    service.close()
    assert created[0].closed is True


def test_custom_tool_dsh_session_keeps_blocking_interaction_out_of_coding(
    tmp_path: Path,
) -> None:
    class _Harness:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run(self, prompt, *, session_id, on_notification):
            context_path = Path(self.kwargs["env"]["FIN_AGENT_DSH_CONTEXT_PATH"])
            trace_path = Path(self.kwargs["env"]["FIN_AGENT_DSH_TRACE_PATH"])
            context = json.loads(context_path.read_text(encoding="utf-8"))
            trace_path.write_text(
                json.dumps(
                    {
                        "revision": context["revision"],
                        "tracker": {
                            "calls": [],
                            "artifact_updates": [
                                {
                                    "artifact_type": "requirement",
                                    "payload": {
                                        "requirement_brief": "构造一个收益目标工具。",
                                        "questions": [
                                            {
                                                "question": "收益是绝对还是相对？",
                                                "candidate": ["绝对收益", "相对收益"],
                                            }
                                        ],
                                    },
                                }
                            ],
                            "interaction_requests": [
                                {
                                    "questions": [
                                        {
                                            "question": "旧问题？",
                                            "candidate": ["旧选项"],
                                        }
                                    ]
                                },
                                {
                                    "questions": [
                                        {
                                            "question": "收益是绝对还是相对？",
                                            "candidate": ["绝对收益", "相对收益"],
                                        }
                                    ]
                                }
                            ],
                            "asset_reads": [],
                            "dynamic_runs": [],
                            "result_refs": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(
                final_response="需要明确收益口径。",
                finish_reason="blocked",
                events=[],
            )

        def close(self) -> None:
            return None

    service = CustomToolDeepSeekHarnessSessionService(
        enabled=True,
        root_dir=tmp_path / "runtime",
        log_path=tmp_path / "events.jsonl",
        worker_count=1,
        harness_factory=_Harness,
    )
    result = service.run_turn(
        thread_id=8,
        turn_id=1,
        owner_id="owner-a",
        user_text="做一个收益目标工具",
        context={"custom_tool_flow_id": "flow-b"},
    )

    assert result["ok"] is True
    assert result["implementation_requested"] is False
    assert len(result["interaction_requests"]) == 1
    assert result["interaction_requests"][0]["questions"][0]["question"] == (
        "收益是绝对还是相对？"
    )
    assert len(result["artifact_updates"]) == 1
    assert result["artifact_updates"][0]["payload"]["requirement_brief"].startswith(
        "构造一个收益目标工具"
    )
