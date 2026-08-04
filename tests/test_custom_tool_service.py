import json
import re
from pathlib import Path

import pytest

from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService
from src.services.custom_tool_service import (
    CustomToolAgentService,
    CustomToolError,
    CustomToolRuntimeService,
    CustomToolStoreService,
)
from src.services.python_execution_runtime import PythonExecutionRuntime


class _Designer:
    def design(self, requirement_text, context=None, event_sink=None):
        return {
            "ok": True,
            "message": "模块和流程已经形成。",
            "understanding": {"goal": "计算数字之和"},
            "questions": [],
            "design": {
                "tool_name": "sum_values",
                "display_name": "求和工具",
                "description": "对输入数组求和。",
                "inputs": [
                    {
                        "name": "values",
                        "label": "数字数组",
                        "type": "array",
                        "required": True,
                        "description": "待求和的数字。",
                    }
                ],
                "outputs": [
                    {
                        "name": "total",
                        "label": "合计",
                        "type": "number",
                        "required": True,
                        "description": "求和结果。",
                    }
                ],
                "process": [],
                "modules": [],
                "rules": [],
                "data_requirements": [],
                "exceptions": [],
                "acceptance": [],
                "flow": {"steps": [], "links": []},
                "mermaid": "flowchart TD\nA[输入数字数组] --> B[求和] --> C[返回合计]",
            },
            "existing_analysis": {},
            "events": [],
        }


class _Coder:
    def __init__(self, *, failures=0):
        self.failures = failures
        self.contexts = []

    def code(self, design, *, requirement_text="", context=None, event_sink=None):
        self.contexts.append(dict(context or {}))
        source = (
            "def run(inputs: dict) -> dict:\n    return {'wrong': 1, 'key_process_info': {'value_count': len(inputs.get('values') or [])}}\n"
            if len(self.contexts) <= self.failures
            else "def run(inputs: dict) -> dict:\n    values = inputs.get('values') or []\n    return {'total': sum(values), 'key_process_info': {'value_count': len(values)}}\n"
        )
        return {
            "ok": True,
            "message": "代码已生成。",
            "events": [],
            "final": {
                "message": "代码已生成。",
                "tool_contract": {
                    "tool_name": "sum_values",
                    "display_name": "求和工具",
                    "description": "对输入数组求和。",
                    "inputs": [{
                        "name": "values",
                        "type": "array",
                        "required": True,
                        "description": "待求和的数字。",
                    }],
                    "outputs": [{
                        "name": "total",
                        "type": "number",
                        "required": True,
                        "description": "求和结果。",
                    }],
                },
                "implementation": {
                    "summary": "实现求和工具。",
                    "entry_module": "main",
                    "modules": [{
                        "module_id": "main",
                        "role": "动态执行入口",
                        "language": "python",
                        "entrypoint": "run",
                        "functions": [{"name": "run", "responsibility": "计算求和。"}],
                        "source_code": source,
                    }],
                },
                "implementation_explanation": {
                    "summary": "读取数组并返回合计。",
                    "core_flow": ["读取 values", "计算合计", "返回 total"],
                    "key_modules": ["run：动态执行入口"],
                },
                "implementation_review": {
                    "conclusion": "matches",
                    "requirement_alignment": ["实现求和目标"],
                    "design_alignment": ["输入输出与设计一致"],
                    "deviations": [],
                },
                "technical_summary": {
                    "status": "complete",
                    "conclusion": "代码正常执行。",
                    "verified": ["动态入口可执行"],
                    "unresolved": [],
                },
                "tests": [],
                "execution_examples": [{
                    "input": {"values": [1, 2, 3]},
                    "output": {
                        "total": 6,
                        "key_process_info": {"value_count": 3},
                    },
                }],
                "implementation_notes": [],
                "issues": [],
            },
        }


def _runtime(store: CustomToolStoreService, tmp_path: Path) -> CustomToolRuntimeService:
    return CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )


def _save_active_sum_tool(store: CustomToolStoreService, *, owner_id: str = "user_a") -> None:
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_personal_sum",
                "display_name": "个人求和工具",
                "description": "计算输入数字之和。",
                "visibility": "personal",
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
            },
            "code": "def run(inputs: dict) -> dict:\n    return {'total': sum(inputs.get('values') or [])}\n",
        },
        owner_id=owner_id,
    )
    store.record_test("ct_personal_sum", {"ok": True, "execution_ok": True})
    store.commit("ct_personal_sum", owner_ids=[owner_id])


