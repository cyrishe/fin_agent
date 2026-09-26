#!/usr/bin/env python3
"""Synthetic saved-result reading through real DSH, MCP, storage and policy.

Default: keyless deterministic SSE model, including parallel wide-table pages.
--live: actual configured model makes decisions about the same synthetic data.
No financial database is queried. All runtime data lives in a temporary folder;
only sanitized coverage, tool choices, usage and answers enter the report.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sample_rows():
    return [{
        "code": f"fixture_{i:03d}", "name": f"合成公司{i:03d}",
        "rating": "买入" if i < 100 else "中性",
        "highlights": f"公司{i:03d}收入增长，订单稳定。",
        "risk": "客户集中度上升。" if i == 137 else "无新增风险。",
        "raw_text": "这段是无关背景原文。" * (2400 if i == 189 else 400) + ("尾证据：库存减值731万元。" if i == 189 else "背景结束。"),
    } for i in range(190)]


def system_tools(data_root):
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.session_variable_store_service import SessionVariableStoreService

    class FixtureCatalog:
        def catalog_revision(self):
            return "synthetic-reports-v1"

        def get_model_dataview(self, subject, dataview, operation=""):
            assert (subject, dataview) == ("fixture", "reports")
            return {"name": "reports", "desc": "合成研报表", "fields": [
                {"name": name, "type": "string", "desc": name} for name in sample_rows()[0]],
                "functions": [{"api_name": "fixture.reports.query", "operation": "query",
                    "description": "读取固定190条合成研报的完整快照，无参数。",
                    "request_pattern": "r1 = fixture.reports.query() -> " + ", ".join(sample_rows()[0])}]}

        def get_dataview(self, subject, dataview):
            return self.get_model_dataview(subject, dataview)

        def get_subject(self, subject):
            assert subject == "fixture"
            return {"name": "fixture", "desc": "合成验收数据", "dataviews": [self.get_dataview(subject, "reports")]}

        def build_tree(self):
            return {"subjects": [self.get_subject("fixture")]}

    class FixtureRuntime:
        def execute_request(self, *, request, previous_results=None):
            if "fixture.reports.query()" not in request:
                return {"validation": {"ok": False}, "error": "Fixture only supports fixture.reports.query()"}
            rows = sample_rows()
            return {"protocol": "finance_data_tool.v1", "request": request, "validation": {"ok": True},
                "result": {"name": request.split("=", 1)[0].strip(), "api": "fixture.reports.query",
                    "columns": list(rows[0]), "data": {"rows": rows, "row_count": len(rows)}}}

    return FinanceDataQueryCcTools(finance_runtime=FixtureRuntime(), finance_catalog=FixtureCatalog(),
        result_store=SessionVariableStoreService(data_root=data_root))


async def serve_mcp(data_root):
    from mcp.server.lowlevel import NotificationOptions
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
    from src.scenarios.financial_qa.dsh_mcp_server import FinanceDshMcpBridge, create_server

    bridge = FinanceDshMcpBridge(context_path=Path(os.environ["FIN_AGENT_DSH_CONTEXT_PATH"]),
        trace_path=Path(os.environ["FIN_AGENT_DSH_TRACE_PATH"]), system_tools=system_tools(data_root))
    server = create_server(bridge)
    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(server_name="finance-detail-fixture", server_version="1",
            capabilities=server.get_capabilities(notification_options=NotificationOptions(), experimental_capabilities={})))


def run(args):
    if args.live:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
    os.environ.update(FINANCE_DSH_PREWARM_ON_START="0", FINANCE_STATUS_ENABLED="0")
    pending, requests = deque(), []

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if not pending:
                self.send_error(500, "unexpected model request")
                return
            action = pending.popleft()
            delta, finish = {"role": "assistant"}, "stop"
            if isinstance(action, list):
                delta["tool_calls"] = [{"index": i, "id": f"call_{len(requests)}_{i}", "type": "function",
                    "function": {"name": "mcp__finance__" + name, "arguments": json.dumps(arguments)}}
                    for i, (name, arguments) in enumerate(action)]
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

    server = None
    if not args.live:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        os.environ.update(LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
            LLM_API_KEY="keyless-local-fixture", LLM_DEFAULT_MODEL="fixture")
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.presentation import FinancialQaPresentationService
    native, events = _load_sdk_class(), []
    rows = sample_rows()
    sources = ["src/scenarios/financial_qa/dsh_loop_policy.mjs", "src/scenarios/financial_qa/tools.py",
        "src/scenarios/financial_qa/dsh_service.py", "src/scenarios/financial_qa/dsh_system.md",
        "config/deepseek_harness/finance_query.patch.yml", "scripts/verify_finance_dsh_detail_replay.py"]
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(),
        "fin_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sources},
        "mode": "live model" if args.live else "keyless replay", "model": os.environ.get("LLM_DEFAULT_MODEL"),
        "scope": "synthetic catalog/provider; real DSH/MCP/query handlers/storage; no database, outer routing or production deployment", "cases": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="finance-detail-replay-") as directory:
            root = Path(directory)
            patch = root / "fixture-mcp.yml"
            source = (ROOT / "config/deepseek_harness/finance_query.patch.yml").read_text()
            original_args = "args: ['-m', 'src.scenarios.financial_qa.dsh_mcp_server']"
            assert original_args in source
            patch.write_text(source.replace(original_args, "args: " + json.dumps([
                str(Path(__file__).resolve()), "--mcp", "--data-root", str(root / "data")])) )

            class RecordingHarness(native):
                def __init__(self, **kwargs):
                    kwargs["patches"] = (str(patch), *kwargs["patches"][1:])
                    super().__init__(**kwargs)

                def run(self, *args, **kwargs):
                    result = super().run(*args, **kwargs)
                    events.extend(result.events)
                    return result

            tools = system_tools(root / "data")
            service = FinanceDeepSeekHarnessSessionService(enabled=True, worker_count=1, system_tools=tools,
                root_dir=root / "runtime", log_path=root / "events.jsonl", harness_factory=RecordingHarness)
            cases = [
                ("list", "请列出这些公司的名单。参考数据区已能分页显示所有190家公司，正文简要说明即可。", [], []),
                ("selected_all", "逐条阅读全部190条记录的投资要点和风险，概括共同观点并找出所有新增风险，注明公司代码。", ["code", "highlights", "risk"], list(range(0, 190, 50))),
                ("tail", "完整阅读最后一条fixture_189的raw_text，告诉我原文末尾的库存减值金额。", ["code", "raw_text"], [189]),
            ]
            if not args.live:
                cases += [("all_columns", "读取完整190条所有列。", [], list(range(0, 190, 50))),
                          ("skill_ten_pages", "按已加载方法遍历全部190条记录。", ["code", "risk"], list(range(0, 190, 20)))]
            try:
                for case, goal, columns, offsets in cases:
                    events.clear()
                    requests.clear()
                    pending.clear()
                    scope = "financial_qa_dsh:" + service._key(thread_id=case, owner_id="fixture-owner")
                    pending.append([("read_finance_catalog", {"subject": "fixture", "dataview": "reports", "operation": "query"})])
                    pending.append([("finance_query", {"steps": [{"goal": "取得合成研报快照",
                        "request": "r1 = fixture.reports.query() -> " + ", ".join(rows[0])}], "data_request_complete": True})])
                    actions = [("load_finance_result", {"result_ref": "r1", "offset": offset,
                        "limit": 1 if case == "tail" else 20 if case == "skill_ten_pages" else 50,
                        **({"columns": columns} if columns else {})}) for offset in offsets]
                    if actions:
                        if case == "skill_ten_pages":
                            pending.extend([[action] for action in actions])
                        else:
                            pending.append(actions)
                    pending.append("固定回放完成；只验证数据传递，不评估模型理解。")
                    context = {"_finance_history_independent": True}
                    if case == "skill_ten_pages":
                        context["_finance_explicit_skill_prompt"] = "读取完整目标范围后归纳风险；使用已存数据。"
                    prompt = "请先查询fixture.reports的完整合成研报表，保留全部字段的快照，然后完成以下工作：\n" + goal
                    prompt += "\n" + FinancialQaPresentationService.result_delivery_prompt(thread_id=23)
                    started = time.monotonic()
                    record = service.run_turn(thread_id=case, owner_id="fixture-owner", turn_id=1,
                        user_text=prompt, context=context)
                    assert not record.get("error"), (record.get("error"), [e for e in events if "error" in e.get("type", "")])
                    calls, payloads, errors = [], [], []
                    for event in events:
                        data = event.get("data", {})
                        if event.get("type") == "tool/call":
                            arguments = data.get("arguments", {})
                            if isinstance(arguments, str):
                                arguments = json.loads(arguments)
                            calls.append({"name": data.get("name"), "arguments": arguments})
                        if event.get("type") == "tool/result":
                            for block in data.get("message", {}).get("content", []):
                                if block.get("isError"):
                                    errors.append("native tool error")
                                for item in block.get("content", []):
                                    if item.get("type") != "text":
                                        continue
                                    try:
                                        payload = json.loads(item["text"])
                                    except (ValueError, TypeError):
                                        continue
                                    if isinstance(payload, dict) and payload.get("error"):
                                        errors.append(str(payload["error"]))
                                    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                                        payloads.append(payload)
                    visible_rows = [row for payload in payloads for row in payload["rows"]]
                    summary = {"elapsed_seconds": round(time.monotonic() - started, 3), "calls": calls,
                        "pages_returned": [p.get("page") for p in payloads], "rows_returned": len(visible_rows),
                        "row_json_chars": sum(len(json.dumps(p["rows"], ensure_ascii=False, separators=(",", ":"))) for p in payloads),
                        "errors": errors, "usage": record.get("llm_usage"), "answer": record.get("result")}
                    if not args.live:
                        assert not pending and not errors, (case, pending, errors)
                        expected_rows = [({k: row[k] for k in columns} if columns else row)
                            for row in (rows[-1:] if case == "tail" else rows if offsets else [])]
                        assert visible_rows == expected_rows, (case, "native evidence changed")
                        # Assert the actual next model request contains the full tool rows,
                        # not just the durable canonical tool event.
                        received = []
                        for message in requests[-1]["messages"]:
                            if message.get("role") == "tool":
                                payload = json.loads(message["content"])
                                if isinstance(payload, dict) and "rows" in payload:
                                    received.extend(payload["rows"])
                        assert received == expected_rows, (case, "model-visible evidence changed")
                        summary["passed"] = True
                        summary["model_requests"] = len(requests)
                    else:
                        answer = str(record.get("result") or "")
                        covered = {r.get("code") for r in visible_rows}
                        summary["coverage_check"] = (
                            not payloads and ("190" in answer or "参考数据" in answer) if case == "list" else
                            len(covered) == 190 and "137" in answer and "客户集中度" in answer if case == "selected_all" else
                            any(r.get("raw_text") == rows[-1]["raw_text"] for r in visible_rows) and "731" in answer)
                    report["cases"][case] = summary
                    print(json.dumps({"case": case, "elapsed_seconds": summary["elapsed_seconds"],
                        "tool_calls": len(calls), "rows": len(visible_rows), "errors": errors,
                        "check": summary.get("passed", summary.get("coverage_check"))}, ensure_ascii=False), flush=True)
                    # Same saved snapshot must still be accessible after projection.
                    variables = tools.result_store.list_variables(session_id=scope)
                    assert variables
                    assert tools.result_store.load_data_ref(session_id=scope, data_ref=variables[0]["data_ref"], offset=189, limit=1)["rows"] == rows[-1:]
            finally:
                service.close()
    finally:
        if server:
            server.shutdown()
            server.server_close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--mcp", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.mcp:
        import anyio
        anyio.run(serve_mcp, args.data_root)
    else:
        report = run(args)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"completed": list(report["cases"]), "mode": report["mode"]}, ensure_ascii=False))
