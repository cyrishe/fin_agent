import datetime as dt
import time

import pytest
from flask import Flask

from src.services.scheduled_task_compiler import (
    ScheduledTaskCompileError,
    ScheduledTaskCompiler,
)
from src.services.scheduled_task_executor import (
    ScheduledTaskExecutor,
)
from src.services.scheduled_task_protocol import (
    CronExpression,
    ScheduledTaskProtocolError,
    normalize_schedule_draft,
)
from src.services.scheduled_task_service import ScheduledTaskService
from src.services.scheduled_task_store import InMemoryScheduledTaskStore
from src.services.scheduled_task_worker import ScheduledTaskWorker
from src.web.scheduled_task_routes import create_scheduled_task_blueprint


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 30, 0, 0, tzinfo=UTC)


def draft(*, cron="0 9 * * 1-5", steps=None):
    return {
        "requirement_brief": "每个工作日上午九点查询贵州茅台行情",
        "trigger": {"cron": cron, "timezone": "Asia/Shanghai"},
        "execution_plan": {
            "steps": steps
            or [
                {
                    "step_id": "quote",
                    "type": "tool",
                    "target_ref": {"kind": "tool", "name": "stock_realtime_quote"},
                    "inputs": {"code": "600519"},
                    "depends_on": [],
                }
            ]
        },
    }


class FakeAssetService:
    def __init__(self, allowed=None):
        self.allowed = set(allowed or {"stock_realtime_quote", "report_skill"})
        self.calls = []

    def load_contract(self, *, kind, name, owner_ids, allow_inactive):
        self.calls.append((kind, name, tuple(owner_ids), allow_inactive))
        if name not in self.allowed:
            raise ValueError(f"asset unavailable: {name}")
        return {
            "kind": kind,
            "name": name,
            "display_name": name,
            "input_schema": {"type": "object"},
        }


class FakeToolStudio:
    def list_tools(self):
        return [
            {
                "tool_name": "stock_realtime_quote",
                "description": "股票行情",
                "input_schema": {"type": "object"},
            }
        ]


class FakeSkillStudio:
    def list_skills(self):
        return [
            {
                "skill_name": "report_skill",
                "description": "生成报告",
                "input_schema": {"type": "object"},
                "availability": {"lifecycle": "active"},
            }
        ]


def compiler(*, llm=None, asset_service=None):
    return ScheduledTaskCompiler(
        llm_chat=llm or (lambda *_args, **_kwargs: (draft(), {})),
        asset_service=asset_service or FakeAssetService(),
        tool_studio=FakeToolStudio(),
        skill_studio=FakeSkillStudio(),
    )


def test_cron_next_after_respects_timezone_and_weekday():
    result = CronExpression("0 9 * * 1-5").next_after(
        NOW,
        timezone="Asia/Shanghai",
    )
    assert result == dt.datetime(2026, 7, 30, 1, 0, tzinfo=UTC)


def test_cron_integer_step_runs_from_start_through_field_maximum():
    expression = CronExpression("5/10 * * * *")
    assert expression.minutes == {5, 15, 25, 35, 45, 55}


def test_protocol_accepts_declared_result_binding_and_rejects_undeclared_one():
    steps = [
        {
            "step_id": "first",
            "type": "tool",
            "target_ref": {"name": "stock_realtime_quote"},
            "inputs": {},
            "depends_on": [],
        },
        {
            "step_id": "second",
            "type": "skill",
            "target_ref": {"name": "report_skill"},
            "inputs": {"quote": {"$from": "first.result.data"}},
            "depends_on": ["first"],
        },
    ]
    normalized = normalize_schedule_draft(draft(steps=steps), now=NOW)
    assert normalized["execution_plan"]["steps"][1]["inputs"]["quote"]["$from"] == "first.result.data"

    steps[1]["depends_on"] = []
    with pytest.raises(ScheduledTaskProtocolError) as exc_info:
        normalize_schedule_draft(draft(steps=steps), now=NOW)
    assert exc_info.value.code == "undeclared_result_dependency"


def test_compiler_uses_catalog_and_server_owner_for_authorization():
    assets = FakeAssetService()
    seen = {}

    def fake_llm(messages, enable_think):
        seen["messages"] = messages
        return draft(), {"total_tokens": 12}

    result = compiler(llm=fake_llm, asset_service=assets).compile(
        instruction="每个工作日上午九点查询贵州茅台行情",
        owner_user_id="user_a",
        now=NOW,
    )
    assert result["compile_source"] == "natural_language"
    assert result["next_run_at"] == dt.datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    assert assets.calls == [("tool", "stock_realtime_quote", ("user_a",), False)]
    assert "stock_realtime_quote" in seen["messages"][-1]["content"]


