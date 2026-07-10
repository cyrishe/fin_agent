import json
import os
import signal
import subprocess
import time

from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.python_execution_runtime import PythonExecutionRuntime


def _trusted_runner(tmp_path):
    return CodeWorkItemRunner(
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path),
    )


def _code_step(code, **overrides):
    step = {
        "step_id": "step_1",
        "type": "code",
        "name": "analysis_python",
        "runtime_profile": {
            "backend": "local_dev",
            "limits": {
                "timeout_ms": 2000,
                "stdout_chars": 20,
                "stderr_chars": 20,
                "output_json_bytes": 100000,
            },
        },
        "code_task_spec": {
            "task_kind": "table_analysis",
            "solution_mode": "generated_inline",
            "entrypoint": "run",
            "code": code,
        },
        "output_binding": {},
    }
    step.update(overrides)
    return step


def test_code_work_item_runner_executes_python_and_returns_envelope(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
import json
import os

with open(os.environ["CODE_INPUT_JSON"], "r", encoding="utf-8") as f:
    payload = json.load(f)
rows = payload["inputs"]["rows"]
print("hello from code runtime")
result = {
    "tool": "analysis_python",
    "ok": True,
    "data": {
        "structured_data": {"row_count": len(rows)},
        "render_blocks": [
            {"type": "table", "title": "Rows", "data": {"columns": ["name"], "rows": rows}}
        ]
    },
    "error": ""
}
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False)
"""

    run = runner.run(step=_code_step(code), resolved_inputs={"rows": [{"name": "A"}, {"name": "B"}]})

    assert run["status"] == "completed"
    assert run["result"]["ok"] is True
    assert run["result"]["data"]["structured_data"]["row_count"] == 2
    assert run["result"]["meta"]["diagnostics"]["stdout"].startswith("hello")
    assert run["result"]["meta"]["diagnostics"]["input_artifact"]["kind"] == "directory"
    assert run["result"]["meta"]["diagnostics"]["output_artifact"]["kind"] == "directory"


def test_code_work_item_runner_truncates_stdout_and_stderr(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
import json
import os
import sys

print("x" * 80)
print("y" * 80, file=sys.stderr)
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump({"ok": True, "data": {"structured_data": {}}, "error": ""}, f)
"""

    run = runner.run(step=_code_step(code), resolved_inputs={})
    diagnostics = run["result"]["meta"]["diagnostics"]

    assert run["status"] == "completed"
    assert len(diagnostics["stdout"]) < 80
    assert "truncated" in diagnostics["stdout"]
    assert "truncated" in diagnostics["stderr"]


def test_code_work_item_runner_marks_invalid_envelope_as_schema_invalid(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
import json
import os

with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump({"data": []}, f)
"""

    run = runner.run(step=_code_step(code), resolved_inputs={})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "schema_invalid"
    assert run["result"]["meta"]["failure_kind"] == "schema_invalid"


def test_code_work_item_runner_rejects_non_boolean_ok(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
import json
import os

with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump({"ok": "false", "data": {"structured_data": {}}, "error": ""}, f)
"""

    run = runner.run(step=_code_step(code), resolved_inputs={})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "schema_invalid"
    assert "boolean ok" in run["error"]


def test_code_work_item_runner_enforces_artifact_total_limit(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
import json
import os

with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "large.bin"), "wb") as f:
    f.write(b"x" * 128)
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump({"ok": True, "data": {"structured_data": {}}, "error": ""}, f)
"""
    step = _code_step(
        code,
        runtime_profile={
            "backend": "local_dev",
            "limits": {
                "timeout_ms": 2000,
                "stdout_chars": 2000,
                "stderr_chars": 2000,
                "artifact_total_bytes": 32,
            },
        },
    )

    run = runner.run(step=step, resolved_inputs={})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "output_limit_exceeded"
    assert run["diagnostics"]["artifact_total_bytes"] > 32


def test_code_work_item_runner_times_out_and_records_attempt(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
import time
time.sleep(2)
"""
    step = _code_step(
        code,
        runtime_profile={"backend": "local_dev", "limits": {"timeout_ms": 100, "stdout_chars": 2000, "stderr_chars": 2000}},
    )

    run = runner.run(step=step, resolved_inputs={})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "timeout"
    assert run["diagnostics"]["attempts"][0]["attempt"] == 1


def test_local_dev_timeout_kills_spawned_child_process(tmp_path):
    runner = _trusted_runner(tmp_path)
    child_pid_file = tmp_path / "child.pid"
    code = f"""
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
with open({str(child_pid_file)!r}, "w", encoding="utf-8") as f:
    f.write(str(child.pid))
    f.flush()
time.sleep(30)
"""
    step = _code_step(
        code,
        runtime_profile={"backend": "local_dev", "limits": {"timeout_ms": 200, "stdout_chars": 2000, "stderr_chars": 2000}},
    )

    run = runner.run(step=step, resolved_inputs={})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "timeout"
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise AssertionError(f"child process {child_pid} survived code runtime timeout")


