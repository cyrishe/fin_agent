from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path

import pytest
from flask import Flask

from src.services.scheduled_task_executor import ScheduledTaskExecutor, ScheduledTaskExecutionError
from src.services.scheduled_task_protocol import normalize_schedule_draft, ScheduledTaskProtocolError
from src.services.scheduled_task_service import ScheduledTaskService
from src.services.scheduled_task_store import SqliteScheduledTaskStore, InMemoryScheduledTaskStore, TaskIdempotencyConflict
from src.services.scheduled_task_worker import ScheduledTaskWorker
from src.web.scheduled_task_routes import create_scheduled_task_blueprint

UTC=dt.timezone.utc
NOW=dt.datetime(2026,10,3,tzinfo=UTC)


def draft(trigger=None, steps=None, budget=None):
    return {"requirement_brief":"生成研究并复核", "trigger":trigger or {}, "budget":budget or {"max_runtime_seconds":3600},
            "execution_plan":{"steps":steps or [{"step_id":"one","type":"tool","target_ref":{"kind":"tool","name":"demo"},"inputs":{},"depends_on":[]}]}}


class Compiler:
    def compile(self, *, instruction, owner_user_id, draft, now):
        return normalize_schedule_draft(draft,now=now)


def test_immediate_creation_is_atomic_idempotent_and_durable(tmp_path):
    path=tmp_path/"queue.sqlite"
    service=ScheduledTaskService(store=SqliteScheduledTaskStore(path),compiler=Compiler())
    first=service.create(owner_user_id="a",instruction="test",draft=draft(),idempotency_key="one",now=NOW)
    again=service.create(owner_user_id="a",instruction="test",draft=draft(),idempotency_key="one",now=NOW+dt.timedelta(hours=2))
    assert first["initial_run_id"]==again["initial_run_id"]
    reopened=SqliteScheduledTaskStore(path)
    assert len(reopened.list_runs_for_owner(owner_user_id="a"))==1
    assert reopened.get_run_for_owner(run_id=first["initial_run_id"],owner_user_id="b") is None
    with pytest.raises(TaskIdempotencyConflict):
        service.create(owner_user_id="a",instruction="changed",draft=draft(),idempotency_key="one",now=NOW)


def test_once_is_not_claimed_early_and_never_materialized_twice():
    store=InMemoryScheduledTaskStore()
    future=NOW+dt.timedelta(hours=1)
    item=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft({"at":future.isoformat()}),now=NOW))
    assert item["next_run_at"] is None
    assert store.claim(worker_id="w",now=NOW) is None
    assert store.materialize_due(now=future)==0
    run=store.claim(worker_id="w",now=future)
    assert run["run_id"]==item["initial_run_id"]
    assert store.materialize_due(now=future+dt.timedelta(days=20))==0


def test_lease_token_fences_same_worker_after_expiry_and_preserves_deadline():
    store=InMemoryScheduledTaskStore()
    task=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft(),now=NOW))
    first=store.claim(worker_id="same",now=NOW,lease_seconds=2)
    second=store.claim(worker_id="same",now=NOW+dt.timedelta(seconds=3),lease_seconds=20)
    assert first["lease_token"]!=second["lease_token"]
    assert first["deadline_at"]==second["deadline_at"]
    old={"run_id":first["run_id"],"worker_id":"same","lease_token":first["lease_token"],"now":NOW+dt.timedelta(seconds=3)}
    assert not store.save_progress(**old,result={"bad":True})
    assert not store.finish(**old,result={"bad":True})
    assert not store.renew_lease(**old)
    assert not store.finish(run_id=first["run_id"],worker_id="same",now=NOW+dt.timedelta(seconds=3))


def test_periodic_runs_coalesce_and_do_not_overlap():
    store=InMemoryScheduledTaskStore()
    item=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft({"cron":"* * * * *"}),now=NOW))
    assert store.materialize_due(now=NOW+dt.timedelta(minutes=1))==1
    assert store.materialize_due(now=NOW+dt.timedelta(hours=2))==0
    assert len(store.runs)==1
    run=store.claim(worker_id="w",now=NOW+dt.timedelta(hours=2))
    assert store.finish(run_id=run["run_id"],worker_id="w",lease_token=run["lease_token"],now=NOW+dt.timedelta(hours=2))
    assert store.materialize_due(now=NOW+dt.timedelta(hours=2))==1
    assert store.materialize_due(now=NOW+dt.timedelta(hours=2))==0