def test_compiler_surfaces_clarification_without_creating_protocol_state():
    def fake_llm(_messages, enable_think):
        return {
            "error": {
                "code": "schedule_needs_clarification",
                "message": "请说明每天几点执行",
            }
        }, {}

    with pytest.raises(ScheduledTaskCompileError) as exc_info:
        compiler(llm=fake_llm).compile(
            instruction="每天查一下",
            owner_user_id="user_a",
            now=NOW,
        )
    assert exc_info.value.code == "schedule_needs_clarification"


def test_compiler_rejects_missing_required_input_that_would_certainly_fail():
    class RequiredAssetService(FakeAssetService):
        def load_contract(self, **kwargs):
            contract = super().load_contract(**kwargs)
            contract["input_schema"] = {
                "type": "object",
                "required": ["code"],
                "properties": {"code": {"type": "string"}},
            }
            return contract

    missing = draft()
    missing["execution_plan"]["steps"][0]["inputs"] = {}
    with pytest.raises(ScheduledTaskCompileError) as exc_info:
        compiler(asset_service=RequiredAssetService()).compile(
            instruction="定时查询",
            owner_user_id="user_a",
            draft=missing,
            now=NOW,
        )
    assert exc_info.value.code == "missing_step_input"


def test_store_is_owner_scoped_idempotent_and_coalesces_missed_slots():
    store = InMemoryScheduledTaskStore()
    normalized = normalize_schedule_draft(draft(cron="* * * * *"), now=NOW)
    first = store.create(
        owner_user_id="user_a",
        draft=normalized,
        idempotency_key="request-1",
    )
    same = store.create(
        owner_user_id="user_a",
        draft=normalized,
        idempotency_key="request-1",
    )
    assert first["schedule_id"] == same["schedule_id"]
    assert store.get_for_owner(
        schedule_id=first["schedule_id"],
        owner_user_id="user_b",
    ) is None

    much_later = NOW + dt.timedelta(hours=2)
    assert store.materialize_due(now=much_later) == 1
    assert store.materialize_due(now=much_later) == 0
    assert len(store.runs) == 1
    assert first["schedule_id"] == next(iter(store.runs.values()))["schedule_id"]


class FakeSkillResult:
    def to_dict(self):
        return {"ok": True, "final_output": {"summary": "done"}}


class FakeSkillRunner:
    def __init__(self):
        self.calls = []

    def run(self, name, inputs, runtime_context):
        self.calls.append((name, inputs, runtime_context))
        return FakeSkillResult()


def test_executor_runs_dependencies_and_resolves_result_bindings():
    calls = []
    skill = FakeSkillRunner()

    def tool_runner(name, inputs, runtime_ctx):
        calls.append((name, inputs, runtime_ctx))
        return {"data": {"price": 1500}}

    plan = normalize_schedule_draft(
        draft(
            steps=[
                {
                    "step_id": "quote",
                    "type": "tool",
                    "target_ref": {"name": "stock_realtime_quote"},
                    "inputs": {"code": "600519"},
                    "depends_on": [],
                },
                {
                    "step_id": "report",
                    "type": "skill",
                    "target_ref": {"name": "report_skill"},
                    "inputs": {"price": {"$from": "quote.result.data.price"}},
                    "depends_on": ["quote"],
                },
            ]
        ),
        now=NOW,
    )["execution_plan"]
    executor = ScheduledTaskExecutor(
        authorizer=lambda current_plan, owner: True,
        tool_runner=tool_runner,
        skill_runner=skill,
    )
    result = executor.execute(
        {
            "run_id": "run_1",
            "schedule_id": "sch_1",
            "owner_user_id": "user_a",
            "execution_plan": plan,
        }
    )
    assert calls[0][1] == {"code": "600519"}
    assert calls[0][2]["custom_tool_owner_ids"] == ["user_a"]
    assert skill.calls[0][1] == {"price": 1500}
    assert [step["status"] for step in result["steps"]] == ["completed", "completed"]


def test_registry_forwards_server_runtime_scope_to_execution_trace(monkeypatch):
    from src.tools import registry

    seen = {}

    class FakeRuntimeExecution:
        def execute_tool(self, *, tool_name, args, executor):
            seen.update({"tool_name": tool_name, "args": args})
            return {"ok": True}

    monkeypatch.setattr(registry, "_runtime_execution_service", FakeRuntimeExecution())
    result = registry.run_tool(
        "stock_realtime_quote",
        {"code": "600519"},
        runtime_ctx={"owner_type": "user", "owner_id": "user_a"},
    )
    assert result["ok"] is True
    assert seen["args"]["_runtime"]["owner_id"] == "user_a"