def test_code_work_item_runner_does_not_exceed_max_attempts(tmp_path):
    runner = _trusted_runner(tmp_path)
    code = """
raise RuntimeError("boom")
"""
    step = _code_step(code, max_attempts=2)

    run = runner.run(step=step, resolved_inputs={})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "runtime_error"
    assert len(run["diagnostics"]["attempts"]) == 2


def test_python_execution_runtime_strict_fake_denies_forbidden_access(tmp_path):
    runtime = PythonExecutionRuntime(allow_unsafe_backends=True)
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    result = runtime.execute(
        code="import os\nprint(os.environ)",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        profile={"backend": "strict_fake"},
    )

    assert result["ok"] is False
    assert result["failure_kind"] == "permission_denied"


def test_python_execution_runtime_denies_unsafe_backend_by_default(tmp_path):
    runtime = PythonExecutionRuntime()
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    result = runtime.execute(
        code="print('should not run')",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        profile={"backend": "local_dev"},
    )

    assert result["ok"] is False
    assert result["failure_kind"] == "sandbox_unavailable"
    assert result["diagnostics"]["backend"] == "local_dev"


def test_python_execution_runtime_auto_fails_closed_without_formal_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr("src.services.python_execution_runtime.shutil.which", lambda name: None)
    runtime = PythonExecutionRuntime()
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    result = runtime.execute(
        code="""
import json
import os

with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump({"ok": True, "data": {"structured_data": {"escaped": True}}, "error": ""}, f)
""",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        profile={"backend": "auto", "backend_candidates": ["local_dev"]},
    )

    assert result["ok"] is False
    assert result["failure_kind"] == "sandbox_unavailable"
    assert result["diagnostics"]["backend"] == "auto"
    assert not (output_dir / "output.json").exists()


def test_python_execution_runtime_auto_selects_formal_fallback_backend(monkeypatch):
    def fake_which(name):
        return f"/usr/bin/{name}" if name == "docker" else None

    monkeypatch.setattr("src.services.python_execution_runtime.shutil.which", fake_which)
    runtime = PythonExecutionRuntime()

    backend = runtime._select_backend({"backend": "auto", "backend_candidates": ["bwrap", "docker", "local_dev"]})

    assert backend == "docker"


def test_container_backend_tracks_container_for_timeout_cleanup(monkeypatch, tmp_path):
    runtime = PythonExecutionRuntime()
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    cleanup_commands = []

    def fake_execute_subprocess(**kwargs):
        command = kwargs["command"]
        assert "--name" in command
        assert "--cidfile" in command
        cleanup = kwargs["timeout_cleanup"]
        cleanup()
        return {
            "ok": False,
            "failure_kind": "timeout",
            "diagnostics": {"backend": "docker", "attempt": 1, "stdout": "", "stderr": "", "duration_ms": 1},
        }

    def fake_run(command, **kwargs):
        cleanup_commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runtime, "_execute_subprocess", fake_execute_subprocess)
    monkeypatch.setattr("src.services.python_execution_runtime.subprocess.run", fake_run)

    result = runtime._execute_container(
        code="print('x')",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        runtime_profile={"backend": "docker", "limits": {"timeout_ms": 1}},
        backend="docker",
        executable="docker",
        code_hash="abc",
        attempt=1,
    )

    assert result["failure_kind"] == "timeout"
    assert cleanup_commands
    assert cleanup_commands[0][:3] == ["docker", "rm", "-f"]


def test_bwrap_mounts_avoid_broad_user_config_prefixes(tmp_path):
    runtime = PythonExecutionRuntime()
    mounts = runtime._bwrap_python_mounts()

    assert "/usr/local" not in mounts
    assert "/opt" not in mounts
    assert str(tmp_path) not in mounts


def test_python_execution_runtime_bwrap_formal_backend_or_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_AGENT_TEST_SECRET", "should_not_escape")
    runtime = PythonExecutionRuntime()
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    result = runtime.execute(
        code="""
import json
import os

secret = os.environ.get("STOCK_AGENT_TEST_SECRET", "")
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump({"ok": True, "data": {"structured_data": {"secret": secret}}, "error": ""}, f)
""",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        profile={"backend": "bwrap", "limits": {"timeout_ms": 3000}},
    )

    if result["failure_kind"] == "sandbox_unavailable":
        assert result["ok"] is False
        return

    assert result["ok"] is True
    assert result["diagnostics"]["backend"] == "bwrap"
    assert result["diagnostics"]["backend_is_formal_sandbox"] is True
    output = json.loads((output_dir / "output.json").read_text(encoding="utf-8"))
    assert output["data"]["structured_data"]["secret"] == ""
