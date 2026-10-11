import asyncio
import json
from types import SimpleNamespace

import pytest

from src.services.user_task_agent_tools import build_user_task_agent_tools, _receipt_snapshot
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


def test_submission_uses_original_turn_and_only_adds_confirmed_context(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(tasks, "_service", lambda: service)
    original = "用收盘前最后30-5分钟的行情训练策略，预测次日高开。"
    runtime = {**ACTOR, "_task_user_text": original}
    tasks.run_submit({"instruction": "改成14:00开始", "_runtime": {"_task_user_text": "伪造原文"}}, runtime_ctx=runtime)
    tasks.run_submit({}, runtime_ctx=runtime)
    assert service.calls[0] == service.calls[1]
    assert service.calls[0]["instruction"] == original
    tasks.run_submit({"context": "此前已确认只研究银行行业，最多6个实验。"},
                     runtime_ctx={**runtime, "turn_id": "turn2", "_task_user_text": "按刚才的范围开始。"})
    assert service.calls[2]["instruction"] == (
        "[本轮用户原文]\n按刚才的范围开始。\n\n[前文已确认的补充]\n此前已确认只研究银行行业，最多6个实验。")
    assert original not in service.calls[2]["instruction"]
    assert service.calls[2]["idempotency_key"] != service.calls[0]["idempotency_key"]


def test_submission_rejects_missing_or_oversized_text_without_truncation(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(tasks, "_service", lambda: service)
    maximum = "研" * 4000
    tasks.run_submit({}, runtime_ctx={**ACTOR, "_task_user_text": maximum})
    assert service.calls[0]["instruction"] == maximum
    for args, runtime in (
        ({}, ACTOR),
        ({"context": "不能独立代替任务说明"}, ACTOR),
        ({"instruction": "旧接口" * 1500}, ACTOR),
        ({"instruction": "不得用短改写绕过原文上限"}, {**ACTOR, "_task_user_text": maximum + "研"}),
        ({"context": "一条补充"}, {**ACTOR, "_task_user_text": maximum}),
    ):
        with pytest.raises(ValueError, match="4000"):
            tasks.run_submit(args, runtime_ctx=runtime)
    with pytest.raises(ValueError, match="上下文需要是文本"):
        tasks.run_submit({"context": {"instruction": "不得作为字段树"}},
                         runtime_ctx={**ACTOR, "_task_user_text": "研究"})
    assert len(service.calls) == 1


def test_conversation_tools_recheck_live_owner_and_preserve_receipt(monkeypatch):
    service = TaskService()
    monkeypatch.setattr(tasks, "_service", lambda: service)
    runtime = SimpleNamespace(owner_ids=["alice"], tool_context={
        "_task_actor": {"user_id": "alice", "user_type": "member"},
        "_task_conversation_id": "thread1", "_task_turn_id": "turn1",
        "_task_user_text": "后台生成报告"}, tracker={"calls": []})
    tools = {tool.name: tool for tool in build_user_task_agent_tools(runtime)}
    assert set(tools) == {"task_submit", "task_get", "task_list", "task_cancel"}
    response = asyncio.run(tools["task_submit"].handler({}))
    result = json.loads(response["content"][0]["text"])
    assert result["ok"] and result["task_id"] == "task_1"
    receipt = runtime.tracker["calls"][0]["task_receipt"]
    assert receipt["task"]["requirement_brief"] == "后台生成报告"
    assert receipt["task"]["trigger"] == {}
    assert receipt["run_id"] == "run_1" and receipt["run"] is None
    assert receipt["recorded_at"]
    runtime.tool_context.update(_task_turn_id="turn2", _task_user_text="只跑两次实验")
    response = asyncio.run(tools["task_submit"].handler({"instruction": "模型不应重写原文"}))
    assert json.loads(response["content"][0]["text"])["ok"] is True
    assert service.calls[1]["instruction"] == "只跑两次实验"
    assert service.calls[1]["idempotency_key"] != service.calls[0]["idempotency_key"]
    runtime.owner_ids = ["bob"]
    rejected = asyncio.run(tools["task_submit"].handler({"instruction": "后台生成报告"}))
    assert json.loads(rejected["content"][0]["text"])["ok"] is False
    assert "task_receipt" not in runtime.tracker["calls"][-1]
    assert len(service.calls) == 2
    assert build_user_task_agent_tools(runtime) == []


def test_task_receipt_preserves_observed_run_without_copying_large_results():
    payload = {"ok": True, "task": {"task_id": "task_1", "requirement_brief": "研究", "trigger": {"cron": "0 9 * * 1-5"}},
               "runs": [{"run_id": "old", "status": "completed", "created_at": "2026-09-01"},
                        {"run_id": "latest", "status": "running", "created_at": "2026-10-03", "progress": {"message": "训练第三个模型"}, "result": {"large": "body"}}]}
    receipt = _receipt_snapshot("task_get", payload)
    assert receipt["run"]["run_id"] == "latest"
    assert receipt["run"]["progress"]["message"] == "训练第三个模型"
    assert "result" not in receipt["run"]
    assert _receipt_snapshot("task_submit", {"ok": False, "error": "无法安排"}) is None
    assert _receipt_snapshot("task_list", {"ok": True, "tasks": []}) is None


@pytest.mark.parametrize("runtime", ["cc", "dsh"])
def test_chat_task_receipt_reaches_saved_surface_for_both_runtimes(runtime):
    from src.scenarios.financial_qa.service import FinancialQaCcService
    receipt = _receipt_snapshot("task_submit", {"ok": True, "task_id": "task_1", "run_id": "run_1",
                                               "requirement_brief": "后台研究未来七日上涨概率", "trigger": {}})
    observed = []
    def run_turn(**kwargs):
        observed.append(kwargs)
        return {"result": "已提交后台研究。", "result_refs": [],
                "tool_calls": [{"tool": "task_submit", "task_receipt": receipt}]}
    session = SimpleNamespace(run_turn=run_turn)
    service = FinancialQaCcService(enabled=True, session_service=session, dsh_session_service=session)
    result = service.answer(thread_id=1, turn_id=2, owner_id="alice", user_text="请后台研究",
                            dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route",
                                           "semantic_turn": {"ori_question": "路由改写的原文", "resolved_question": "路由补充"}},
                            application_context={"_task_actor": {"user_id": "alice", "user_type": "member"},
                                                 "_task_user_text": "客户端不能覆盖本轮原文"}, runtime=runtime)
    assert observed[0]["context"]["_task_user_text"] == "请后台研究"
    cards = [block for block in result["surface_blocks"] if block.get("semantic") == "user_task"]
    assert len(cards) == 1
    assert cards[0]["payload"]["task"]["requirement_brief"] == "后台研究未来七日上涨概率"
    assert cards[0]["payload"]["run_id"] == "run_1"


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
