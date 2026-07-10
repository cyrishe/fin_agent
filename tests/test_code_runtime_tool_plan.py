from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.conversation_preprocess_service import ConversationPreprocessService
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.tool_plan_runtime_service import ToolPlanRuntimeService


def test_conversation_preprocess_strips_code_hint_and_marks_context(monkeypatch):
    service = ConversationPreprocessService()
    monkeypatch.setattr(service, "_build_execution_plan_preview", lambda **kwargs: {})

    result = service.preprocess(text="/code 请用 Python 聚合这批表格", enable_llm=False)

    assert result["normalized_input"]["text"] == "请用 Python 聚合这批表格"
    assert result["normalized_input"]["code_runtime_hint"] is True
    assert result["work_context"]["code_runtime_hint"] is True
    assert result["work_context"]["preferred_runtime"] == "code"
    assert result["preprocessing_signals"]["code_runtime_hint"] is True


def test_tool_plan_runtime_executes_code_step_and_downstream_binding(monkeypatch, tmp_path):
    def fake_run_tool(tool_name, args, runtime_ctx=None):
        return {
            "tool": tool_name,
            "ok": True,
            "data": {
                "rows": [
                    {"name": "A", "score": 3},
                    {"name": "B", "score": 7},
                ]
            },
            "error": "",
        }

    monkeypatch.setattr("src.services.tool_plan_runtime_service.run_tool", fake_run_tool)
    code = """
import json
import os

with open(os.environ["CODE_INPUT_JSON"], "r", encoding="utf-8") as f:
    payload = json.load(f)
rows = payload["inputs"]["rows"]
ranked = sorted(rows, key=lambda item: item["score"], reverse=True)
out = {
    "tool": "analysis_python",
    "ok": True,
    "data": {
        "structured_data": {"analysis_table": ranked},
        "render_blocks": [
            {"type": "table", "title": "Ranked", "data": {"columns": ["name", "score"], "rows": ranked}}
        ]
    },
    "error": ""
}
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
"""
    plan = {
        "objective": "取上游 rows 并用 Python 排序",
        "plan_type": "planned_run",
        "work_items": [
            {
                "step_id": "step_1",
                "type": "tool",
                "name": "mock_rows",
                "output_binding": {"rows": "$result.data.rows"},
            },
            {
                "step_id": "step_2",
                "type": "code",
                "name": "analysis_python",
                "depends_on": ["step_1"],
                "input_binding": {"rows": "${step_1.rows}"},
                "runtime_profile": {"backend": "local_dev", "limits": {"timeout_ms": 2000}},
                "code_task_spec": {"task_kind": "table_analysis", "solution_mode": "generated_inline", "code": code},
                "output_binding": {
                    "analysis_table": "$result.data.structured_data.analysis_table",
                    "render_blocks": "$result.data.render_blocks",
                },
            },
            {
                "step_id": "step_3",
                "type": "transform",
                "name": "$input | first() | project(name=@.name, score=@.score)",
                "depends_on": ["step_2"],
                "input_binding": {"$input": "${step_2.analysis_table}"},
                "output_binding": {"top_name": "$result.name"},
            },
        ],
        "presentation_plan": {"page_type": "analysis_result", "layout": "report"},
    }
    service = ToolPlanRuntimeService(
        code_work_item_runner=CodeWorkItemRunner(
            python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
            runtime_root=str(tmp_path),
        ),
        enable_tool_preflight=False,
    )

    result = service.execute_for_assistant(execution_plan=plan, user_text="用代码排序")

    items = result["items"]
    assert [item["status"] for item in items] == ["completed", "completed", "completed"]
    assert items[1]["name"] == "analysis_python"
    assert result["runtime_trace"]["local_events"][1]["event_type"] == "code_call"
    assert any(event["event_type"] == "code_result" for event in result["runtime_trace"]["local_events"])
    blocks = [block for section in result["task_result"]["render_payload"]["sections"] for block in (section.get("blocks") or [])]
    assert any(block.get("title") == "Ranked" for block in blocks)
    runtime_nodes = {item["node"]: item for item in result["runtime_node_results"]}
    assert result["runtime_contract"]["phase"] == "execution"
    assert [item["module"] for item in result["runtime_modules"]] == ["execution_runtime"]
    assert runtime_nodes["runtime_execute"]["status"] == "completed"
    assert runtime_nodes["observe_present_writeback"]["status"] == "completed"
    assert result["runtime_feedback_protocol"]["loop_levels"] == ["node_internal_loop", "module_feedback_loop"]