def test_step_checkpoint_skips_completed_steps_after_process_restart(tmp_path):
    store=SqliteScheduledTaskStore(tmp_path/"queue.sqlite")
    steps=draft()["execution_plan"]["steps"]+[{"step_id":"two","type":"tool","target_ref":{"kind":"tool","name":"next"},"inputs":{"value":{"$from":"one.result.value"}},"depends_on":["one"]}]
    item=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft(steps=steps)))
    run=store.claim(worker_id="dead",lease_seconds=1)
    partial={"steps":[{"step_id":"one","status":"completed","result":{"value":42}}],"outputs":{"one":{"result":{"value":42}}}}
    assert store.save_progress(run_id=run["run_id"],worker_id="dead",lease_token=run["lease_token"],result=partial)
    recovered=store.claim(worker_id="new",now=dt.datetime.now(UTC)+dt.timedelta(seconds=2))
    calls=[]
    executor=ScheduledTaskExecutor(authorizer=lambda *_:None,tool_runner=lambda name,args,**_:calls.append((name,args)) or {"ok":True})
    result=executor.execute(recovered)
    assert calls==[("next",{"value":42})]
    assert len(result["steps"])==2


def test_explicit_tool_failure_blocks_dependents_but_business_rejection_completes():
    run={"run_id":"test","owner_user_id":"a","execution_plan":normalize_schedule_draft(draft())["execution_plan"]}
    executor=ScheduledTaskExecutor(authorizer=lambda *_:None,tool_runner=lambda *_args,**_: {"ok":False,"error":"failed source"})
    with pytest.raises(ScheduledTaskExecutionError,match="failed source"):
        executor.execute(run)
    executor.tool_runner=lambda *_args,**_: {"ok":True,"qualified":False}
    assert executor.execute(run)["steps"][0]["status"]=="completed"


def test_cancel_pending_is_owner_scoped():
    store=InMemoryScheduledTaskStore()
    task=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft()))
    rid=task["initial_run_id"]
    assert store.cancel(run_id=rid,owner_user_id="b") is None
    assert store.cancel(run_id=rid,owner_user_id="a")["status"]=="cancelled"
    assert store.claim(worker_id="w") is None


def _slow_child(connection, _type, _name, inputs, _ctx, _parent):
    import os
    if hasattr(os,"setsid"):
        os.setsid()
    Path(inputs["started"]).write_text(str(os.getpid()))
    connection.send(("progress", {"progress":{"message":"working"},"checkpoint":{"completed":1}}))
    time.sleep(20)
    Path(inputs["finished"]).write_text("should not exist")


@pytest.mark.parametrize("action",["cancel","deadline","lease_loss"])
def test_supervised_process_stops_for_control_changes(tmp_path,monkeypatch,action):
    import src.services.scheduled_task_executor as executor_module
    monkeypatch.setattr(executor_module,"_child_step",_slow_child)
    monkeypatch.setenv("TASK_ARTIFACT_ROOT",str(tmp_path/"artifacts"))
    store=SqliteScheduledTaskStore(tmp_path/"queue.sqlite")
    body=draft(budget={"max_runtime_seconds":2 if action=="deadline" else 60})
    body["execution_plan"]["steps"][0]["inputs"]={"started":str(tmp_path/"started"),"finished":str(tmp_path/"finished")}
    item=store.create(owner_user_id="a",draft=normalize_schedule_draft(body))
    executor=ScheduledTaskExecutor(authorizer=lambda *_:None)
    worker=ScheduledTaskWorker(store=store,executor=executor,heartbeat_seconds=.1,lease_seconds=5)
    output=[]
    thread=threading.Thread(target=lambda:output.append(worker.run_once()))
    thread.start()
    until=time.monotonic()+8
    while not (tmp_path/"started").exists() and time.monotonic()<until:
        time.sleep(.05)
    assert (tmp_path/"started").exists()
    if action=="cancel":
        store.cancel(run_id=item["initial_run_id"],owner_user_id="a")
    elif action=="lease_loss":
        with store._transaction() as cursor:
            cursor.execute("UPDATE aiia_scheduled_task_run SET lease_token='another' WHERE run_id=%s",(item["initial_run_id"],))
    thread.join(8)
    assert not thread.is_alive()
    assert not (tmp_path/"finished").exists()
    run=store.get_run_for_owner(run_id=item["initial_run_id"],owner_user_id="a")
    assert run["status"]=={"cancel":"cancelled","deadline":"failed","lease_loss":"running"}[action]
    assert not output[0]["completed"]


