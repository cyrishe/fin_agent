from __future__ import annotations

import logging
import socket
import threading
import uuid
from typing import Any, Dict

from src.services.scheduled_task_executor import (
    ScheduledTaskExecutionError,
    ScheduledTaskExecutor,
)
from src.services.scheduled_task_store import MySqlScheduledTaskStore, ScheduledTaskStore


logger = logging.getLogger(__name__)


class ScheduledTaskWorker:
    def __init__(
        self,
        *,
        store: ScheduledTaskStore | None = None,
        executor: ScheduledTaskExecutor | None = None,
        worker_id: str = "",
        lease_seconds: int = 3600,
        heartbeat_seconds: float | None = None,
    ) -> None:
        self.store = store or MySqlScheduledTaskStore()
        self.executor = executor or ScheduledTaskExecutor()
        self.worker_id = (
            str(worker_id or "").strip()
            or f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
        )
        self.lease_seconds = max(30, int(lease_seconds))
        self.heartbeat_seconds = (
            max(0.1, float(heartbeat_seconds))
            if heartbeat_seconds is not None
            else max(5.0, self.lease_seconds / 3)
        )

    def run_once(self) -> Dict[str, Any]:
        materialized = self.store.materialize_due()
        run = self.store.claim(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if not run:
            return {
                "claimed": False,
                "materialized": materialized,
                "worker_id": self.worker_id,
            }
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            kwargs={
                "run_id": run["run_id"],
                "stop": heartbeat_stop,
                "lease_lost": lease_lost,
            },
            name=f"schedule-heartbeat-{str(run['run_id'])[-12:]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = self.executor.execute(
                run,
                before_step=lambda _step_id: self._renew_or_raise(
                    run["run_id"],
                    lease_lost=lease_lost,
                ),
            )
            heartbeat_stop.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds * 2))
            if lease_lost.is_set():
                raise ScheduledTaskExecutionError("运行租约已丢失，停止写回结果")
            if not self.store.finish(
                run_id=run["run_id"],
                worker_id=self.worker_id,
                result=result,
            ):
                raise RuntimeError("运行完成，但 worker 已失去写回租约")
            return {
                "claimed": True,
                "completed": True,
                "materialized": materialized,
                "run_id": run["run_id"],
                "worker_id": self.worker_id,
            }
        except ScheduledTaskExecutionError as exc:
            self.store.finish(
                run_id=run["run_id"],
                worker_id=self.worker_id,
                result=exc.partial_result,
                error_text=str(exc),
            )
            logger.exception("scheduled task run failed: %s", run["run_id"])
            return {
                "claimed": True,
                "completed": False,
                "materialized": materialized,
                "run_id": run["run_id"],
                "error": str(exc),
                "worker_id": self.worker_id,
            }
        except Exception as exc:
            self.store.finish(
                run_id=run["run_id"],
                worker_id=self.worker_id,
                error_text=str(exc),
            )
            logger.exception("scheduled task worker failed: %s", run["run_id"])
            return {
                "claimed": True,
                "completed": False,
                "materialized": materialized,
                "run_id": run["run_id"],
                "error": str(exc),
                "worker_id": self.worker_id,
            }
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds * 2))

    def _heartbeat(
        self,
        *,
        run_id: str,
        stop: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                renewed = self.store.renew_lease(
                    run_id=run_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                logger.exception("scheduled task lease heartbeat failed: %s", run_id)
                renewed = False
            if not renewed:
                lease_lost.set()
                return

    def _renew_or_raise(
        self,
        run_id: str,
        *,
        lease_lost: threading.Event | None = None,
    ) -> None:
        if lease_lost is not None and lease_lost.is_set():
            raise ScheduledTaskExecutionError("运行租约已丢失，停止继续执行")
        if not self.store.renew_lease(
            run_id=run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        ):
            raise ScheduledTaskExecutionError("运行租约已丢失，停止继续执行")