def test_start_edit_loads_the_selected_owned_tool_as_authoritative_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _save_active_sum_tool(store, owner_id="user_a")
    service = CustomToolAgentService(store=store, use_codex=False)
    captured = {}

    def fake_start_create(requirement_text, **kwargs):
        captured["requirement_text"] = requirement_text
        captured.update(kwargs)
        return {"ok": True, "state": kwargs["state"]}

    monkeypatch.setattr(service, "start_create", fake_start_create)

    result = service.start_edit(
        "ct_personal_sum",
        "把输入为空时的提示写清楚",
        owner_id="user_a",
        thread_id=12,
        turn_id=34,
    )

    assert result["ok"] is True
    assert captured["requirement_text"] == "把输入为空时的提示写清楚"
    assert captured["state"]["tool_name"] == "ct_personal_sum"
    assert captured["state"]["design_contract"]["tool_name"] == "ct_personal_sum"
    assert captured["state"]["confirmed_requirement_revision"] == captured["state"]["requirement_revision"]
    assert captured["thread_id"] == 12
    assert captured["turn_id"] == 34


def test_start_edit_rejects_a_different_users_personal_tool(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _save_active_sum_tool(store, owner_id="user_a")
    service = CustomToolAgentService(store=store, use_codex=False)

    with pytest.raises(CustomToolError):
        service.start_edit(
            "ct_personal_sum",
            "修改输出",
            owner_id="user_b",
        )


def test_explicit_design_adapter_still_supports_existing_designer(tmp_path: Path) -> None:
    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem"),
        designer=_Designer(),
        use_codex=False,
    )

    result = service.start_create(
        "按已经明确的需求形成方案",
        owner_id="user_a",
        selected_skills=["financial-tool-design", "financial-tool-flowchart"],
    )

    assert result["state"]["design_contract"]["tool_name"] == "sum_values"
    assert "status" not in result["state"]


def test_design_confirmation_runs_codex_output_and_keeps_only_asset_facts(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    service = CustomToolAgentService(
        store=store,
        designer=_Designer(),
        coder=_Coder(),
        runtime=_runtime(store, tmp_path),
        use_codex=False,
    )
    design = service.start_create(
        "形成求和工具方案",
        owner_id="user_a",
        selected_skills=["financial-tool-design", "financial-tool-flowchart"],
    )

    drafted = service.continue_flow_action(
        "custom_tool.confirm_design",
        state=design["state"],
        expected_revision=design["state"]["design_revision"],
        owner_id="user_a",
    )

    assert drafted["test_result"]["execution_ok"] is True
    assert drafted["test_result"]["data"] == {
        "total": 6,
        "key_process_info": {"value_count": 3},
    }
    saved_bundle = store.load("sum_values")
    assert saved_bundle["implementation_review"]["conclusion"] == "matches"
    assert saved_bundle["implementation_explanation"]["core_flow"]
    assert "status" not in drafted["state"]
    activated = service.continue_flow_action(
        "custom_tool.activate_draft",
        state=drafted["state"],
        expected_revision=drafted["state"]["implementation_revision"],
        owner_id="user_a",
    )
    assert activated["tool"]["manifest"]["status"] == "active"


def test_missing_execution_examples_never_blocks_or_retries_coding(tmp_path: Path) -> None:
    class _CoderWithoutExamples(_Coder):
        def code(self, design, *, requirement_text="", context=None, event_sink=None):
            result = super().code(
                design,
                requirement_text=requirement_text,
                context=context,
                event_sink=event_sink,
            )
            result["final"].pop("execution_examples", None)
            return result

    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    coder = _CoderWithoutExamples()
    service = CustomToolAgentService(
        store=store,
        coder=coder,
        runtime=_runtime(store, tmp_path),
        use_codex=False,
    )
    state = {
        "owner_id": "user_a",
        "requirement_text": "创建求和工具",
        "design_contract": _Designer().design("")["design"],
    }

    implemented = service.implement_dynamic_tool(
        state=state,
        owner_id="user_a",
        instruction="实现当前设计",
    )

    assert implemented["coding_status"] == "implemented"
    assert "test_result" not in implemented
    assert implemented["coding_tests"] == []
    assert len(coder.contexts) == 1
    assert store.load("sum_values")["manifest"]["status"] == "draft"
    activated = service.continue_flow_action(
        "custom_tool.activate_draft",
        state=implemented["state"],
        expected_revision=implemented["state"]["implementation_revision"],
        owner_id="user_a",
    )
    assert activated["tool"]["manifest"]["status"] == "active"


def test_coding_harness_evidence_is_saved_without_changing_final_schema(tmp_path: Path) -> None:
    class _CoderWithHarnessEvidence(_Coder):
        def code(self, design, *, requirement_text="", context=None, event_sink=None):
            result = super().code(
                design,
                requirement_text=requirement_text,
                context=context,
                event_sink=event_sink,
            )
            result["final"].pop("execution_examples", None)
            result["events"] = [{
                "source": "harness",
                "type": "tool_result",
                "content": (
                    'ok\nCUSTOM_TOOL_TEST_EVIDENCE={"input":{"values":[1,2,3]},'
                    '"actual":{"total":6,"key_process_info":{"value_count":3}}}'
                ),
            }]
            return result

    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    service = CustomToolAgentService(
        store=store,
        coder=_CoderWithHarnessEvidence(),
        runtime=_runtime(store, tmp_path),
        use_codex=False,
    )
    implemented = service.implement_dynamic_tool(
        state={
            "owner_id": "user_a",
            "requirement_text": "创建求和工具",
            "design_contract": _Designer().design("")["design"],
        },
        owner_id="user_a",
        instruction="实现当前设计",
    )

    assert implemented["coding_status"] == "implemented"
    assert implemented["test_result"]["evidence_source"] == "production_runtime"
    assert implemented["test_result"]["cases"][0]["input"] == {"values": [1, 2, 3]}
    assert implemented["test_result"]["cases"][0]["actual"]["key_process_info"] == {
        "value_count": 3,
    }


def test_runtime_wrapper_does_not_shadow_tool_module_helpers(tmp_path: Path) -> None:
    store = CustomToolStoreService(
        root_dir=str(tmp_path / "tools"),
        backend="filesystem",
    )
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_runtime_namespace",
                "display_name": "运行命名空间测试",
                "description": "验证工具内部名称不受运行包装器污染。",
                "visibility": "personal",
                "runtime": {
                    "kind": "python_sandbox",
                    "backend": "local_dev",
                    "timeout_ms": 2000,
                },
            },
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "code": (
                "def _inputs(value):\n"
                "    return {'received': value}\n\n"
                "def run(inputs):\n"
                "    return {'value': _inputs(inputs), 'key_process_info': {}}\n"
            ),
        },
        owner_id="user_a",
    )

    result = _runtime(store, tmp_path).run(
        "ct_runtime_namespace",
        {"code": "600519.SH"},
        owner_ids=["user_a"],
        allow_inactive=True,
    )

    assert result["ok"] is True
    assert result["data"]["value"] == {"received": {"code": "600519.SH"}}


