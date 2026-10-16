from types import SimpleNamespace

import src.tools.registry as tool_registry
from src.services.tool_plan_runtime_service import ToolPlanRuntimeService


class _RecordingRuntime:
    def __init__(self):
        self.calls = []

    def begin_artifact_run(self, **kwargs):
        return {
            "thread_id": None,
            "task_id": None,
            "turn_id": None,
            "local_events": [],
        }

    def append_event(self, **kwargs):
        return None

    def finish_task(self, **kwargs):
        return None

    def execute_tool(self, tool_name, args, executor):
        self.calls.append((tool_name, dict(args)))
        clean_args = {key: value for key, value in args.items() if key != "_runtime"}
        return executor(clean_args)


def _install_probe_tool(monkeypatch):
    registry = dict(tool_registry.TOOL_REGISTRY)
    registry["runtime_tracking_probe"] = "runtime_tracking_probe_module:run"
    monkeypatch.setattr(tool_registry, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(
        tool_registry,
        "import_module",
        lambda _name: SimpleNamespace(
            run=lambda args: {
                "tool": "runtime_tracking_probe",
                "ok": True,
                "data": {"received": dict(args)},
                "error": "",
            }
        ),
    )


def test_direct_registry_call_keeps_runtime_tracking(monkeypatch):
    _install_probe_tool(monkeypatch)
    registry_runtime = _RecordingRuntime()
    monkeypatch.setattr(
        tool_registry,
        "_runtime_execution_service",
        registry_runtime,
    )

    result = tool_registry.run_tool("runtime_tracking_probe", {"value": 1})

    assert result["ok"] is True
    assert len(registry_runtime.calls) == 1


def test_direct_tool_argument_cannot_forge_tracking_owner(monkeypatch):
    _install_probe_tool(monkeypatch)
    registry_runtime = _RecordingRuntime()
    monkeypatch.setattr(
        tool_registry,
        "_runtime_execution_service",
        registry_runtime,
    )

    result = tool_registry.run_tool(
        "runtime_tracking_probe",
        {
            "value": 1,
            "_runtime": {"_execution_tracking_owner": "tool_plan_runtime"},
        },
    )

    assert result["ok"] is True
    assert len(registry_runtime.calls) == 1
    assert "_runtime" not in registry_runtime.calls[0][1]


def test_tool_plan_records_each_physical_tool_call_once(monkeypatch):
    _install_probe_tool(monkeypatch)
    registry_runtime = _RecordingRuntime()
    outer_runtime = _RecordingRuntime()
    monkeypatch.setattr(
        tool_registry,
        "_runtime_execution_service",
        registry_runtime,
    )

    service = ToolPlanRuntimeService(
        runtime_execution_service=outer_runtime,
        enable_tool_preflight=False,
    )
    monkeypatch.setattr(
        service,
        "_build_final_output",
        lambda **kwargs: ({"summary": "ok", "facts": [], "risks": []}, None),
    )
    monkeypatch.setattr(
        service,
        "_build_render_payload",
        lambda **kwargs: {"sections": [], "reference_materials": []},
    )

    result = service.execute_for_assistant(
        execution_plan={
            "objective": "tracking probe",
            "work_items": [
                {
                    "step_id": "step_1",
                    "type": "tool",
                    "name": "runtime_tracking_probe",
                    "arguments": {"value": 1},
                }
            ],
        },
        user_text="tracking probe",
        thread_context={},
    )

    assert result["items"][0]["status"] == "completed"
    assert len(outer_runtime.calls) == 1
    assert len(registry_runtime.calls) == 0
    assert outer_runtime.calls[0][1]["_runtime"]["_execution_tracking_owner"] == "tool_plan_runtime"