def test_executor_reauthorizes_before_any_step_runs():
    called = []

    def reject(_plan, _owner):
        raise ValueError("revoked")

    executor = ScheduledTaskExecutor(
        authorizer=reject,
        tool_runner=lambda *args, **kwargs: called.append(args),
        skill_runner=FakeSkillRunner(),
    )
    with pytest.raises(ValueError, match="revoked"):
        executor.execute(
            {
                "run_id": "run_1",
                "schedule_id": "sch_1",
                "owner_user_id": "user_a",
                "execution_plan": normalize_schedule_draft(draft(), now=NOW)["execution_plan"],
            }
        )
    assert called == []


class FakeCompiler:
    def compile(self, *, instruction, owner_user_id, draft, now):
        return normalize_schedule_draft(draft or globals()["draft"](), now=now or NOW)


def test_service_never_uses_request_owner_and_routes_hide_other_users():
    store = InMemoryScheduledTaskStore()
    service = ScheduledTaskService(store=store, compiler=FakeCompiler())
    app = Flask(__name__)
    app.register_blueprint(
        create_scheduled_task_blueprint(
            service=service,
            identity_resolver=lambda: {"user_id": "server_user"},
        )
    )
    client = app.test_client()
    response = client.post(
        "/api/schedules",
        json={
            "instruction": "创建任务",
            "owner_user_id": "attacker_selected_owner",
            "draft": draft(),
        },
        headers={"Idempotency-Key": "route-1"},
    )
    assert response.status_code == 201
    schedule = response.get_json()["schedule"]
    assert schedule["owner_user_id"] == "server_user"
    queued = client.post(f"/api/schedules/{schedule['schedule_id']}/run")
    assert queued.status_code == 202
    run_id = queued.get_json()["run"]["run_id"]
    assert client.get(f"/api/schedule-runs/{run_id}").get_json()["run"]["status"] == "pending"
    assert len(
        client.get(f"/api/schedules/{schedule['schedule_id']}/runs").get_json()["runs"]
    ) == 1

    other_service = ScheduledTaskService(store=store, compiler=FakeCompiler())
    other_app = Flask("other")
    other_app.register_blueprint(
        create_scheduled_task_blueprint(
            service=other_service,
            identity_resolver=lambda: {"user_id": "other_user"},
        )
    )
    hidden = other_app.test_client().get(f"/api/schedules/{schedule['schedule_id']}")
    assert hidden.status_code == 404
    assert other_app.test_client().get(f"/api/schedule-runs/{run_id}").status_code == 404


def test_worker_claims_executes_and_finishes_run():
    store = InMemoryScheduledTaskStore()
    normalized = normalize_schedule_draft(draft(cron="* * * * *"), now=NOW)
    item = store.create(owner_user_id="user_a", draft=normalized)
    store.enqueue_manual(
        schedule_id=item["schedule_id"],
        owner_user_id="user_a",
        now=NOW,
    )

    class FakeExecutor:
        def execute(self, run, before_step):
            before_step("quote")
            return {"steps": [{"step_id": "quote", "status": "completed"}]}

    worker = ScheduledTaskWorker(
        store=store,
        executor=FakeExecutor(),
        worker_id="worker-1",
        lease_seconds=60,
    )
    outcome = worker.run_once()
    assert outcome["completed"] is True
    run = next(iter(store.runs.values()))
    assert run["status"] == "completed"
    assert run["lease_owner"] == ""
    public_run = store.get_run_for_owner(run_id=run["run_id"], owner_user_id="user_a")
    assert public_run["result"]["steps"][0]["status"] == "completed"
    assert store.get_run_for_owner(run_id=run["run_id"], owner_user_id="user_b") is None


def test_worker_renews_lease_while_a_step_is_still_running():
    class CountingStore(InMemoryScheduledTaskStore):
        def __init__(self):
            super().__init__()
            self.renewal_count = 0

        def renew_lease(self, **kwargs):
            self.renewal_count += 1
            return super().renew_lease(**kwargs)

    store = CountingStore()
    normalized = normalize_schedule_draft(draft(cron="0 0 1 1 *"), now=NOW)
    item = store.create(owner_user_id="user_a", draft=normalized)
    store.enqueue_manual(
        schedule_id=item["schedule_id"],
        owner_user_id="user_a",
        now=NOW,
    )

    class SlowExecutor:
        def execute(self, _run, before_step):
            before_step("quote")
            time.sleep(0.25)
            return {"steps": [{"step_id": "quote", "status": "completed"}]}

    outcome = ScheduledTaskWorker(
        store=store,
        executor=SlowExecutor(),
        worker_id="heartbeat-worker",
        lease_seconds=30,
        heartbeat_seconds=0.02,
    ).run_once()

    assert outcome["completed"] is True
    assert store.renewal_count >= 2


