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
from src.services.llm_stream_block_service import LlmStreamBlockBuilder
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
                "source": "model",
                "type": "final",
                "status": "code_ready",
                "message": "代码已生成",
                "implementation": {
                    "summary": "实现求和工具。",
                    "entry_module": "main",
                    "modules": [{
                        "module_id": "main",
                        "role": "动态执行入口",
                        "language": "python",
                        "entrypoint": "run",
                        "functions": [{"name": "run", "responsibility": "计算并返回求和结果。"}],
                        "source_code": """
def run(inputs: dict) -> dict:
    values = inputs.get("values") or []
    return {"total": sum(values)}
""",
                    }],
                },
                "tests": [{
                    "test_id": "basic_sum",
                    "category": "happy_path",
                    "status": "proposed",
                    "input_json": "{\"values\":[1,2,3]}",
                    "expected_json": "{\"total\":6}",
                    "purpose": "验证基础求和。",
                }],
                "sample_input_json": "{\"values\":[1,2,3]}",
                "implementation_notes": ["使用 Python sum"],
                "design_issues": [],
                "risks": [],
            },
        }


class _FailedCoder:
    def code(self, design, *, requirement_text="", context=None):
        return {
            "status": "coding_failed",
            "message": "实现未完成：结构化输出 Schema 不符合严格格式要求。 当前设计已保留，可以重试。",
            "error": {
                "code": "coding_schema_invalid",
                "summary": "结构化输出 Schema 不符合严格格式要求。",
            },
            "events": [],
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


def test_active_custom_tool_reference_routes_without_pseudo_agent():
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
    assert result["selected_agent"] == "default_assistant"
    assert result["turn_mode"] == "tool_development"
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


def test_custom_tool_publication_is_separate_from_activation(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    _save_active_sum_tool(store)

    assert store.load("ct_personal_sum")["manifest"]["visibility"] == "personal"
    with pytest.raises(Exception, match="custom_tool:publish permission"):
        store.publish("ct_personal_sum", owner_ids=["user_a"], actor_id="admin")

    published = store.publish(
        "ct_personal_sum",
        owner_ids=["user_a"],
        actor_id="admin",
        actor_scopes=["custom_tool:publish"],
    )
    assert published["manifest"]["visibility"] == "public"
    assert published["manifest"]["published_by"] == "admin"


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

    activated = agent.continue_flow_action(
        "custom_tool.activate_draft",
        state=drafted["state"],
        expected_revision=drafted["state"]["implementation_revision"],
        owner_id="user_a",
    )
    assert activated["tool"]["manifest"]["status"] == "active"
    assert activated["state"] == {}
    assert activated["thread_context_patch"] == {"custom_tool_state": None}


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


def test_custom_tool_agent_propagates_safe_coding_failure_reason(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    agent = CustomToolAgentService(store=store, coder=_FailedCoder(), use_codex=False)
    state = {
        "status": "awaiting_design_confirmation",
        "owner_id": "user_a",
        "requirement_text": "创建求和工具",
        "design_revision": 1,
        "design_contract": {
            "tool_name": "sum_values",
            "display_name": "求和工具",
            "inputs": [],
            "outputs": [],
        },
    }

    result = agent.continue_flow_action(
        "custom_tool.confirm_design",
        state=state,
        expected_revision=1,
        owner_id="user_a",
    )

    assert result["coding_status"] == "coding_failed"
    assert result["coding_error"] == {
        "code": "coding_schema_invalid",
        "summary": "结构化输出 Schema 不符合严格格式要求。",
    }
    assert result["state"]["coding_error"] == result["coding_error"]


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


def test_business_error_payload_cannot_pass_tool_activation_gate(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    agent = CustomToolAgentService(store=store, runtime=runtime, use_codex=False)
    result = agent._save_test_and_return({
        "manifest": {
            "tool_name": "ct_business_error",
            "display_name": "业务错误工具",
            "description": "用于验证测试门禁。",
            "visibility": "personal",
            "runtime": {"backend": "local_dev", "timeout_ms": 2000},
        },
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {
            "type": "object",
            "properties": {"error": {"type": "object"}},
            "additionalProperties": False,
        },
        "code": "def run(inputs: dict) -> dict:\n    return {'error': {'code': 'DATA_QUERY_FAILED'}}\n",
        "sample_input": {},
        "proposed_tests": [],
    }, owner_id="user_a")

    assert result["test_result"]["ok"] is True
    assert result["test_result"]["execution_ok"] is True
    assert result["test_result"]["contract_ok"] is True
    assert result["test_result"]["business_ok"] is False
    assert result["test_result"]["gate_passed"] is False
    assert result["state"]["status"] == "draft_needs_test"
    with pytest.raises(Exception, match="must pass"):
        store.commit("ct_business_error", owner_ids=["user_a"])


def test_custom_tool_sdk_normalizes_nested_finance_provider_rows(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    store.save_draft({
        "manifest": {
            "tool_name": "ct_finance_envelope",
            "display_name": "金融响应规范化",
            "description": "验证 SDK 稳定响应。",
            "visibility": "personal",
            "runtime": {"backend": "local_dev", "timeout_ms": 2000},
        },
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {
            "type": "object",
            "properties": {
                "query_ok": {"type": "boolean"},
                "row_count": {"type": "integer"},
                "first_code": {"type": "string"},
            },
            "required": ["query_ok", "row_count", "first_code"],
            "additionalProperties": False,
        },
        "code": (
            "from custom_tool_sdk import finance_query\n"
            "def run(inputs: dict) -> dict:\n"
            "    result = finance_query('r1 = stock.quote() -> code')\n"
            "    rows = result.get('data') or []\n"
            "    return {'query_ok': result.get('ok') is True, 'row_count': len(rows), 'first_code': rows[0]['code']}\n"
        ),
    }, owner_id="user_a")
    fixture = {
        "protocol": "finance_data_tool.v1",
        "validation": {"ok": True, "errors": [], "warnings": []},
        "result": {
            "columns": ["code"],
            "data": {"status": "ok", "rows": [{"code": "600519.SH"}], "row_count": 1},
        },
    }
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
        finance_query_fixture=fixture,
    )

    result = runtime.run("ct_finance_envelope", {}, owner_ids=["user_a"], allow_inactive=True)

    assert result["ok"] is True
    assert result["data"] == {"query_ok": True, "row_count": 1, "first_code": "600519.SH"}


def test_file_store_preserves_confirmed_design_for_runtime_api_allowlist(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    store.save_draft({
        "manifest": {
            "tool_name": "ct_design_contract",
            "display_name": "设计契约持久化",
            "description": "验证文件型测试存储与数据库存储一致。",
            "visibility": "personal",
        },
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "code": "def run(inputs: dict) -> dict:\n    return {}\n",
        "sample_input": {},
        "modules": [{"module_id": "main", "entrypoint": "run"}],
        "proposed_tests": [{"test_id": "smoke"}],
        "design_contract": {"data_requirements": [{"source_ref": "stock.quote"}]},
        "design_provenance": {"artifact_id": "design-1", "revision": 2},
        "design_feedback_evidence": [{"feedback_id": "f1"}],
    }, owner_id="user_a")

    bundle = store.load_for_runtime(
        "ct_design_contract",
        owner_ids=["user_a"],
        allow_inactive=True,
    )

    assert bundle["design_contract"] == {"data_requirements": [{"source_ref": "stock.quote"}]}
    assert bundle["design_provenance"] == {"artifact_id": "design-1", "revision": 2}
    assert bundle["design_feedback_evidence"] == [{"feedback_id": "f1"}]
    assert bundle["modules"] == [{"module_id": "main", "entrypoint": "run"}]
    assert bundle["proposed_tests"] == [{"test_id": "smoke"}]


def test_custom_tool_finance_query_runs_through_host_bridge_without_secrets(tmp_path: Path, monkeypatch) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    store.save_draft({
        "manifest": {
            "tool_name": "ct_finance_bridge",
            "display_name": "宿主金融 API 桥",
            "description": "验证沙箱只提交 API 请求。",
            "visibility": "personal",
            "runtime": {"backend": "local_dev", "timeout_ms": 2000},
        },
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "code": (
            "from custom_tool_sdk import finance_query\n"
            "def run(inputs: dict) -> dict:\n"
            "    result = finance_query('r1 = stock.quote(filter = \\\"code = 600519.SH\\\") -> code')\n"
            "    return {'value': (result.get('data') or [{}])[0].get('code', '')}\n"
        ),
        "design_contract": {
            "data_requirements": [{"source_ref": "stock.quote"}],
        },
    }, owner_id="user_a")

    def execute_request(self, *, request, previous_results=None):
        assert request.startswith("r1 = stock.quote")
        return {
            "validation": {"ok": True, "errors": [], "warnings": []},
            "result": {"columns": ["code"], "data": {"rows": [{"code": "600519.SH"}]}},
        }

    monkeypatch.setattr(
        "src.services.finance_data_tool_runtime_service.FinanceDataToolRuntimeService.execute_request",
        execute_request,
    )
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )

    result = runtime.run("ct_finance_bridge", {}, owner_ids=["user_a"], allow_inactive=True)

    assert result["ok"] is True
    assert result["data"] == {"value": "600519.SH"}
    assert result["meta"]["diagnostics"]["finance_query_count"] == 1
    assert result["meta"]["diagnostics"]["finance_bridge_rounds"] == 1
    assert result["meta"]["diagnostics"]["attempt"] == 2


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


def test_codex_sdk_events_keep_the_active_stage_for_progress_rendering():
    events = CodexSdkSkillHarness._attach_stage(
        [{"source": "codex", "type": "reasoning_summary_delta", "content": "planning"}],
        "coding",
    )

    assert events[0]["metadata"] == {"stage": "coding"}
    block = LlmStreamBlockBuilder(run_id="coding_stage").event_to_blocks(events[0])[0]
    assert block["block_id"] == "coding_live_progress"
    assert block["title"] == "实现进展"


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


def test_custom_tool_runtime_collects_structured_info_and_debug_logs(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_logged_sum",
                "display_name": "带计算证据的求和工具",
                "description": "验证核心中间指标日志。",
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {
                "type": "object",
                "required": ["values"],
                "properties": {"values": {"type": "array"}},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "required": ["total"],
                "properties": {"total": {"type": "number"}},
                "additionalProperties": False,
            },
            "code": """
from custom_tool_sdk import debug, info

def run(inputs: dict) -> dict:
    values = inputs.get("values") or []
    info("输入准备完成", {"count": len(values)})
    total = sum(values)
    debug("求和计算", {"values": values, "total": total})
    return {"total": total}
""",
        },
        owner_id="user_a",
    )

    result = runtime.run("ct_logged_sum", {"values": [1, 2, 3]}, owner_ids=["user_a"], allow_inactive=True)

    assert result["ok"] is True
    assert result["data"] == {"total": 6}
    assert "execution_logs" not in result["data"]
    assert result["meta"]["execution_logs"] == [
        {"level": "info", "message": "输入准备完成", "data": {"count": 3}},
        {"level": "debug", "message": "求和计算", "data": {"values": [1, 2, 3], "total": 6}},
    ]
    assert result["meta"]["diagnostics"]["execution_log_count"] == 2


def test_custom_tool_runtime_caps_execution_logs(tmp_path: Path):
    store = CustomToolStoreService(root_dir=str(tmp_path / "custom_tools"))
    runtime = CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_log_cap",
                "display_name": "日志上限测试",
                "description": "验证调试日志不会刷屏。",
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
                "additionalProperties": False,
            },
            "code": """
from custom_tool_sdk import debug

def run(inputs: dict) -> dict:
    for index in range(80):
        debug("计算步骤", {"index": index})
    return {"ok": True}
""",
        },
        owner_id="user_a",
    )

    result = runtime.run("ct_log_cap", {}, owner_ids=["user_a"], allow_inactive=True)

    assert result["ok"] is True
    assert len(result["meta"]["execution_logs"]) == 50
    assert result["meta"]["execution_logs"][-1]["data"] == {"index": 49}
