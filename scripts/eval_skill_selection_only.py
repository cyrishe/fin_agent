"""Replay real DSH first requests without dispatching any model-selected tool.

The local capture endpoint always returns HTTP 400 (never a model completion).
After DSH closes, replay that exact request to the configured model, changing
only streaming transport. The completion is saved and scored, NOT sent to DSH.
This measures first-generation method selection, not full chat routing/execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def cases_from_history():
    source = "outputs/financial_qa_cc/finance_business_skills_real_20260729.json"
    rows = json.loads((ROOT / source).read_text())["cases"]
    cases = []
    single_stock = {"earnings-analysis", "valuation-analysis", "financial-quality-analysis", "technical-structure-analysis", "dividend-analysis"}
    for row in rows:
        primary = row["expected_skill_id"]
        acceptable = [primary] + (["stock-research"] if primary in single_stock else [])
        # A comprehensive stock study may start with relevant specialist methods.
        if primary == "stock-research":
            acceptable += ["financial-quality-analysis", "valuation-analysis", "equity-report-analysis", "technical-structure-analysis"]
        if primary == "stock-comparison":
            acceptable += ["financial-quality-analysis", "valuation-analysis"]
        cases.append(dict(id=row["id"], question=row["question"], source=source,
                          primary=primary, acceptable=acceptable, expected_generic=False))
    source = "outputs/server_report_eval_20260908/chat_cases.json"
    for ident, question in json.loads((ROOT / source).read_text())["cases"]:
        cases.append(dict(id=ident, question=question, source=source,
                          primary="equity-report-analysis", acceptable=["equity-report-analysis", "stock-research"], expected_generic=False))
    source = "outputs/server_report_eval_20260908/cases20.json"
    for row in json.loads((ROOT / source).read_text())["cases"]:
        if row["case_id"] not in {"RTE001", "RTE003", "RTE005"}:
            continue
        primary = "stock-research" if row["case_id"] == "RTE001" else "equity-report-analysis"
        acceptable = ["stock-research", "equity-report-analysis"]
        if row["case_id"] == "RTE003":
            acceptable += ["stock-comparison"]
        cases.append(dict(id=row["case_id"], question=row["question"], source=source,
                          primary=primary, acceptable=acceptable, expected_generic=False))
    source = "outputs/financial_qa_cc/stock_research_rewrite_simple_entry_20260804.json"
    row = json.loads((ROOT / source).read_text())["cases"][0]
    cases.append(dict(id="raw_quote_control", question=row["question"], source=source,
                      primary=None, acceptable=["stock-research"], expected_generic=True))
    return cases


def capture_request(question, directory):
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService, _load_sdk_class
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools

    captured = []

    class CaptureOnly(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No headers or credentials in logs.

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append({"path": self.path, "body": body})
            reply = b'{"error":{"message":"selection-only capture boundary","type":"invalid_request_error","code":"selection_capture"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureOnly)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sdk = _load_sdk_class()

    def harness_factory(**kwargs):
        kwargs["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
        kwargs["api_key"] = "local-capture-placeholder"
        kwargs["request_timeout_seconds"] = 45
        return sdk(**kwargs)

    system_tools = FinanceDataQueryCcTools()
    dsh = FinanceDeepSeekHarnessSessionService(
        enabled=True, system_tools=system_tools, worker_count=1,
        root_dir=directory / "runtime", log_path=directory / "capture-events.jsonl",
        harness_factory=harness_factory,
    )
    service = FinancialQaCcService(enabled=True, system_tools=system_tools,
                                   session_service=object(), dsh_session_service=dsh)
    try:
        service.answer(
            thread_id=directory.name, turn_id=directory.name,
            owner_id="selection-only-eval", user_text=question,
            dispatch_plan={"selected_agent": "investment_analyst", "turn_mode": "normal_qa", "entry": "agent_route",
                           "semantic_turn": {"resolved_question": question}},
            runtime="dsh", research_mode="auto", execution_mode="standard", isolated_request=True,
        )
    finally:
        dsh.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    if not captured:
        raise RuntimeError("No first model request captured; inspect local capture events")
    traces = []
    for path in directory.glob("runtime/**/turn_trace.json"):
        tracker = json.loads(path.read_text()).get("tracker", {})
        traces.append({"calls": tracker.get("calls", []), "result_refs": tracker.get("result_refs", [])})
    if not traces or any(trace["calls"] or trace["result_refs"] for trace in traces):
        raise RuntimeError("Capture boundary did not prove zero tool execution")
    save(directory / "capture.json", {"requests": captured, "traces": traces, "termination": "intentional HTTP 400; no provider response delivered to DSH"})
    return captured[0]["body"]


def replay_request(captured):
    import requests
    body = dict(captured)
    body["stream"] = False
    body.pop("stream_options", None)
    response = requests.post(
        os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]},
        json=body, timeout=(10, 120),
    )
    if response.status_code != 200:
        # Do not persist provider error messages, which may echo credentials.
        raise RuntimeError(f"Model HTTP {response.status_code}")
    return body, response.json()


def score(case, response, *, allow_catalog=False):
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    selected, calls, invalid = [], message.get("tool_calls") or [], []
    catalog_calls = []
    for call in calls:
        function = call.get("function") or {}
        if allow_catalog and function.get("name", "").endswith("read_finance_catalog"):
            try:
                args = json.loads(function.get("arguments", "{}"))
                if not isinstance(args, dict) or set(args) - {"subject", "dataview", "operation"}:
                    raise ValueError("invalid catalog arguments")
                catalog_calls.append(args)
            except (ValueError, TypeError):
                invalid.append("invalid_catalog_arguments")
            continue
        if not function.get("name", "").endswith("read_finance_skill"):
            invalid.append(function.get("name", "unknown"))
            continue
        try:
            args = json.loads(function.get("arguments", "{}"))
            ids = args.get("skill_ids", [args["skill_id"]] if args.get("skill_id") else None)
            if not isinstance(ids, list) or not all(isinstance(x, str) and x for x in ids):
                raise ValueError("invalid skill ids")
            selected.extend(ids)
        except (ValueError, TypeError, AttributeError):
            invalid.append("invalid_skill_arguments")
    selected = list(dict.fromkeys(selected))
    valid = bool(calls) and not invalid and choice.get("finish_reason") == "tool_calls"
    primary_hit = valid and (not selected if case["expected_generic"] else case["primary"] in selected)
    acceptable = valid and all(x in case["acceptable"] for x in selected) and (bool(selected) or case["expected_generic"])
    return {"selected": selected, "catalog_calls": catalog_calls, "primary_hit": primary_hit, "acceptable_selection": acceptable,
            "invalid_calls": invalid, "finish_reason": choice.get("finish_reason"), "tool_calls": calls}


def main():
    from dotenv import load_dotenv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--cases-file", type=Path, help="Frozen questions and selection expectations; no gold enters model context.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(args.env_file, override=False)
    if not os.environ.get("LLM_API_KEY"):
        raise SystemExit("Configure canonical LLM_API_KEY first")
    out = args.output.resolve()
    if out.exists():
        raise SystemExit("Preserve existing run: choose a new output directory")
    source_cases = json.loads(args.cases_file.read_text())["cases"] if args.cases_file else cases_from_history()
    cases = [case for case in source_cases if not args.case or case["id"] in args.case]
    if not cases:
        raise SystemExit("No matching cases")
    from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
    catalog = FinanceBusinessSkillCatalog()
    files = list(ROOT.glob("src/skills/**/*")) + list(ROOT.glob("src/scenarios/financial_qa/*")) + [Path(__file__), ROOT / "config/deepseek_harness/finance_query.patch.yml"]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file() and "__pycache__" not in str(p) and not p.name.startswith("._")}
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(),
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "worktree": subprocess.check_output(["git", "status", "--short"], text=True),
                "source_hashes": hashes, "skill_revision": catalog.revision,
                "catalog": catalog.public_entries(), "cases": cases,
                "model": os.environ["LLM_DEFAULT_MODEL"], "model_host": urlsplit(os.environ["LLM_BASE_URL"]).hostname,
                "scope": "Real FinancialQaCcService + DSH initial request capture; real model first-generation replay; no dispatcher, HTTP chat, personal Skills, or business tool execution",
                "scorer": "v2: predeclared primary inclusion; direct catalog discovery or empty method selection is a generic path. Tool names must be present in the captured native first-step schema. Original raw scores are preserved; no repeated stability estimate"}
    save(out / "manifest.json", manifest)
    results = []
    for case in cases:
        started = time.monotonic()
        directory = out / case["id"]
        print("START " + case["id"], flush=True)
        nodes = [{"node": "capture", "status": "started"}]
        try:
            captured = capture_request(case["question"], directory)
            nodes += [{"node": "capture", "status": "completed"}, {"node": "model_selection", "status": "started"}]
            model_started = time.monotonic()
            request, response = replay_request(captured)
            model_elapsed_ms = round((time.monotonic() - model_started) * 1000)
            save(directory / "request.json", request)
            save(directory / "response.json", response)
            catalog_visible = any((t.get("function") or {}).get("name", "").endswith("read_finance_catalog") for t in request.get("tools", []))
            result = {"id": case["id"], **score(case, response, allow_catalog=catalog_visible), "usage": response.get("usage"), "model_elapsed_ms": model_elapsed_ms}
            nodes += [{"node": "model_selection", "status": "completed"}, {"node": "tool_dispatch", "status": "not_executed"}]
        except Exception as exc:
            result = {"id": case["id"], "error_type": type(exc).__name__}
            nodes.append({"node": "evaluation", "status": "failed", "error_type": type(exc).__name__})
        result.update(elapsed_ms=round((time.monotonic()-started)*1000), nodes=nodes)
        results.append(result)
        save(directory / "result.json", result)
        save(out / "results.json", results)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    completed = [r for r in results if "error_type" not in r]
    summary = {"total": len(results), "completed": len(completed), "errors": len(results)-len(completed),
               "primary_hits": sum(r["primary_hit"] for r in completed),
               "acceptable_selections": sum(r["acceptable_selection"] for r in completed),
               "business_tools_executed": 0,
               "source_hashes_still_match": all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == h for p,h in hashes.items())}
    save(out / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