def test_task_api_and_owner_checked_artifact_download(tmp_path,monkeypatch):
    monkeypatch.setenv("TASK_ARTIFACT_ROOT",str(tmp_path/"artifacts"))
    store=InMemoryScheduledTaskStore()
    service=ScheduledTaskService(store=store,compiler=Compiler())
    identity={"user_id":"a"}
    app=Flask(__name__)
    app.register_blueprint(create_scheduled_task_blueprint(service=service,identity_resolver=lambda:identity))
    client=app.test_client()
    payload={"instruction":"report","draft":draft()}
    created=client.post("/api/task-definitions",json=payload,headers={"Idempotency-Key":"key"})
    assert created.status_code==201
    task=created.json["task"]
    assert client.post("/api/task-definitions",json={**payload,"instruction":"different"},headers={"Idempotency-Key":"key"}).status_code==409
    run=store.claim(worker_id="w")
    root=tmp_path/"artifacts"/run["run_id"]/"one"
    root.mkdir(parents=True)
    (root/"report.md").write_text("verified report")
    outside=tmp_path/"secret.txt"
    outside.write_text("private")
    result={"steps":[{"step_id":"one","status":"completed","result":{"summary":"Report ready", "artifacts":[{"path":str(root/"report.md"),"name":"report.md"},{"path":str(outside),"name":"bad"}]}}]}
    store.finish(run_id=run["run_id"],worker_id="w",lease_token=run["lease_token"],result=result)
    public=client.get(f"/api/task-runs/{run['run_id']}").json["run"]
    assert "lease_token" not in public and "checkpoint" not in public
    assert len(public["artifacts"])==1
    url=public["artifacts"][0]["url"]
    assert client.get(url).data==b"verified report"
    identity["user_id"]="b"
    assert client.get(url).status_code==404
    assert client.post(f"/api/task-runs/{run['run_id']}/cancel",json={}).status_code==404


def test_protocol_rejects_conflicting_trigger_and_invalid_budget():
    with pytest.raises(ScheduledTaskProtocolError):
        normalize_schedule_draft(draft({"cron":"* * * * *","at":NOW.isoformat()}))
    with pytest.raises(ScheduledTaskProtocolError):
        normalize_schedule_draft(draft(budget={"max_runtime_seconds":0}))


def test_manual_run_idempotency_remains_after_completion():
    store=InMemoryScheduledTaskStore()
    task=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft({"cron":"0 0 * * *"})))
    first=store.enqueue_manual(schedule_id=task["task_id"],owner_user_id="a",idempotency_key="click")
    run=store.claim(worker_id="w")
    store.finish(run_id=run["run_id"],worker_id="w",lease_token=run["lease_token"])
    second=store.enqueue_manual(schedule_id=task["task_id"],owner_user_id="a",idempotency_key="click")
    assert first["run_id"]==second["run_id"]


def test_two_persistent_workers_cannot_claim_same_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/"queue.sqlite"
    store=SqliteScheduledTaskStore(path)
    task=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft()))
    gate=threading.Barrier(2)
    def claim(index):
        connection=SqliteScheduledTaskStore(path)
        gate.wait()
        return connection.claim(worker_id=f"w{index}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs=list(pool.map(claim,[1,2]))
    assert len([run for run in runs if run])==1


def test_concurrent_duplicate_create_has_one_task_and_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/"queue.sqlite"
    SqliteScheduledTaskStore(path)
    gate=threading.Barrier(2)
    def create(_):
        service=ScheduledTaskService(store=SqliteScheduledTaskStore(path),compiler=Compiler())
        gate.wait()
        return service.create(owner_user_id="a",instruction="same",draft=draft(),idempotency_key="same")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks=list(pool.map(create,[1,2]))
    assert tasks[0]["task_id"]==tasks[1]["task_id"]
    assert len(SqliteScheduledTaskStore(path).list_runs_for_owner(owner_user_id="a"))==1


def test_revoked_asset_blocks_later_step():
    steps=draft()["execution_plan"]["steps"]+[{"step_id":"two","type":"tool","target_ref":{"kind":"tool","name":"revoked"},"inputs":{},"depends_on":["one"]}]
    allowed={"demo","revoked"}
    def authorize(plan,owner):
        for step in plan["steps"]:
            if step["target_ref"]["name"] not in allowed:
                raise RuntimeError("asset revoked")
    def execute(name,*_,**__):
        allowed.remove("revoked")
        return {"ok":True}
    executor=ScheduledTaskExecutor(authorizer=authorize,tool_runner=execute)
    with pytest.raises(ScheduledTaskExecutionError,match="asset revoked") as failure:
        executor.execute({"run_id":"test","owner_user_id":"a","execution_plan":normalize_schedule_draft(draft(steps=steps))["execution_plan"]})
    assert failure.value.partial_result["steps"][0]["status"]=="completed"


def test_manual_run_can_bring_forward_future_oneoff_without_duplicate():
    store=InMemoryScheduledTaskStore()
    task=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft({"at":(NOW+dt.timedelta(days=1)).isoformat()}),now=NOW))
    run=store.enqueue_manual(owner_user_id="a",schedule_id=task["task_id"],now=NOW)
    assert run["scheduled_for"]==NOW
    assert run["run_id"]==task["initial_run_id"]
    assert len(store.runs)==1


