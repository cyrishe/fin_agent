from pathlib import Path

import pytest

from src.services.agent_runtime_llm_planner_service import AgentRuntimeLLMPlannerService
from src.services.assistant_dispatch_planner import AssistantDispatchPlanner
from src.services.capability_search_service import CapabilitySearchService
from src.services.custom_tool_service import (
    CustomToolAgentService,
    CustomToolDesigner,
    CustomToolRuntimeService,
    CustomToolStoreService,
)
from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService
from src.services.codex_exec_skill_harness import CodexExecSkillHarness, CodexSdkSkillHarness
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.tool_runtime_preflight_service import ToolRuntimePreflightService
from src.tools import registry as tool_registry


class _MomentumDesigner:
    def design(self, requirement_text):
        return {
            "status": "ok",
            "design": {
                "manifest": {
                    "tool_name": "ct_test_momentum",
                    "display_name": "测试动量",
                    "description": "测试用动量工具",
                    "visibility": "personal",
                    "capabilities": ["custom_tool", "momentum"],
                    "implementation_logic": "按 value 降序输出。",
                    "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
                },
                "input_schema": {
                    "type": "object",
                    "required": ["rows"],
                    "properties": {
                        "rows": {"type": "array"},
                        "top_n": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "properties": {"items": {"type": "array"}}},
                "code": """
def run(inputs: dict) -> dict:
    rows = inputs.get("rows") or []
    top_n = int(inputs.get("top_n") or 10)
    items = sorted(rows, key=lambda item: item.get("value", 0), reverse=True)
    return {"items": items[:top_n]}
""",
                "sample_input": {"top_n": 1, "rows": [{"code": "A", "value": 2}, {"code": "B", "value": 1}]},
            },
        }


class _ContractDesigner:
    def design(self, requirement_text, context=None):
        return {
            "status": "design_ready",
            "message": "设计已生成",
            "design": {
                "tool_name": "sum_values",
                "display_name": "求和工具",
                "description": "对输入数组求和。",
                "inputs": [
                    {"name": "values", "type": "array", "required": True, "description": "数字数组"},
                ],
                "outputs": [
                    {"name": "total", "type": "number", "description": "求和结果"},
                ],
                "logic": ["读取 values", "计算求和", "返回 total"],
            },
            "events": [{"source": "model", "type": "analysis", "content": "设计可实现"}],
        }


class _ContractCoder:
    def code(self, design, *, requirement_text="", context=None):
        return {
            "status": "code_ready",
            "message": "代码已生成",
            "events": [{"source": "model", "type": "code_plan", "content": "生成 run 入口"}],
            "final": {
                "status": "code_ready",
                "files": [{
                    "path": "tool.py",
                    "role": "tool",
                    "content": """
def run(inputs: dict) -> dict:
    values = inputs.get("values") or []
    return {"total": sum(values)}
""",
                }],
                "tests": [{
                    "name": "basic_sum",
                    "status": "passed",
                    "input": {"values": [1, 2, 3]},
                    "expected": {"total": 6},
                    "actual": {"total": 6},
                    "summary": "基础求和通过",
                }],
                "code_summary": "实现求和工具。",
                "implementation_notes": ["使用 Python sum"],
                "need_design_fix": "",
                "risks": [],
            },
        }


class _CustomToolEmbeddingStub:
    def score(self, query, documents):
        return [1.0 if "个人求和工具" in str(document) else 0.0 for document in documents]


def _save_active_sum_tool(store: CustomToolStoreService, *, owner_id: str = "user_a") -> None:
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_personal_sum",
                "display_name": "个人求和工具",
                "description": "个人求和工具，计算输入数字之和。",
                "visibility": "personal",
                "capabilities": ["custom_tool"],
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {
                "type": "object",
                "required": ["values"],
                "properties": {"values": {"type": "array", "description": "数字数组"}},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"total": {"type": "number", "description": "合计"}},
            },
            "code": "def run(inputs: dict) -> dict:\n    return {'total': sum(inputs.get('values') or [])}\n",
        },
        owner_id=owner_id,
    )
    store.record_test("ct_personal_sum", {"ok": True})
    store.commit("ct_personal_sum", owner_ids=[owner_id])


