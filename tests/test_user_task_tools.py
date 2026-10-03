import asyncio
import json
from types import SimpleNamespace

import pytest

from src.services.user_task_agent_tools import build_user_task_agent_tools
from src.tools import user_task_tools as tasks


class TaskService:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"schedule_id": "task_1", "initial_run_id": "run_1",
                "requirement_brief": kwargs["instruction"], "trigger": {}}

    def get_run(self, *, owner_user_id, run_id):
        if owner_user_id != "alice":
            raise LookupError("任务不存在")
        return {"run_id": run_id, "schedule_id": "task_1", "status": "running"}

    def cancel_run(self, **kwargs):
        return self.get_run(**kwargs)

    def list(self, **kwargs):
        return []


ACTOR = {"owner_user_id": "alice", "user_type": "member", "conversation_id": "thread1", "turn_id": "turn1"}


def test_task_submission_uses_trusted_owner_and_retry_key(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(tasks, "_service", lambda: service)
    args = {"instruction": "后台训练，最多 3 个实验", "owner_user_id": "mallory", "_runtime": ACTOR}
    result = tasks.run_submit(args, runtime_ctx=ACTOR)
    tasks.run_submit(args, runtime_ctx=ACTOR)
    assert service.calls[0] == service.calls[1]
    assert service.calls[0]["owner_user_id"] == "alice"
    assert service.calls[0]["source_conversation"] == "thread1"
    assert result["task_url"] == "?view=tasks&task=task_1&run=run_1"
    assert "completed" not in result.values()
    with pytest.raises(ValueError, match="当前用户身份"):
        tasks.run_submit(args)
    with pytest.raises(PermissionError):
        tasks.run_submit(args, runtime_ctx={**ACTOR, "user_type": "guest"})
    with pytest.raises(ValueError, match="递归"):
        tasks.run_submit(args, runtime_ctx={**ACTOR, "task_run_id": "parent"})


def test_get_cancel_retain_owner_scope(monkeypatch):
    monkeypatch.setattr(tasks, "_service", TaskService)
    assert tasks.run_get({"run_id": "run_1"}, runtime_ctx=ACTOR)["run"]["status"] == "running"
    for handler in (tasks.run_get, tasks.run_cancel):
        with pytest.raises(LookupError):
            handler({"run_id": "run_1"}, runtime_ctx={**ACTOR, "owner_user_id": "bob"})
    with pytest.raises(ValueError):
        tasks.run_get({}, runtime_ctx=ACTOR)


def test_conversation_tools_recheck_live_owner_and_preserve_receipt(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(tasks, "_service", lambda: service)
    runtime = SimpleNamespace(owner_ids=["alice"], tool_context={
        "_task_actor": {"user_id": "alice", "user_type": "member"},
        "_task_conversation_id": "thread1", "_task_turn_id": "turn1"}, tracker={"calls": []})
    tools = {tool.name: tool for tool in build_user_task_agent_tools(runtime)}
    assert set(tools) == {"task_submit", "task_get", "task_list", "task_cancel"}
    response = asyncio.run(tools["task_submit"].handler({"instruction": "后台生成报告"}))
    result = json.loads(response["content"][0]["text"])
    assert result["ok"] and result["task_id"] == "task_1"
    runtime.owner_ids = ["bob"]
    rejected = asyncio.run(tools["task_submit"].handler({"instruction": "后台生成报告"}))
    assert json.loads(rejected["content"][0]["text"])["ok"] is False
    assert len(service.calls) == 1
    assert build_user_task_agent_tools(runtime) == []


def test_registry_never_accepts_client_runtime_owner():
    from src.tools.registry import run_tool
    with pytest.raises(ValueError, match="当前用户身份"):
        run_tool("task_get", {"run_id": "run_1", "_runtime": ACTOR},
                 runtime_ctx={"_execution_tracking_owner": "unit-test"})


def test_task_files_are_scoped_and_can_feed_later_steps(tmp_path):
    from src.tools.file_io_tool import run
    from src.tools.file_intake_tools import run_csv
    context = {**ACTOR, "task_run_id": "run_a", "task_output_dir": str(tmp_path / "run_a" / "step1" / "attempt1")}
    written = run({"action": "write", "file_type": "text", "content": "研究报告", "file_name": "report.md"}, runtime_ctx=context)
    ref = written["data"]["artifact_ref"]
    assert written["ok"] and written["artifacts"]
    read = run({"action": "read", "file_id": ref}, runtime_ctx=context)
    assert read["report_markdown"] == "研究报告"
    other = {**context, "task_run_id": "run_b", "task_output_dir": str(tmp_path / "run_b" / "step1" / "attempt1")}
    assert run({"action": "read", "file_id": ref}, runtime_ctx=other)["ok"] is False
    for reader in (run, run_csv):
        with pytest.raises(ValueError, match="本次任务"):
            reader({"file_path": str(tmp_path / "secret.csv"), "_runtime": {"data_root": "/"}}, runtime_ctx=context)