def test_http_to_worker_full_chain_executes_tool_then_skill():
    store = InMemoryScheduledTaskStore()
    service = ScheduledTaskService(store=store, compiler=FakeCompiler())
    app = Flask("scheduled-task-e2e")
    app.register_blueprint(
        create_scheduled_task_blueprint(
            service=service,
            identity_resolver=lambda: {"user_id": "server_user"},
        )
    )
    client = app.test_client()
    sequential_draft = draft(
        cron="0 0 1 1 *",
        steps=[
            {
                "step_id": "quote",
                "type": "tool",
                "target_ref": {"kind": "tool", "name": "stock_realtime_quote"},
                "inputs": {"code": "600519"},
                "depends_on": [],
            },
            {
                "step_id": "summary",
                "type": "skill",
                "target_ref": {"kind": "skill", "name": "report_skill"},
                "inputs": {"quote": {"$from": "quote.result.data"}},
                "depends_on": ["quote"],
            },
        ]
    )
    created = client.post(
        "/api/schedules",
        json={"instruction": "每个工作日上午九点先查行情再总结", "draft": sequential_draft},
        headers={"Idempotency-Key": "full-chain-1"},
    )
    assert created.status_code == 201
    schedule_id = created.get_json()["schedule"]["schedule_id"]
    queued = client.post(f"/api/schedules/{schedule_id}/run", json={})
    assert queued.status_code == 202
    run_id = queued.get_json()["run"]["run_id"]

    tool_calls = []
    skill_runner = FakeSkillRunner()
    worker = ScheduledTaskWorker(
        store=store,
        executor=ScheduledTaskExecutor(
            authorizer=lambda _plan, _owner: True,
            tool_runner=lambda name, inputs, runtime_ctx: (
                tool_calls.append((name, inputs, runtime_ctx))
                or {"data": {"price": 1500}}
            ),
            skill_runner=skill_runner,
        ),
        worker_id="e2e-worker",
    )
    outcome = worker.run_once()
    assert outcome["completed"] is True
    assert tool_calls[0][0:2] == ("stock_realtime_quote", {"code": "600519"})
    assert tool_calls[0][2]["owner_user_id"] == "server_user"
    assert skill_runner.calls[0][0:2] == ("report_skill", {"quote": {"price": 1500}})

    completed = client.get(f"/api/schedule-runs/{run_id}")
    assert completed.status_code == 200
    run = completed.get_json()["run"]
    assert run["status"] == "completed"
    assert [item["status"] for item in run["result"]["steps"]] == [
        "completed",
        "completed",
    ]


def test_schedule_mutations_enforce_json_origin_limits_and_no_store():
    service = ScheduledTaskService(
        store=InMemoryScheduledTaskStore(),
        compiler=FakeCompiler(),
    )
    app = Flask("scheduled-task-security")
    app.register_blueprint(
        create_scheduled_task_blueprint(
            service=service,
            identity_resolver=lambda: {"user_id": "server_user"},
        )
    )
    client = app.test_client()

    unsupported = client.post(
        "/api/schedules/preview",
        data="{}",
        content_type="text/plain",
    )
    assert unsupported.status_code == 415
    assert unsupported.get_json()["code"] == "unsupported_media_type"
    assert unsupported.headers["Cache-Control"] == "no-store, private"
    assert unsupported.headers["Vary"] == "Cookie"

    cross_site = client.post(
        "/api/schedules/preview",
        json={"instruction": "每天九点运行"},
        headers={"Origin": "https://attacker.example"},
    )
    assert cross_site.status_code == 403
    assert cross_site.get_json()["code"] == "invalid_origin"

    oversized = client.post(
        "/api/schedules/preview",
        data='{"instruction":"' + ("a" * (129 * 1024)) + '"}',
        content_type="application/json",
    )
    assert oversized.status_code == 413
    assert oversized.get_json()["code"] == "request_too_large"

    key_too_long = client.post(
        "/api/schedules",
        json={"instruction": "创建任务", "draft": draft()},
        headers={"Idempotency-Key": "k" * 129},
    )
    assert key_too_long.status_code == 400
    assert key_too_long.get_json()["code"] == "idempotency_key_too_long"