def test_default_custom_tool_designer_does_not_domain_match_momentum():
    result = CustomToolDesigner().design("我想做一个个股动量工具")

    assert result["status"] == "need_more_info"
    assert "默认 designer 不做领域硬匹配" in result["message"]


def test_active_custom_tool_flow_is_dispatched_before_business_planning():
    planner = AssistantDispatchPlanner()

    result = planner.plan_free_chat(
        text="确认实现",
        attachments=[],
        thread_context={
            "custom_tool_state": {
                "status": "awaiting_design_confirmation",
                "tool_name": "ct_personal_sum",
            },
            "_custom_tool_owner_ids": ["user_a", "101"],
        },
        application_context={},
    )

    assert result["entry"] == "custom_tool_flow"
    assert result["planning_scope"] == "top_level_dispatch"
    assert result["selected_agent"] == "custom_tool_builder"
    assert result["execution_plan"] == {}


def test_published_custom_tool_is_retrievable_only_for_owner(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    _save_active_sum_tool(store)
    service = CapabilitySearchService(
        custom_tool_store_service=store,
        embedding_service=_CustomToolEmbeddingStub(),
    )

    owner_result = service.find_for_agent_runtime(
        query="使用个人求和工具",
        work_context={"_custom_tool_owner_ids": ["user_a"]},
        application_context={"execution_agent": {"tools": ["stock_realtime_quote"]}},
        tool_top_k=16,
    )
    other_result = service.find_for_agent_runtime(
        query="使用个人求和工具",
        work_context={"_custom_tool_owner_ids": ["user_b"]},
        application_context={"execution_agent": {"tools": ["stock_realtime_quote"]}},
        tool_top_k=16,
    )

    owner_tool = next(item for item in owner_result["planner_tools"] if item["tool_name"] == "ct_personal_sum")
    assert owner_tool["required_inputs"] == ["values"]
    assert owner_tool["output_fields"] == ["data.total"]
    assert "ct_personal_sum" not in [item["tool_name"] for item in other_result["planner_tools"]]


def test_planner_loads_custom_tool_input_and_output_schema(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    _save_active_sum_tool(store)
    planner = AgentRuntimeLLMPlannerService(custom_tool_store_service=store)

    input_fields = planner._load_tool_input_schema_fields("ct_personal_sum")
    output_fields = planner._load_tool_output_schema_fields("ct_personal_sum")

    assert input_fields[0]["name"] == "values"
    assert input_fields[0]["required"] is True
    assert output_fields[0]["name"] == "data.total"


def test_custom_tool_preflight_enforces_active_owner_and_schema(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    _save_active_sum_tool(store)
    preflight = ToolRuntimePreflightService(custom_tool_store_service=store)

    allowed = preflight.validate_tool_call(
        tool_name="ct_personal_sum",
        arguments={"values": [1, 2]},
        custom_tool_owner_ids=["user_a"],
    )
    denied = preflight.validate_tool_call(
        tool_name="ct_personal_sum",
        arguments={"values": [1, 2]},
        custom_tool_owner_ids=["user_b"],
    )

    assert allowed["ok"] is True
    assert denied["ok"] is False
    assert denied["reason"] == "custom_tool_unavailable"


def test_custom_tool_registry_runtime_requires_active_owner(tmp_path: Path, monkeypatch):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    _save_active_sum_tool(store)
    monkeypatch.setattr(tool_registry, "CustomToolStoreService", lambda: store)
    monkeypatch.setattr(tool_registry, "CustomToolRuntimeService", lambda: runtime)

    allowed = tool_registry.run_tool(
        "ct_personal_sum",
        {"values": [2, 3]},
        runtime_ctx={"custom_tool_owner_ids": ["user_a"]},
    )
    denied = tool_registry.run_tool(
        "ct_personal_sum",
        {"values": [2, 3]},
        runtime_ctx={"custom_tool_owner_ids": ["user_b"]},
    )

    assert allowed["ok"] is True
    assert allowed["data"]["total"] == 5
    assert denied["ok"] is False
    assert denied["meta"]["failure_kind"] == "permission_or_lifecycle_error"


def test_custom_tool_commit_rejects_other_owner(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    _save_active_sum_tool(store)

    with pytest.raises(Exception, match="not visible"):
        store.commit("ct_personal_sum", owner_ids=["user_b"])


def test_custom_tool_agent_create_confirm_test_commit_with_injected_designer(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    agent = CustomToolAgentService(store=store, designer=_MomentumDesigner(), runtime=runtime)

    design = agent.start_create("创建测试动量工具", owner_id="user_a")
    assert design["state"]["status"] == "awaiting_design_confirmation"

    drafted = agent.continue_flow("确认实现", state=design["state"], owner_id="user_a")
    assert drafted["state"]["status"] == "draft_ready"
    assert drafted["test_result"]["ok"] is True

    call_result = agent.call(
        "ct_test_momentum",
        {"top_n": 1, "rows": [{"code": "A", "value": 3}, {"code": "B", "value": 5}]},
    )
    assert call_result["ok"] is True
    assert call_result["data"]["items"][0]["code"] == "B"

    committed = agent.commit("ct_test_momentum")
    assert committed["manifest"]["status"] == "active"
    assert store.list_tools()[0]["tool_name"] == "ct_test_momentum"


def test_custom_tool_agent_design_contract_coding_commit_with_injected_coder(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    agent = CustomToolAgentService(
        store=store,
        designer=_ContractDesigner(),
        coder=_ContractCoder(),
        runtime=runtime,
        use_codex=False,
    )

    design = agent.start_create("创建求和工具", owner_id="user_a")
    assert design["state"]["status"] == "awaiting_design_confirmation"
    assert design["state"]["design_contract"]["tool_name"] == "sum_values"

    drafted = agent.continue_flow_action(
        "custom_tool.confirm_design",
        state=design["state"],
        expected_revision=design["state"]["design_revision"],
        owner_id="user_a",
    )
    assert drafted["state"]["status"] == "draft_ready"
    assert drafted["test_result"]["ok"] is True
    assert drafted["test_result"]["data"]["total"] == 6

    call_result = agent.call("ct_sum_values", {"values": [4, 5]})
    assert call_result["ok"] is True
    assert call_result["data"]["total"] == 9

    committed = agent.commit("ct_sum_values")
    assert committed["manifest"]["status"] == "active"


def test_custom_tool_structured_action_rejects_unknown_action_and_state() -> None:
    agent = CustomToolAgentService(use_codex=False)

    assert agent.interaction_user_text("custom_tool.confirm_design") == "确认并继续"
    with pytest.raises(Exception, match="unknown custom tool action"):
        agent.interaction_user_text("custom_tool.run_arbitrary_command")
    with pytest.raises(Exception, match="is not allowed"):
        agent.continue_flow_action(
            "custom_tool.confirm_design",
            state={"status": "collect_requirement"},
            owner_id="user_a",
        )


def test_custom_tool_structured_action_rejects_stale_design_revision() -> None:
    agent = CustomToolAgentService(use_codex=False)

    with pytest.raises(Exception, match="design revision changed"):
        agent.continue_flow_action(
            "custom_tool.confirm_design",
            state={"status": "awaiting_design_confirmation", "design_revision": 3},
            expected_revision=2,
            owner_id="user_a",
        )


def test_custom_tool_agent_sample_input_supports_sdk_json_string():
    sample = CustomToolAgentService._sample_input({"sample_input_json": "{\"text\":\"abc\"}", "tests": []})

    assert sample == {"text": "abc"}


def test_codex_exec_skill_harness_extracts_model_final_events():
    harness = CodexExecSkillHarness()
    text = """
{"source":"model","type":"analysis","content":"阶段分析"}
{"source":"model","type":"final","status":"design_ready","design":{"tool_name":"demo"}}
"""
    events = harness._extract_model_events(text)
    final = harness._find_final(events)

    assert events[0]["type"] == "analysis"
    assert final["status"] == "design_ready"
    assert final["design"]["tool_name"] == "demo"


def test_codex_harness_uses_separate_idle_and_hard_timeouts():
    harness = CodexExecSkillHarness(timeout_seconds=30, hard_timeout_seconds=600)

    assert harness.timeout_seconds == 30
    assert harness.hard_timeout_seconds == 600


def test_custom_tool_state_context_omits_stream_payloads():
    compact = CustomToolAgentService._clean_state_for_context(
        {
            "status": "awaiting_design_confirmation",
            "tool_name": "demo",
            "events": [{"type": "analysis", "content": "large"}],
            "raw_stdout": "large",
        }
    )

    assert compact == {"status": "awaiting_design_confirmation", "tool_name": "demo"}


def test_codex_sdk_skill_harness_extracts_structured_final_json():
    harness = CodexSdkSkillHarness()

    final = harness._final_from_text('{"status":"design_ready","design":{"tool_name":"demo"}}')

    assert final["source"] == "model"
    assert final["type"] == "final"
    assert final["status"] == "design_ready"
    assert final["design"]["tool_name"] == "demo"


def test_codex_sdk_skill_harness_normalizes_delta_notification():
    class Payload:
        delta = "阶段输出"

    class Event:
        method = "item/agentMessage/delta"
        payload = Payload()

    events = CodexSdkSkillHarness()._normalize_sdk_notification(Event())

    assert events == [{"source": "codex", "type": "agent_delta", "content": "阶段输出"}]


def test_custom_tool_context_bundle_builds_api_catalog_files(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        """
{
  "version": "test",
  "api_class_patterns": {"basic_query": {"call_pattern": "r{id} = subject.dataview(...)"}},
  "subjects": {
    "stock": {
      "_meta": {"desc": "股票主体", "rules": []},
      "quote": {
        "desc": "行情",
        "fields": {"code": {"desc": "代码"}, "pct": {"desc": "涨跌幅"}},
        "api": [{"api_name": "stock.quote", "api_class": "basic_query"}]
      }
    }
  }
}
""",
        encoding="utf-8",
    )
    service = CustomToolContextBundleService(catalog_path=str(catalog), root_dir=str(tmp_path / "context"))

    bundle = service.build(stage="coding", user_request="测试", context={"x": 1}, run_id="case_a")

    bundle_dir = Path(bundle["bundle_dir"])
    assert (bundle_dir / "api_catalog" / "index.json").exists()
    stock = (bundle_dir / "api_catalog" / "subjects" / "stock.json").read_text(encoding="utf-8")
    assert "stock.quote" in stock
    assert (bundle_dir / "custom_tool_sdk.md").exists()


def test_custom_tool_runtime_injects_custom_tool_sdk(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_sdk_probe",
                "display_name": "SDK 探针",
                "description": "测试 SDK 注入",
                "implementation_logic": "调用 custom_tool_sdk.web_search",
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            "code": """
def run(inputs: dict) -> dict:
    from custom_tool_sdk import web_search
    result = web_search("测试")
    return {"sdk_ok": isinstance(result, dict), "search_ok": result.get("ok")}
""",
            "sample_input": {},
        },
        owner_id="user_a",
    )

    result = runtime.run("ct_sdk_probe", {})

    assert result["ok"] is True
    assert result["data"]["sdk_ok"] is True
    assert result["data"]["search_ok"] is False