def test_tool_plan_runtime_fails_code_step_with_missing_upstream_binding(monkeypatch, tmp_path):
    calls = {"count": 0}

    class RunnerStub:
        def run(self, **kwargs):
            calls["count"] += 1
            raise AssertionError("code runner should not be called for missing bindings")

    def fake_run_tool(tool_name, args, runtime_ctx=None):
        return {"tool": tool_name, "ok": True, "data": {"rows": [{"name": "A"}]}, "error": ""}

    monkeypatch.setattr("src.services.tool_plan_runtime_service.run_tool", fake_run_tool)
    plan = {
        "objective": "missing code input should fail before execution",
        "plan_type": "planned_run",
        "work_items": [
            {
                "step_id": "step_1",
                "type": "tool",
                "name": "mock_rows",
                "output_binding": {"rows": "$result.data.rows"},
            },
            {
                "step_id": "step_2",
                "type": "code",
                "name": "analysis_python",
                "depends_on": ["step_1"],
                "input_binding": {"missing_rows": "${step_1.not_exported}"},
                "runtime_profile": {"backend": "local_dev", "limits": {"timeout_ms": 2000}},
                "code_task_spec": {"task_kind": "table_analysis", "solution_mode": "generated_inline", "code": "print('no')"},
                "output_binding": {"analysis_table": "$result.data.structured_data.analysis_table"},
            },
        ],
    }
    service = ToolPlanRuntimeService(code_work_item_runner=RunnerStub(), enable_tool_preflight=False)

    result = service.execute_for_assistant(execution_plan=plan, user_text="用代码排序")

    assert calls["count"] == 0
    assert result["items"][1]["status"] == "failed"
    assert result["items"][1]["reason"] == "missing_upstream_binding"
    assert result["items"][1]["failure_kind"] == "missing_upstream_binding"
    runtime_execute = {item["node"]: item for item in result["runtime_node_results"]}["runtime_execute"]
    assert runtime_execute["status"] == "failed"
    assert runtime_execute["feedback"]["reason_code"] == "tool_schema_mismatch"
    assert runtime_execute["feedback"]["to_node"] == "agent_runtime_planning"


def test_tool_plan_runtime_turns_retention_error_into_feedback(monkeypatch):
    def fake_run_tool(tool_name, args, runtime_ctx=None):
        return {"tool": tool_name, "ok": True, "data": {"items": [{"name": "A"}]}, "error": ""}

    def fake_reduce(tool_name, result):
        raise ValueError("bad retention path")

    monkeypatch.setattr("src.services.tool_plan_runtime_service.run_tool", fake_run_tool)
    monkeypatch.setattr("src.services.tool_plan_runtime_service.reduce_tool_result_for_runtime", fake_reduce)

    service = ToolPlanRuntimeService(enable_tool_preflight=False)
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "retention failed", "facts": [], "risks": []}, None),
    )
    monkeypatch.setattr(
        service,
        "_build_render_payload",
        lambda **kwargs: {"sections": [], "reference_materials": []},
    )
    plan = {
        "objective": "测试 retention 失败",
        "plan_type": "tool_plan_run",
        "work_items": [{"step_id": "step_1", "type": "tool", "name": "mock_tool"}],
    }

    result = service.execute_for_assistant(execution_plan=plan, user_text="测试")

    assert result["items"][0]["status"] == "failed"
    assert result["items"][0]["reason"] == "result_retention_error"
    assert result["items"][0]["feedback"]["suggested_action"] == "retry_step"
    assert result["runtime_feedback"]["status"] == "failed"
    assert result["runtime_feedback"]["reason_code"] == "execution_failed"
    runtime_execute = {item["node"]: item for item in result["runtime_node_results"]}["runtime_execute"]
    assert runtime_execute["feedback"]["to_node"] == "runtime_execute"
