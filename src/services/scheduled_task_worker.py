from __future__ import annotations

import inspect
import logging
import socket
import threading
import uuid
from typing import Any

from src.services.scheduled_task_executor import ScheduledTaskExecutionError, ScheduledTaskExecutor
from src.services.scheduled_task_protocol import ensure_utc
from src.services.scheduled_task_store import ScheduledTaskStore, default_task_store

logger = logging.getLogger(__name__)


class ScheduledTaskWorker:
    def __init__(self, *, store: ScheduledTaskStore | None = None,
                 executor: ScheduledTaskExecutor | None = None, worker_id="",
                 lease_seconds=3600, heartbeat_seconds=None, stop_event=None):
        self.store=store or default_task_store()
        self.executor=executor or ScheduledTaskExecutor()
        self.worker_id=worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
        self.lease_seconds=max(1,int(lease_seconds))
        self.heartbeat_seconds=max(.02,float(heartbeat_seconds)) if heartbeat_seconds is not None else min(10,max(.1,self.lease_seconds/3))
        self.stop_event=stop_event or threading.Event()

    def run_once(self) -> dict[str,Any]:
        materialized=self.store.materialize_due()
        run=self.store.claim(worker_id=self.worker_id,lease_seconds=self.lease_seconds)
        if not run:
            return {"claimed":False,"materialized":materialized,"worker_id":self.worker_id}
        run_id=run["run_id"]
        claim={"run_id":run_id,"worker_id":self.worker_id,"lease_token":run["lease_token"]}
        stop=threading.Event()
        lost=threading.Event()
        checkpoint=dict(run.get("checkpoint") or {})
        outcome={"claimed":True,"materialized":materialized,"run_id":run_id,"worker_id":self.worker_id}
        def heartbeat():
            while not stop.wait(self.heartbeat_seconds):
                try:
                    if self.store.renew_lease(**claim,lease_seconds=self.lease_seconds):
                        continue
                except Exception:
                    logger.exception("task lease renewal failed: %s",run_id)
                lost.set()
                return
        def control():
            if lost.is_set():
                raise ScheduledTaskExecutionError("运行租约已丢失")
            if self.stop_event.is_set():
                raise ScheduledTaskExecutionError("worker 正在停止；保留检查点供后续恢复")
            current=self.store.get_run_for_owner(run_id=run_id,owner_user_id=run["owner_user_id"])
            if not current or current.get("lease_token") != run["lease_token"] or current["status"] != "running" or current["lease_until"] < ensure_utc(None):
                lost.set()
                raise ScheduledTaskExecutionError("运行租约已丢失")
            if current.get("cancel_requested_at"):
                raise ScheduledTaskExecutionError("用户已取消运行")
            if current.get("deadline_at") and current["deadline_at"] <= ensure_utc(None):
                raise ScheduledTaskExecutionError("已达到任务运行时限")
        def before_step(_):
            control()
            if not self.store.renew_lease(**claim,lease_seconds=self.lease_seconds):
                lost.set()
                raise ScheduledTaskExecutionError("运行租约已丢失")
        def progress(*,progress=None,checkpoint=None,step_id="",result=None):
            control()
            if checkpoint is not None:
                saved_checkpoint[step_id]=checkpoint
            if not self.store.save_progress(**claim,progress=progress,checkpoint=saved_checkpoint,result=result):
                lost.set()
                raise ScheduledTaskExecutionError("运行租约已丢失，检查点未提交")
        saved_checkpoint=checkpoint
        thread=threading.Thread(target=heartbeat,name=f"task-heartbeat-{run_id[-8:]}",daemon=True)
        thread.start()
        try:
            kwargs={"before_step":before_step}
            if "on_progress" in inspect.signature(self.executor.execute).parameters:
                kwargs.update(on_progress=progress,check_control=control)
            result=self.executor.execute(run,**kwargs)
            control()
            if not self.store.finish(**claim,result=result):
                raise ScheduledTaskExecutionError("运行租约已丢失，结果未提交")
            return {**outcome,"completed":True}
        except Exception as exc:
            # Graceful worker shutdown leaves the leased run recoverable. Lease loss
            # cannot publish a result; user cancellation remains durable in the store.
            if not lost.is_set() and not self.stop_event.is_set():
                partial=exc.partial_result if isinstance(exc,ScheduledTaskExecutionError) else None
                self.store.finish(**claim,result=partial,error_text=str(exc))
            logger.warning("task run interrupted: %s: %s",run_id,exc)
            return {**outcome,"completed":False,"error":str(exc)}
        finally:
            stop.set()
            thread.join(timeout=1)
            if self.stop_event.is_set() and not lost.is_set():
                self.store.release(**claim)