def test_worker_release_preserves_completed_checkpoints_and_allows_immediate_recovery():
    store=InMemoryScheduledTaskStore()
    task=store.create(owner_user_id="a",draft=normalize_schedule_draft(draft(),now=NOW))
    run=store.claim(worker_id="w",now=NOW)
    claim={"run_id":run["run_id"],"worker_id":"w","lease_token":run["lease_token"],"now":NOW}
    assert store.save_progress(**claim,checkpoint={"one":{"trial":4}},result={"steps":[]})
    assert store.release(**claim)
    recovered=store.claim(worker_id="new",now=NOW)
    assert recovered["checkpoint"]=={"one":{"trial":4}}
    assert recovered["deadline_at"]==run["deadline_at"]


def test_resume_uses_isolated_attempt_and_keeps_progress_on_checkpoint(tmp_path,monkeypatch):
    monkeypatch.setenv("TASK_ARTIFACT_ROOT",str(tmp_path))
    previous=tmp_path/"run_test"/"one"/"oldclaim"
    (previous/"research").mkdir(parents=True)
    (previous/"research"/"trial.json").write_text('{"score":0.4}')
    observed=[]
    def tool(_name,_inputs,*,runtime_ctx):
        context=runtime_ctx
        destination=Path(context["task_output_dir"])
        assert destination.name=="newclaim"
        assert context["task_checkpoint"]["research_dir"]==str(destination/"research")
        assert (destination/"research"/"trial.json").read_text()=='{"score":0.4}'
        context["task_progress"]({"message":"完成实验 1","stage":"training"})
        context["task_save_checkpoint"]({"experiment":1})
        return {"ok":True}
    executor=ScheduledTaskExecutor(authorizer=lambda *_:None,tool_runner=tool)
    run={"run_id":"run_test","owner_user_id":"a","lease_token":"newclaim","execution_plan":normalize_schedule_draft(draft())["execution_plan"],
         "checkpoint":{"one":{"_attempt_dir":str(previous),"research_dir":str(previous/"research")}}}
    executor.execute(run,on_progress=lambda **event:observed.append(event))
    checkpoint_event=next(event for event in observed if (event.get("checkpoint") or {}).get("experiment")==1)
    assert checkpoint_event["progress"]["message"]=="完成实验 1"
    assert checkpoint_event["progress"]["stage"]=="training"
    assert (previous/"research"/"trial.json").is_file()


def _child_with_descendant(connection, _type, _name, inputs, _ctx, _parent):
    import os
    import subprocess
    import sys
    os.setsid()
    subprocess.Popen([sys.executable,"-c","import pathlib,time,sys; time.sleep(1.5); pathlib.Path(sys.argv[1]).write_text('orphan')",inputs["marker"]])
    connection.send(("result",{"ok":True}))
    connection.close()


def test_completed_step_does_not_leave_descendant_processes(tmp_path,monkeypatch):
    import src.services.scheduled_task_executor as executor_module
    monkeypatch.setattr(executor_module,"_child_step",_child_with_descendant)
    marker=tmp_path/"orphan.txt"
    # Let the leader exit before the supervisor consumes its result.
    def slow_control():
        time.sleep(.3)
    result=executor_module._supervised_step("tool","test",{"marker":str(marker)},{},lambda *_args,**_kwargs:None,slow_control)
    assert result["ok"]
    time.sleep(1.7)
    assert not marker.exists()


def test_mysql_readiness_does_not_ignore_nullable_trigger_or_request_unique_index():
    from scripts.manage_scheduled_task_schema import _schema_status, REQUIRED_COLUMNS
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def execute(self,sql,args=()): self.sql,self.args=sql,args
        def fetchone(self): return {"database_name":"isolated_fixture"}
        def fetchall(self):
            if "INFORMATION_SCHEMA.STATISTICS" in self.sql:
                return []
            return [{"COLUMN_NAME":name,"IS_NULLABLE":"NO" if name=="cron_expr" else "YES"} for name in REQUIRED_COLUMNS[self.args[1]]]
    class Connection:
        def cursor(self): return Cursor()
    missing=_schema_status(Connection())
    assert "cron_expr must allow NULL" in missing["aiia_scheduled_task"]
    assert "uk_task_run_request unique index" in missing["aiia_scheduled_task_run"]
