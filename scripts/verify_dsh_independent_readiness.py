"""Reproduce DSH lifecycle/registry gaps offline, without an LLM or database.

Run from the repository root with .venv/bin/python. Only temporary fixture
files are created. The SDK probe instantiates Session/NotificationSubscription,
not DeepSeekHarness or a subprocess. Execution probes replace final providers
or Harness with deterministic fakes and retain the real Fin Agent adapters.
Output reports observations; an issue disappearing after a fix is not a script
failure. Every thread is explicitly released and joined.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.experiments.staged_data_protocol.phase2 import api_runner
from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge
from src.scenarios.financial_qa.dsh_service import (
    FinanceDeepSeekHarnessSessionService, _atomic_write, _load_sdk_class,
)
from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.services.session_variable_store_service import SessionVariableStoreService


def sdk_turn_deadline() -> dict:
    sdk_class = _load_sdk_class()
    from deepseek_harness.api import Session
    from deepseek_harness.client import NotificationSubscription
    from deepseek_harness.models import Notification

    notifications = queue.Queue()
    session_id = "offline-deadline-audit"

    class FakeClient:
        config = SimpleNamespace(request_timeout_seconds=0.05)

        def subscribe_session_notifications(self, sid):
            return NotificationSubscription(self, "audit", notifications)

        def session_prompt(self, sid, content, **kwargs):
            notifications.put(Notification("session.event", {
                "sessionId": sid,
                "event": {"type": "agent/inbox/spliced", "data": {
                    "inserted": [{"id": "message-audit"}],
                }},
            }))
            return "message-audit"

        def _unsubscribe_notifications(self, sid):
            pass

    harness = SimpleNamespace(
        client=FakeClient(), config=SimpleNamespace(request_timeout_seconds=0.05),
    )
    finished = threading.Event()
    errors = []

    def run():
        try:
            Session(harness, session_id).run("offline only")
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    worker.start()
    try:
        exited_before_idle = finished.wait(timeout=0.15)
        waited_ms = round((time.monotonic() - started) * 1000, 2)
    finally:
        notifications.put(Notification("session.status", {
            "sessionId": session_id, "status": "idle",
        }))
        worker.join(timeout=1)
    assert not worker.is_alive(), "offline SDK probe thread was not reclaimed"
    return {
        "sdk_path": inspect.getfile(sdk_class),
        "configured_request_timeout_ms": 50,
        "waited_ms": waited_ms,
        "exited_before_idle": exited_before_idle,
        "reclaimed_after_explicit_idle": True,
        "errors": errors,
    }


def parallel_result_names() -> dict:
    barrier = threading.Barrier(2)

    def fake_provider(**kwargs):
        try:
            barrier.wait(timeout=0.25)
        except threading.BrokenBarrierError:
            # A future per-session serialization fix may keep the second call
            # outside the provider until the first finishes. Do not deadlock it.
            pass
        codes = kwargs["args"]["codes"]
        code = (ast.literal_eval(codes) if isinstance(codes, str) else codes)[0]
        return {
            "status": "ok", "columns": ["code", "close"],
            "rows": [{"code": code, "close": 100.0}], "row_count": 1,
        }

    with tempfile.TemporaryDirectory(prefix="dsh_parallel_audit_") as temporary:
        root = Path(temporary)
        _atomic_write(root / "context.json", {
            "revision": "offline-parallel", "owner_ids": ["offline-audit"],
            "tool_context": {"_agent_runtime_scope": "dsh:offline-parallel"},
        })
        service = FinanceDataQueryCcTools(
            finance_runtime=FinanceDataToolRuntimeService(),
            result_store=SessionVariableStoreService(data_root=root / "results"),
        )
        bridge = FinanceDshMcpBridge(
            context_path=root / "context.json", trace_path=root / "trace.json", system_tools=service,
        )
        arguments = [{"steps": [{
            "goal": f"读取行情 {code}",
            "request": f'result = stock.quote(codes=["{code}"],count=1,mode=0) -> code, close',
        }]} for code in ["600519.SH", "300750.SZ"]]

        async def run():
            return await asyncio.gather(*(
                bridge.call_tool("finance_query", item) for item in arguments
            ))

        with patch.object(api_runner, "execute_quote_api", fake_provider):
            results = asyncio.run(run())
        observation = {
            "returned": [{
                "ok": result.get("ok"), "result_name": result.get("result_name"),
                "sample": result.get("sample"),
            } for result in results],
            "working_set_names": list(bridge.tool_runtime.result_handles),
            "stored_variables": len(service.result_store.list_variables(
                session_id=bridge.tool_runtime.result_scope,
            )),
        }
        _atomic_write(root / "context.json", {
            "revision": "offline-other-owner", "owner_ids": ["other-owner"],
            "tool_context": {"_agent_runtime_scope": "dsh:offline-other-owner"},
        })
        denied = asyncio.run(bridge.call_tool("load_finance_result", {
            "result_ref": results[0].get("result_ref", ""),
        }))
        observation["foreign_scope_rejected"] = "does not belong" in str(denied.get("error", ""))
        return observation


def terminal_error_fidelity() -> list[dict]:
    class Catalog:
        def catalog_revision(self):
            return "offline-catalog"

    class Runtime:
        def begin_turn(self, **kwargs):
            return {}

        def current_context_prompt(self):
            return ""

    class Tools:
        finance_catalog = Catalog()

        def create_runtime(self):
            return Runtime()

    bad = {"tool": "finance_query", "goal": "目标 A", "validation_errors": ["unknown output: invalid_field"]}
    good = {"tool": "finance_query", "goal": "目标 B", "result_name": "r1", "row_count": 3}
    observations = []
    for scenario in ["failure_then_unrelated_success", "success_then_failure", "timeout_after_success"]:
        class Harness:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def run(self, prompt, **kwargs):
                env = self.kwargs["env"]
                context = json.loads(Path(env["FIN_AGENT_DSH_CONTEXT_PATH"]).read_text())
                calls = ([bad, good] if scenario == "failure_then_unrelated_success"
                         else [good, bad] if scenario == "success_then_failure" else [good])
                _atomic_write(Path(env["FIN_AGENT_DSH_TRACE_PATH"]), {
                    "revision": context["revision"],
                    "tracker": {
                        "calls": calls,
                        "result_refs": [{"api": "stock.quote", "result_name": "r1", "row_count": 3}],
                        "data_only_complete": True,
                    },
                })
                if scenario == "timeout_after_success":
                    raise TimeoutError("simulated transport timeout after query completed")
                return SimpleNamespace(final_response="", finish_reason="completed", events=[])

            def close(self):
                pass

        with tempfile.TemporaryDirectory(prefix="dsh_error_audit_") as temporary:
            root = Path(temporary)
            service = FinanceDeepSeekHarnessSessionService(
                enabled=True, system_tools=Tools(), root_dir=root / "runtime",
                log_path=root / "events.jsonl", worker_count=1, harness_factory=Harness,
            )
            try:
                result = service.run_turn(
                    thread_id="offline", owner_id="audit", user_text="查询目标 A 和目标 B",
                    context={"_finance_data_only": True},
                )
                worker = service._workers[0]
                trace = json.loads(worker.trace_path.read_text())
                observations.append({
                    "scenario": scenario, "error": result["error"],
                    "data_only_complete": result.get("data_only_complete"),
                    "returned_calls": len(result["tool_calls"]),
                    "returned_refs": len(result["result_refs"]),
                    "on_disk_trace_calls": len(trace["tracker"]["calls"]),
                    "on_disk_trace_refs": len(trace["tracker"]["result_refs"]),
                    "worker_invalidated": worker.harness is None,
                })
            finally:
                service.close()
    return observations


if __name__ == "__main__":
    print(json.dumps({
        "scope": "offline; no model, database, subprocess or network calls",
        "sdk_turn_deadline": sdk_turn_deadline(),
        "parallel_result_names": parallel_result_names(),
        "terminal_error_fidelity": terminal_error_fidelity(),
    }, ensure_ascii=False, indent=2))
