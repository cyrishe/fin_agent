#!/usr/bin/env python3
"""Controlled MCP/DSH regression: decisions, stream recovery and read-only tools.

No paid model calls. A loopback SSE fixture supplies fixed tool calls to the
actual DSH runtime. API auth, SDK lifecycle, MCP bridge, catalog, SQL execution,
result storage and public responses remain real. Run explicitly with a working
database env; this is not a model-quality benchmark.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMPTY_QUERY = (
    'r1 = stock.corporate_action(filter = "(source == \'add_issue\') and '
    '(ai_progress == \'已实施\')", order = "ann_date desc", limit = 20) '
    '-> code, name, ann_date, ai_price, ai_raised_funds, ai_progress'
)
QUOTE_QUERY = 'r1 = stock.quote(codes=["600519.SH"], count=5, mode=0, order="tradedate desc") -> code, name, tradedate, close, pct'


def catalog(view):
    return ("read_finance_catalog", {"subject": "stock", "dataview": view, "operation": "query"})


def query(request, complete=True):
    return ("finance_query", {
        "steps": [{"goal": "按指定条件查询数据", "request": request}],
        "data_request_complete": complete,
    })


def run(args):
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=True)
    os.environ.update(FINANCE_API_ALLOWED_HOSTS="testserver,127.0.0.1:*,localhost:*",
                      FINANCE_API_ALLOWED_ORIGINS="", FINANCE_API_ROOT_PATH="",
                      FINANCE_DSH_PREWARM_ON_START="0", FINANCE_STATUS_ENABLED="0")
    pending = deque()
    requests = []
    wire_requests = []

    class ModelFixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            wire_body = self.rfile.read(int(self.headers["Content-Length"]))
            wire_requests.append(wire_body)
            body = json.loads(wire_body)
            requests.append(body)
            if not pending:
                self.send_error(500, "Unexpected model call in controlled replay")
                return
            action = pending.popleft()
            if isinstance(action, dict) and "http_status" in action:
                self.send_error(action["http_status"], "Controlled provider rejection")
                return
            interrupted = isinstance(action, dict) and "stream_eof" in action
            if interrupted:
                action = action["stream_eof"]
            delta = {"role": "assistant"}
            if isinstance(action, tuple):
                name, arguments = action
                delta["tool_calls"] = [{
                    "index": 0, "id": f"fixture_call_{len(requests)}", "type": "function",
                    "function": {"name": f"mcp__finance__{name}", "arguments": json.dumps(arguments, ensure_ascii=False)},
                }]
                finish = "tool_calls"
            else:
                delta["content"] = action
                finish = "stop"
            chunks = [
                {"id": "fixture", "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "fixture", "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            if interrupted:
                chunks = chunks[:1]
            data = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
            if not interrupted:
                data += "data: [DONE]\n\n"
            encoded = data.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    # Fixture credentials are ephemeral, never read from or written to .env.
    os.environ.update(LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
                      LLM_API_KEY="local-replay-only", LLM_DEFAULT_MODEL="fixture")
    from fastapi.testclient import TestClient
    from src.finance_api.app import create_app
    from src.finance_api.auth import FinanceApiKeyAuth
    from src.finance_api.service import FinanceApiGateway
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    bodies = {}
    namespace = secrets.token_hex(8)
    native = _load_sdk_class()
    try:
        for enabled in (False, True):
            variant = "optimized" if enabled else "baseline"
            directory = output / variant
            directory.mkdir(exist_ok=True)
            captured_events = []

            class RecordingHarness(native):
                def run(self, *positional, **kwargs):
                    result = super().run(*positional, **kwargs)
                    captured_events.append(result.events)
                    return result

            tools = FinanceDataQueryCcTools()
            dsh = FinanceDeepSeekHarnessSessionService(
                enabled=True, system_tools=tools, worker_count=1, root_dir=directory / "runtime",
                log_path=directory / "events.jsonl", harness_factory=RecordingHarness,
                loop_policy_config={"emptyResultEarlyStop": enabled},
            )
            engine = FinancialQaCcService(enabled=True, system_tools=tools,
                                          session_service=object(), dsh_session_service=dsh)
            token = secrets.token_urlsafe(32)
            app = create_app(gateway=FinanceApiGateway(engine=engine),
                             auth=FinanceApiKeyAuth({"controlled-replay": token}))
            report[variant] = {}
            bodies[variant] = {}
            try:
                # Match production: startup warms clients before any caller has
                # granted supplementary tools. The first real turn must refresh
                # the cached MCP definitions, while later turns retain context.
                dsh.prewarm()
                with TestClient(app) as client:
                    unauth = client.post("/mcp", json={"jsonrpc": "2.0", "id": "denied", "method": "tools/list"})
                    assert unauth.status_code == 401
                    scenarios = [
                        ("empty", [catalog("corporate_action"), query(EMPTY_QUERY)], True, "both", "continuity"),
                        ("followup", [catalog("corporate_action"),
                                      query(EMPTY_QUERY.replace("limit = 20", "limit = 5"))], True, "both", "continuity"),
                        ("unfinished", [catalog("corporate_action"), query(EMPTY_QUERY, False),
                                        query(EMPTY_QUERY.replace("r1 =", "r2 =").replace("limit = 20", "limit = 5"))], True, "both", None),
                        ("nonempty", [catalog("quote"), query(QUOTE_QUERY)], False, "both", None),
                        ("partial_tool_recovery", [catalog("quote"), {"stream_eof": query(QUOTE_QUERY)},
                                                   query(QUOTE_QUERY)], False, "both", None),
                        ("partial_text_recovery", [catalog("quote"), query(QUOTE_QUERY),
                                                   {"stream_eof": "Discard this incomplete answer."}], False, "both", None),
                        ("data_only", [catalog("corporate_action"), query(EMPTY_QUERY)], True, "data", None),
                    ]
                    for case_id, actions, empty, mode, conversation in scenarios:
                        requests.clear()
                        wire_requests.clear()
                        pending.clear()
                        pending.extend(actions)
                        # This API supplies a Skill catalog even when the model
                        # chooses no Skill. Keep the synthesis step in both
                        # variants; the policy intentionally protects it.
                        if mode == "both":
                            pending.append("固定回放答案；本项只验证运行协议，不评价模型理解能力。")
                        expected_count = len(pending)
                        arguments = {"query": "按指定条件查询金融数据。", "response_mode": mode,
                                     "runtime": "dsh", "execution_mode": "standard", "detail": True, "max_rows": 5}
                        if conversation:
                            # Explicit conversation IDs intentionally restore
                            # data across runtime instances; variants must not
                            # accidentally share that persisted working set.
                            arguments["conversation_id"] = f"{namespace}-{variant}-{conversation}"
                        result = client.post("/mcp", headers={"X-API-Key": token, "Accept": "application/json, text/event-stream"},
                                             json={"jsonrpc": "2.0", "id": case_id, "method": "tools/call",
                                                   "params": {"name": "finance_data_query", "arguments": arguments}})
                        wire = result.json()
                        payload = wire.get("result", {}).get("structuredContent", {})
                        record = json.loads(dsh.log_path.read_text().splitlines()[-1])
                        saved = {"request": arguments, "response": payload, "record": record,
                                 "model_requests": list(requests), "native_events": captured_events[-1]}
                        (directory / f"{case_id}.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2, default=str))
                        assert result.status_code == 200 and payload.get("ok"), (variant, case_id, payload.get("error"))
                        assert not pending and len(requests) == expected_count, (variant, case_id, "model count")
                        if mode == "both":
                            assert any(tool.get("function", {}).get("name") == "mcp__finance__stock_kline_visual_analysis"
                                       for tool in requests[0]["tools"]), "Prewarm hid the caller-granted visual tool"
                        counts = [ref["row_count"] for ref in record["result_refs"]]
                        assert counts and (all(count == 0 for count in counts) if empty else counts == [5]), (case_id, counts)
                        assert record["loop_policy"]["config"]["emptyResultEarlyStop"] is enabled
                        assert record["empty_result_early_stop"] is False
                        assert bool(payload.get("summary")) == (mode == "both")
                        if case_id.endswith("_recovery"):
                            retries = [e for e in captured_events[-1] if e.get("type") == "llm/retry"]
                            assert len(retries) == 1
                            assert retries[0]["data"]["failure"]["code"] == "STREAM_CLOSED"
                            failed_index = 1 if case_id == "partial_tool_recovery" else 2
                            assert wire_requests[failed_index] == wire_requests[failed_index + 1]
                            assert sum(c.get("tool") == "finance_query" for c in record["tool_calls"]) == 1
                            assert "Discard this incomplete answer" not in (payload.get("summary") or "")
                        if case_id == "followup":
                            assert record["resumed"]
                            first_context = json.dumps(requests[0]["messages"], ensure_ascii=False)
                            assert "固定回放答案" in first_context
                        else:
                            assert not record["resumed"]
                            if case_id != "empty":
                                assert "固定回放答案" not in json.dumps(requests[0]["messages"], ensure_ascii=False)
                        report[variant][case_id] = {"calls": len(requests), "rows": counts,
                                                    "summary": payload.get("summary"), "early_stop": record["empty_result_early_stop"],
                                                    "samples": [ref.get("sample") for ref in record["result_refs"]],
                                                    "request_sha256": [hashlib.sha256(body).hexdigest() for body in wire_requests]}
                        bodies[variant][case_id] = list(wire_requests)
                        print(json.dumps({"variant": variant, "case": case_id, "calls": len(requests),
                                          "rows": counts, "early_stop": record["empty_result_early_stop"]}), flush=True)
                    for case_id, failures in (
                        ("exhausted_stream", [{"stream_eof": query(QUOTE_QUERY)}] * 3),
                        ("auth_rejection", [{"http_status": 401}]),
                    ):
                        requests.clear()
                        wire_requests.clear()
                        pending.clear()
                        pending.extend(failures)
                        result = client.post("/mcp", headers={"X-API-Key": token, "Accept": "application/json, text/event-stream"},
                            json={"jsonrpc": "2.0", "id": case_id, "method": "tools/call",
                                  "params": {"name": "finance_data_query", "arguments": {
                                      "query": "查询贵州茅台最近行情", "runtime": "dsh", "detail": True}}})
                        payload = result.json().get("result", {}).get("structuredContent", {})
                        record = json.loads(dsh.log_path.read_text().splitlines()[-1])
                        assert result.status_code == 200 and payload.get("ok") is False
                        assert len(requests) == len(failures) and not pending
                        assert not record["tool_calls"] and not record["result_refs"]
                        retries = [e for e in captured_events[-1] if e.get("type") == "llm/retry"]
                        assert len(retries) == len(failures) - 1
                        assert all(body == wire_requests[0] for body in wire_requests)
                        (directory / f"{case_id}.json").write_text(json.dumps({
                            "response": payload, "native_events": captured_events[-1],
                            "model_requests": requests}, ensure_ascii=False, indent=2))
                        print(json.dumps({"variant": variant, "case": case_id,
                                          "requests": len(requests), "retries": len(retries),
                                          "tool_calls": 0, "rejected": True}), flush=True)
            finally:
                dsh.close()
        # The two first model requests must be literally identical, including
        # full tool descriptions and the exact operation execution catalog.
        assert bodies["baseline"]["empty"][:2] == bodies["optimized"]["empty"][:2]
        for case_id in report["baseline"]:
            assert report["baseline"][case_id]["rows"] == report["optimized"][case_id]["rows"]
            assert report["baseline"][case_id]["summary"] == report["optimized"][case_id]["summary"]
            assert report["baseline"][case_id]["samples"] == report["optimized"][case_id]["samples"]
        report["verification"] = {"first_two_requests_byte_equivalent": True, "all_rows_and_summaries_equal": True,
                                  "skill_catalog_retains_final_model_step": True,
                                  "unauthenticated_rejected": True,
                                  "interrupted_requests_recovered_without_duplicate_tools": True,
                                  "retry_exhaustion_and_auth_rejection_verified": True,
                                  "prewarm_retains_caller_granted_tools": True,
                                  "note": "Fixed model decisions + real MCP/DSH/database. Not a model-quality or billing benchmark."}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/finance_empty_result_replay_20260908")
    run(parser.parse_args())