def test_coding_contract_builds_runtime_schema_from_natural_language_design(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    service = CustomToolAgentService(store=store, use_codex=False)
    final = _Coder().code({"document": "一份结构化自然语言设计文档。"})["final"]

    bundle = service._bundle_from_coding_final(
        {"document": "一份结构化自然语言设计文档。"},
        final,
    )

    assert bundle["manifest"]["tool_name"] == "ct_sum_values"
    assert bundle["manifest"]["implementation_logic"] == "一份结构化自然语言设计文档。"
    assert bundle["input_schema"]["required"] == ["values"]
    assert bundle["input_schema"]["properties"]["values"]["type"] == "array"
    assert bundle["output_schema"]["required"] == ["total", "key_process_info"]
    assert bundle["output_schema"]["properties"]["total"]["type"] == "number"
    assert bundle["output_schema"]["properties"]["key_process_info"]["type"] == "object"


def test_runtime_does_not_reject_business_output_with_hard_schema_checks(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    coder = _Coder(failures=1)
    service = CustomToolAgentService(
        store=store,
        coder=coder,
        runtime=_runtime(store, tmp_path),
        use_codex=False,
    )
    state = {
        "owner_id": "user_a",
        "requirement_text": "创建求和工具",
        "design_contract": _Designer().design("")["design"],
    }

    implemented = service.implement_dynamic_tool(state=state, owner_id="user_a", instruction="实现当前设计")

    assert implemented["test_result"]["execution_ok"] is True
    assert implemented["test_result"]["data"] == {
        "wrong": 1,
        "key_process_info": {"value_count": 3},
    }
    assert "test_feedback" not in implemented["state"]
    assert len(coder.contexts) == 1


def test_unknown_ui_action_is_the_only_action_level_rejection() -> None:
    service = CustomToolAgentService(use_codex=False)

    with pytest.raises(Exception, match="unknown custom tool action"):
        service.continue_flow_action("custom_tool.run_arbitrary_command", state={}, owner_id="user_a")


def test_empty_business_result_is_a_successful_technical_execution(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    store.save_draft(
        {
            "manifest": {
                "tool_name": "empty_screen",
                "display_name": "空结果筛选",
                "description": "允许没有命中标的。",
                "visibility": "personal",
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {
                "type": "object",
                "required": ["matches"],
                "properties": {"matches": {"type": "array"}},
            },
            "code": "def run(inputs: dict) -> dict:\n    return {'matches': []}\n",
        },
        owner_id="user_a",
    )

    result = _runtime(store, tmp_path).run(
        "empty_screen",
        {},
        owner_ids=["user_a"],
        allow_inactive=True,
    )

    assert result["ok"] is True
    assert result["data"] == {"matches": []}


def test_runtime_allows_null_business_metrics_for_no_signal_result(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    store.save_draft(
        {
            "manifest": {
                "tool_name": "nullable_signal",
                "display_name": "信号检测",
                "description": "未发现信号时保留空指标。",
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {
                "type": "object",
                "required": ["triggered", "ma20"],
                "properties": {
                    "triggered": {"type": "boolean"},
                    "ma20": {"type": "number"},
                },
            },
            "code": (
                "def run(inputs: dict) -> dict:\n"
                "    return {'triggered': False, 'ma20': None, "
                "'key_process_info': {'reason': 'no signal'}}\n"
            ),
        },
        owner_id="user_a",
    )

    result = _runtime(store, tmp_path).run(
        "nullable_signal",
        {},
        owner_ids=["user_a"],
        allow_inactive=True,
    )

    assert result["ok"] is True
    assert result["data"]["ma20"] is None


def test_existing_tool_test_is_reviewed_and_can_request_another_turn(tmp_path: Path, monkeypatch) -> None:
    class Tester:
        def __init__(self):
            self.contexts = []

        def plan(self, request, *, context=None, event_sink=None):
            self.contexts.append(dict(context or {}))
            if len(self.contexts) == 1:
                return {
                    "ok": True,
                    "message": "先执行一个典型输入。",
                    "next_action": "run_tests",
                    "assessment": "尚无真实运行证据。",
                    "cases": [{"name": "典型输入", "purpose": "观察合计", "request": "计算 1、2、3 的合计"}],
                    "presentation": {"headline": "测试中", "notes": []},
                    "events": [],
                }
            return {
                "ok": True,
                "message": "实际结果已经足够说明工具行为。",
                "next_action": "finish",
                "assessment": "真实输出为 6，核心结果可观察。",
                "cases": [],
                "presentation": {"headline": "测试完成", "notes": ["业务结果由用户确认"]},
                "events": [],
            }

    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _save_active_sum_tool(store)
    tester = Tester()
    service = CustomToolAgentService(
        store=store,
        tester=tester,
        runtime=_runtime(store, tmp_path),
        use_codex=False,
    )
    monkeypatch.setattr(
        "src.services.asset_invocation_service.AssetInvocationService.plan",
        lambda self, **kwargs: {"status": "ready", "calls": [{"values": [1, 2, 3]}]},
    )

    result = service._run_existing_test(
        request="请找一个能说明结果的真实样例",
        state={"tool_name": "ct_personal_sum", "owner_id": "user_a"},
        owner_id="user_a",
    )

    assert len(tester.contexts) == 2
    assert tester.contexts[1]["test_history"][0]["executions"][0]["actual"] == {"total": 6}
    assert result["test_result"]["evidence_status"] == "sufficient"
    assert result["test_result"]["assessment"] == "真实输出为 6，核心结果可观察。"
    assert result["presentation"]["headline"] == "测试完成"


def test_existing_tool_test_stops_at_configured_max_turns(tmp_path: Path, monkeypatch) -> None:
    class Tester:
        def __init__(self):
            self.calls = 0

        def plan(self, request, *, context=None, event_sink=None):
            self.calls += 1
            return {
                "ok": True,
                "message": "继续补充测试。",
                "next_action": "run_tests",
                "assessment": "还希望观察更多输入。",
                "cases": [{"name": "补充输入", "purpose": "补充证据", "request": "再计算一次"}],
                "presentation": {"headline": "测试中", "notes": []},
                "events": [],
            }

    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _save_active_sum_tool(store)
    tester = Tester()
    service = CustomToolAgentService(
        store=store,
        tester=tester,
        runtime=_runtime(store, tmp_path),
        use_codex=False,
    )
    monkeypatch.setenv("CUSTOM_TOOL_TEST_MAX_TURNS", "2")
    monkeypatch.setattr(
        "src.services.asset_invocation_service.AssetInvocationService.plan",
        lambda self, **kwargs: {"status": "ready", "calls": [{"values": [1, 2, 3]}]},
    )

    result = service._run_existing_test(
        request="持续补充测试",
        state={"tool_name": "ct_personal_sum", "owner_id": "user_a"},
        owner_id="user_a",
    )

    assert tester.calls == 2
    assert result["test_result"]["max_turns_reached"] is True
    assert result["test_result"]["evidence_status"] == "inconclusive"
    assert len(result["test_result"]["cases"]) == 2


def test_runtime_accepts_catalog_api_without_using_design_as_an_allowlist() -> None:
    allowed, error = CustomToolRuntimeService._finance_request_allowed(
        'r1 = stock.basic_info(filter = "name = 贵州茅台") -> code, name',
        {
            "design_contract": {
                "data_requirements": [{
                    "source_ref": "api_catalog/subjects/stock.json#dataviews.quote",
                    "fields": ["close"],
                    "purpose": "读取行情",
                }],
            },
        },
    )

    assert allowed is True
    assert error == ""


def test_runtime_rejects_api_missing_from_system_catalog() -> None:
    allowed, error = CustomToolRuntimeService._finance_request_allowed(
        "r1 = stock.not_a_real_api() -> value",
        {},
    )

    assert allowed is False
    assert "system API catalog" in error


def test_stale_design_confirmation_is_rejected() -> None:
    service = CustomToolAgentService(use_codex=False)

    with pytest.raises(CustomToolError, match="design revision changed"):
        service.continue_flow_action(
            "custom_tool.confirm_design",
            state={
                "design_revision": 3,
                "design_contract": {"document": "当前设计"},
            },
            expected_revision=2,
            owner_id="user_a",
        )


def test_matching_design_confirmation_uses_the_current_server_design() -> None:
    service = CustomToolAgentService(use_codex=False)
    captured = {}

    service._confirm_and_code = lambda **kwargs: captured.update(kwargs) or {"ok": True}  # type: ignore[method-assign]
    result = service.continue_flow_action(
        "custom_tool.confirm_design",
        state={
            "design_revision": 3,
            "design_contract": {
                "document": "当前设计",
                "mermaid": "flowchart TD\nA --> B",
            },
        },
        expected_revision=3,
        owner_id="user_a",
    )

    assert result == {"ok": True}
    assert captured["state"]["design_contract"]["document"] == "当前设计"


def test_matching_design_confirmation_rejects_a_missing_flow_artifact() -> None:
    service = CustomToolAgentService(use_codex=False)

    with pytest.raises(CustomToolError, match="flow artifact is missing"):
        service.continue_flow_action(
            "custom_tool.confirm_design",
            state={
                "design_revision": 3,
                "design_contract": {"document": "当前设计"},
            },
            expected_revision=3,
            owner_id="user_a",
        )


def test_flow_diagram_is_derived_and_does_not_change_design_revision() -> None:
    base = {
        "document": "## 规则\n涨幅超过 5% 时命中。",
        "mermaid": "flowchart TD\nA --> B{涨幅 > 5%?}",
    }
    first = CustomToolAgentService._design_artifact_identity(base, state={})
    revised_flow = CustomToolAgentService._design_artifact_identity(
        {
            **base,
            "mermaid": "flowchart TD\nA[读取涨幅] --> B{涨幅 > 5%?}\nB -- 是 --> C[命中]",
        },
        state=first,
    )

    assert revised_flow["design_revision"] == first["design_revision"]
    assert revised_flow["design_fingerprint"] == first["design_fingerprint"]


def test_file_store_enforces_owner_and_publication_permissions(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    _save_active_sum_tool(store)

    assert store.list_tools(owner_ids=["user_a"])[0]["tool_name"] == "ct_personal_sum"
    assert store.list_tools(owner_ids=["user_b"]) == []
    with pytest.raises(Exception, match="custom_tool:publish permission"):
        store.publish("ct_personal_sum", owner_ids=["user_a"], actor_id="admin")


def test_context_bundle_materializes_design_and_editable_modules_by_reference(tmp_path: Path) -> None:
    service = CustomToolContextBundleService(
        catalog_path=str(tmp_path / "missing.json"),
        root_dir=str(tmp_path / "bundles"),
    )
    bundle = service.build(
        stage="coding",
        user_request="修复代码",
        context={
            "design": {"tool_name": "ct_demo"},
            "current_implementation": {
                "revision": 1,
                "modules": [{"module_id": "main", "source_code": "def run(inputs):\n    return inputs\n"}],
            },
            "_workspace_identity": {"owner_id": "user_a"},
        },
    )

    prompt_context = service.prompt_context(bundle, {})
    assert prompt_context["design_ref"] == "design.json"
    module_path = prompt_context["current_implementation"]["module_files"][0]
    assert Path(bundle["bundle_dir"], module_path).is_file()
    assert "source_code" not in Path(bundle["bundle_dir"], "task.json").read_text(encoding="utf-8")
    assert Path(bundle["bundle_dir"], bundle["api_index"]).is_file()
    assert Path(bundle["bundle_dir"], bundle["custom_tool_sdk"]).is_file()


def test_first_coding_turn_gets_editable_module_focused_api_context_and_test_runtime(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({
            "version": "v1",
            "api_class_patterns": {
                "basic_query": {
                    "call_pattern": "r{id} = {api_name}(filter, order, limit, realtime) -> field1",
                    "args": {
                        "required": [],
                        "optional": ["filter", "order", "limit", "realtime"],
                    },
                    "output_rule": "输出字段来自当前 dataview。",
                    "examples": ["r1 = stock.quote(filter = \"code = 600519.SH\") -> code, close"],
                }
            },
            "subjects": {
                "stock": {
                    "_meta": {"desc": "股票"},
                    "quote": {
                        "desc": "行情",
                        "fields": {"code": ["代码"], "close": ["收盘价"]},
                        "api": [{"api_name": "stock.quote", "api_class": "basic_query"}],
                    },
                }
            },
        }),
        encoding="utf-8",
    )
    service = CustomToolContextBundleService(
        catalog_path=str(catalog_path),
        root_dir=str(tmp_path / "bundles"),
    )
    bundle = service.build(
        stage="coding",
        user_request="实现工具",
        context={
            "design": {
                "tool_name": "ct_demo",
                "modules": [{"name": "market_loader", "responsibility": "读取行情", "functions": []}],
                "data_requirements": [{
                    "topic": "A股日线行情",
                    "purpose": "读取日线行情",
                    "fields": ["code", "close"],
                    "source_ref": "api_catalog/subjects/stock/quote.json",
                }],
            },
            "_workspace_identity": {"owner_id": "user_a"},
        },
    )

    prompt_context = service.prompt_context(bundle, {})
    module_path = prompt_context["current_implementation"]["module_files"][0]
    assert bundle["coding_workspace"]["editable"] is True
    assert bundle["coding_workspace"]["first_implementation"] is True
    assert bundle["coding_workspace"]["module_plan_items"][0]["title"] == "核心实现模块"
    task_api = json.loads(Path(bundle["bundle_dir"], bundle["api_task_context"]).read_text(encoding="utf-8"))
    assert task_api["data_needs"] == [{
        "topic": "A股日线行情",
        "fields": ["code", "close"],
        "purpose": "读取日线行情",
    }]
    assert task_api["sources"] == [{
        "source_ref": "api_catalog/subjects/stock/quote.json",
        "purpose": "读取日线行情",
        "requested_fields": ["code", "close"],
        "query_fields": ["code", "close"],
        "unavailable_requested_fields": [],
        "subject": "stock",
        "dataview": "quote",
        "asset": "api_catalog/subjects/stock/quote.json",
        "method_names": ["stock.quote"],
    }]
    assert "definition" not in task_api["sources"][0]
    assert "request_patterns" not in task_api["sources"][0]
    assert Path(bundle["bundle_dir"], module_path).is_file()
    assert Path(bundle["bundle_dir"], "dev_runtime/custom_tool_sdk.py").is_file()
    assert Path(bundle["bundle_dir"], "dev_runtime/test_support.py").is_file()
    assert bundle["api_coding_guide"] == "api_catalog/CODING_GUIDE.md"
    assert Path(bundle["bundle_dir"], bundle["api_coding_guide"]).is_file()
    assert "request_patterns" not in bundle
    assert not Path(bundle["bundle_dir"], "api_catalog/request_patterns.json").exists()
    stock_index = json.loads(
        Path(bundle["bundle_dir"], "api_catalog/subjects/stock/index.json").read_text(
            encoding="utf-8"
        )
    )
    assert stock_index["dataviews"][0]["file"].startswith(
        "api_catalog/subjects/stock/"
    )
    stock_subject = json.loads(
        Path(bundle["bundle_dir"], "api_catalog/subjects/stock/quote.json").read_text(
            encoding="utf-8"
        )
    )
    quote_method = stock_subject["methods"][0]
    assert quote_method["name"] == "stock.quote"
    assert quote_method["call"].startswith("{result_name} = stock.quote(")
    assert quote_method["args"]["optional"] == ["filter", "order", "limit", "realtime"]
    assert "output_rule" in quote_method
    assert bundle["module_template"] == "DYNAMIC_TOOL_TEMPLATE.py"
    template = Path(bundle["bundle_dir"], bundle["module_template"]).read_text(encoding="utf-8")
    assert "def run(inputs: dict) -> dict:" in template
    assert '"key_process_info"' in template
    assert 'debug("key_process_info"' not in template
    assert "latest_quote = stock.quote" in template
    assert "r1 = stock.quote" not in template
    coding_guide = Path(bundle["bundle_dir"], "CODING_WORKSPACE.md").read_text(encoding="utf-8")
    assert "Use this exact interpreter" in coding_guide
    assert "PYTHONPYCACHEPREFIX=scratch/pycache" in coding_guide
    assert "from test_support import load_module, install_rows" in coding_guide
    assert "Read `api_catalog/CODING_GUIDE.md` first" in coding_guide
    assert "scratch/test_evidence.json" in coding_guide
    assert Path(bundle["bundle_dir"], "scratch").is_dir()
    assert bundle["coding_evidence"] == "scratch/test_evidence.json"
    sdk_doc = Path(bundle["bundle_dir"], "custom_tool_sdk.md").read_text(encoding="utf-8")
    assert 'filter = "code = 600519.SH and tradedate <= 2026-07-23"' in sdk_doc
    assert "Values inside it are bare protocol literals" in sdk_doc
    coding_asset_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(bundle["bundle_dir"], "api_catalog").rglob("*.json")
    ) + "\n" + sdk_doc
    assert not re.search(r"\br(?:[0-9]+|N)\b", coding_asset_text)

    Path(bundle["bundle_dir"], module_path).write_text(
        "def run(inputs):\n    return {'close': 1}\n",
        encoding="utf-8",
    )
    Path(bundle["bundle_dir"], bundle["coding_evidence"]).write_text(
        json.dumps({
            "result": "passed",
            "cases": [{
                "input": {"code": "600519.SH"},
                "raw_output": {
                    "close": 1,
                    "key_process_info": {"sample_count": 1},
                },
            }],
        }),
        encoding="utf-8",
    )
    collected = service.collect_coding_result(bundle, {
        "implementation_summary": "读取行情并返回收盘价。",
        "execution_examples": [{
            "input": {"code": "600519.SH"},
            "output": {"close": 1, "key_process_info": {"sample_count": 1}},
        }],
    })
    assert "return {'close': 1}" in collected["implementation"]["modules"][0]["source_code"]
    assert "return {'close': 1}" in collected["code"]
    assert collected["implementation_summary"] == "读取行情并返回收盘价。"
    assert collected["coding_test_evidence"]["cases"][0]["actual"]["key_process_info"] == {
        "sample_count": 1,
    }

    Path(bundle["bundle_dir"], bundle["coding_evidence"]).write_text(
        json.dumps({
            "test": "代表性功能测试",
            "input": {"code": "600519.SH"},
            "raw_tool_output": {
                "close": 2,
                "key_process_info": {"sample_count": 2},
            },
        }),
        encoding="utf-8",
    )
    collected_alias = service.collect_coding_result(bundle, {})
    assert collected_alias["coding_test_evidence"]["cases"][0]["actual"] == {
        "close": 2,
        "key_process_info": {"sample_count": 2},
    }

    Path(bundle["bundle_dir"], bundle["coding_evidence"]).write_text(
        json.dumps({
            "tool_inputs": [
                {"market_data": {"volume": [100, 91]}},
                {"market_data": {"volume": [100, 93]}},
            ],
            "raw_tool_outputs": [
                {"ok": True, "results": []},
                {"ok": True, "results": [{"code": "000001.SZ"}]},
            ],
        }),
        encoding="utf-8",
    )
    collected_parallel_cases = service.collect_coding_result(bundle, {})
    parallel_cases = collected_parallel_cases["coding_test_evidence"]["cases"]
    assert len(parallel_cases) == 2
    assert parallel_cases[0]["input"]["market_data"]["volume"] == [100, 91]
    assert parallel_cases[1]["actual"]["results"][0]["code"] == "000001.SZ"

    Path(bundle["bundle_dir"], bundle["coding_evidence"]).write_text(
        json.dumps({
            "tool_inputs": [{"case": 1}, {"case": 2}],
            "raw_tool_outputs": [{"ok": True}],
        }),
        encoding="utf-8",
    )
    ambiguous_parallel_cases = service.collect_coding_result(bundle, {})
    assert "coding_test_evidence" not in ambiguous_parallel_cases


def test_platform_normalizes_key_process_info_as_required_object() -> None:
    fields = CustomToolAgentService._with_key_process_info_output([
        {"name": "result", "type": "string", "required": True, "description": "结果"},
        {
            "name": "key_process_info",
            "type": "string",
            "required": False,
            "description": "",
        },
    ])

    key_process = next(item for item in fields if item["name"] == "key_process_info")
    assert key_process["type"] == "object"
    assert key_process["required"] is True
    assert key_process["description"]
    assert [item["name"] for item in fields].count("key_process_info") == 1


def test_coding_execution_examples_are_best_effort_display_data() -> None:
    native = {
        "execution_examples": [{
            "input": {"code": "600519.SH"},
            "output": {"close": 1},
        }],
    }
    encoded = {
        "execution_examples": [{
            "input": '{"code":"600519.SH"}',
            "output": '{"close":1}',
        }],
    }

    assert CustomToolAgentService._sample_input(native) == {"code": "600519.SH"}
    assert CustomToolAgentService._execution_examples(native)[0]["expected"] == {"close": 1}
    assert CustomToolAgentService._sample_input(encoded) == {}
    assert CustomToolAgentService._execution_examples(encoded) == []
    assert CustomToolAgentService._sample_input({"execution_examples": "{broken"}) == {}
    assert CustomToolAgentService._execution_examples({"execution_examples": "{broken"}) == []
    assert CustomToolAgentService._execution_examples({}) == []


def test_coding_session_reuses_workspace_and_preserves_partial_source(tmp_path: Path) -> None:
    service = CustomToolContextBundleService(
        catalog_path=str(tmp_path / "missing.json"),
        root_dir=str(tmp_path / "bundles"),
    )
    context = {
        "design": {"tool_name": "ct_demo", "modules": []},
        "coding_feedback": "实现当前设计",
        "_workspace_identity": {"owner_id": "user_a"},
    }

    first = service.build(
        stage="coding",
        user_request="实现",
        context=context,
        run_id="coding-session-1",
    )
    module_path = Path(first["bundle_dir"], first["coding_workspace"]["module_files"][0])
    module_path.write_text("def run(inputs):\n    return {'partial': True}\n", encoding="utf-8")
    second = service.build(
        stage="coding",
        user_request="根据反馈继续",
        context={**context, "coding_feedback": "修复真实测试错误"},
        run_id="coding-session-1",
    )

    assert second["bundle_dir"] == first["bundle_dir"]
    assert "partial" in module_path.read_text(encoding="utf-8")


def test_context_bundle_only_exposes_runtime_and_api_material_to_coding(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"version": "v1", "api_class_patterns": {}, "subjects": {}}),
        encoding="utf-8",
    )
    service = CustomToolContextBundleService(
        catalog_path=str(catalog),
        root_dir=str(tmp_path / "bundles"),
    )

    bundle = service.build(stage="design", user_request="设计工具", context={})

    assert "api_index" not in bundle
    assert "request_patterns" not in bundle
    assert "runtime_contract" not in bundle
    assert "custom_tool_sdk" not in bundle
    assert not Path(bundle["bundle_dir"], "api_catalog").exists()


def test_coding_subject_asset_expands_method_contracts(tmp_path: Path) -> None:
    service = CustomToolContextBundleService(root_dir=str(tmp_path / "bundles"))

    bundle = service.build(
        stage="coding",
        user_request="实现分钟成交量工具",
        context={},
    )

    stock = json.loads(
        Path(bundle["bundle_dir"], "api_catalog/subjects/stock/quote.json").read_text(
            encoding="utf-8"
        )
    )
    methods = {
        method["name"]: method
        for method in stock["methods"]
    }
    quote = methods["stock.quote"]
    assert quote["args"]["optional"] == ["filter", "order", "limit", "realtime"]
    assert quote["call"].startswith("{result_name} = stock.quote(")
    assert "只能从当前 view.fields" in quote["output_rule"]

    kday = methods["stock.quote.kd_<field>_<method>"]
    assert kday["available_names"]["field_methods"]["close"] == [
        "max",
        "min",
        "avg",
        "median",
        "high",
    ]
    assert kday["same_minute_variant"]["fields"]["minute_volumn"] == [
        "avg",
        "max",
        "min",
        "median",
        "percentile",
    ]
    assert "current_value" in kday["same_minute_variant"]["output_rule"]
    for method in methods.values():
        assert method["examples"], method["name"]
        assert not any("r1 =" in example for example in method["examples"])

    api_index = json.loads(
        Path(bundle["bundle_dir"], "api_catalog/index.json").read_text(
            encoding="utf-8"
        )
    )
    for subject in api_index["subjects"]:
        for dataview in subject["dataviews"]:
            asset = json.loads(
                Path(bundle["bundle_dir"], dataview["file"]).read_text(
                    encoding="utf-8"
                )
            )
            assert asset["methods"], dataview["file"]
            for method in asset["methods"]:
                assert method["examples"], method["name"]


def test_context_state_cleanup_preserves_system_owned_feedback_history() -> None:
    state = {
        "feedback_ledger": [{"feedback_id": "f1", "text": "窗口改成 60 日"}],
        "status": "awaiting_design_confirmation",
        "events": [{"type": "raw"}],
        "raw_stdout": "noise",
    }

    cleaned = CustomToolAgentService._clean_state_for_context(state)

    assert cleaned["feedback_ledger"] == state["feedback_ledger"]
    assert "status" not in cleaned
    assert "events" not in cleaned
    assert "raw_stdout" not in cleaned


def test_context_bundle_recovers_revised_source_without_repeating_it_in_final_json(tmp_path: Path) -> None:
    service = CustomToolContextBundleService(
        catalog_path=str(tmp_path / "missing.json"),
        root_dir=str(tmp_path / "bundles"),
    )
    bundle = service.build(
        stage="coding",
        user_request="只修改计算函数",
        context={
            "current_implementation": {
                "revision": 1,
                "modules": [{
                    "module_id": "main",
                    "role": "入口",
                    "language": "python",
                    "entrypoint": "run",
                    "functions": [{"name": "run", "responsibility": "入口"}],
                    "source_code": "def run(inputs):\n    return {'value': 1}\n",
                }],
            },
        },
    )
    module_path = bundle["coding_workspace"]["module_files"][0]
    Path(bundle["bundle_dir"], module_path).write_text(
        "def run(inputs):\n    return {'value': 2}\n",
        encoding="utf-8",
    )

    result = service.collect_coding_result(
        bundle,
        {
            "implementation": {
                "summary": "局部修改完成",
                "entry_module": "main",
                "modules": [{
                    "module_id": "main",
                    "role": "入口",
                    "language": "python",
                    "entrypoint": "run",
                    "functions": [{"name": "run", "responsibility": "入口"}],
                    "source_code": "",
                }],
            }
        },
    )

    assert "'value': 2" in result["implementation"]["modules"][0]["source_code"]


def test_runtime_exposes_objective_info_and_debug_logs(tmp_path: Path) -> None:
    store = CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem")
    store.save_draft(
        {
            "manifest": {
                "tool_name": "ct_logs",
                "display_name": "日志工具",
                "description": "验证运行证据。",
                "runtime": {"backend": "local_dev", "timeout_ms": 2000},
            },
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"value": {"type": "number"}}},
            "code": (
                "def run(inputs: dict) -> dict:\n"
                "    info('开始计算', {'rows': 1})\n"
                "    debug('核心指标', {'value': 3})\n"
                "    return {'value': 3}\n"
            ),
        },
        owner_id="user_a",
    )
    result = _runtime(store, tmp_path).run("ct_logs", {}, owner_ids=["user_a"], allow_inactive=True)

    assert result["ok"] is True
    logs = result["meta"]["execution_logs"]
    assert [item["level"] for item in logs] == ["info", "debug"]
