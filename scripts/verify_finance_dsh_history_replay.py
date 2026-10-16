#!/usr/bin/env python3
"""Keyless, database-free replay through the actual sdk-minimal DSH process.

Only the model is a loopback SSE fixture. The SDK, session persistence, finance
policy plugin and MCP Skill reader are real. This verifies request scope and
restoration, not model answer quality.
"""
from __future__ import annotations

import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run() -> dict:
    pending = deque()
    requests = []

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if not pending:
                self.send_error(500, "unexpected model request")
                return
            action = pending.popleft()
            delta = {"role": "assistant"}
            finish = "stop"
            if action is None:
                delta["tool_calls"] = [{"index": 0, "id": f"call_{len(requests)}", "type": "function",
                    "function": {"name": "mcp__finance__read_finance_skill", "arguments": '{"skill_ids":[]}'}}]
                finish = "tool_calls"
            else:
                delta["content"] = action
            chunks = [
                {"id": "fixture", "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "fixture", "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            data = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update(LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
                      LLM_API_KEY="keyless-local-fixture", LLM_DEFAULT_MODEL="fixture",
                      FINANCE_DSH_PREWARM_ON_START="0", FINANCE_STATUS_ENABLED="0")
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.session_variable_store_service import SessionVariableStoreService
    native = _load_sdk_class()
    events = []

    class RecordingHarness(native):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            events.extend(result.events)
            return result

    try:
        with tempfile.TemporaryDirectory(prefix="finance-history-replay-") as directory:
            root = Path(directory)
            service = FinanceDeepSeekHarnessSessionService(enabled=True, worker_count=1,
                root_dir=root / "runtime", log_path=root / "run.jsonl", harness_factory=RecordingHarness,
                system_tools=FinanceDataQueryCcTools(result_store=SessionVariableStoreService(data_root=root / "data")))
            answers = []
            try:
                for index, independent in enumerate([False, True, False]):
                    answer = f"fixture-answer-{index}"
                    pending.extend([None, answer])
                    record = service.run_turn(thread_id="same-thread", owner_id="fixture-owner",
                        turn_id=index + 1, user_text=f"fixture-question-{index}",
                        context={"_finance_history_independent": independent})
                    assert not record.get("error"), record.get("error")
                    assert record["result"] == answer, record["result"]
                    answers.append(record["result"])
                assert len(requests) == 6, len(requests)
                for request in requests[2:4]:
                    messages = json.dumps(request["messages"])
                    assert "fixture-question-0" not in messages
                    assert "fixture-answer-0" not in messages
                    assert "fixture-question-1" in messages
                resumed = json.dumps(requests[4]["messages"])
                assert "fixture-answer-0" in resumed and "fixture-answer-1" in resumed
                serialized_events = json.dumps(events, default=str)
                assert "request/history" in serialized_events
                assert "fixture-answer-0" in serialized_events
                return {"passed": True, "model_requests": len(requests), "turns": len(answers),
                    "independent_history_hidden": True, "followup_history_restored": True,
                    "native_selection_logged": True, "paid_model_calls": 0,
                    "restoration_scope": "live session; separate native log replay tests cover reconstruction",
                    "workload": "synthetic questions and empty Skill selection; no financial database"}
            finally:
                service.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
