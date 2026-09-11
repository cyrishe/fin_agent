"""Evaluate existing cases over authenticated REST/MCP, saving each response before proceeding.

Run the pilot first and review it before starting a full run. Empty data is not
an execution failure. No answer/summary generation is requested.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json",
    "outputs/financial_qa_mainland_eval_20260902/cases_mainland_supported.json",
    "outputs/financial_qa_mainland_full_increment_20260903/cases_increment_no_news.json",
]
# Existing questions, covering seven subjects, financial statements and both report views.
PILOT_IDS = ["BUS028", "BUS009", "BUS176", "BUS192", "BUS151", "BUS119", "BUS085", "BUS214", "RTE007", "RTE016"]


def load_cases(root=ROOT):
    cases = []
    for source in SOURCES:
        for case in json.loads((root / source).read_text())["cases"]:
            cases.append({**case, "source_file": source})
    return cases


def payload_problems(payload, status):
    problems = []
    if status != 200 or payload.get("ok") is not True:
        problems.append("request_failed")
    if payload.get("summary"):
        problems.append("unexpected_generated_summary")
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return problems + ["detail_missing"]
    llm_steps = [s for s in detail.get("steps", []) if s.get("kind") == "llm"]
    if not llm_steps or any(s.get("duration_ms") is None or s.get("usage") is None for s in llm_steps):
        problems.append("model_step_telemetry_incomplete")
    if detail.get("turns") != len(llm_steps):
        problems.append("turn_count_mismatch")
    return problems


def call_case(client, base, case, transport):
    request = {"query": case["question"], "response_mode": "data", "detail": True}
    started = time.monotonic()
    if transport == "mcp":
        response = client.post(base + "/mcp", json={"jsonrpc":"2.0", "id":1,
            "method":"tools/call", "params":{"name":"finance_data_query", "arguments":request}})
        envelope = response.json()
        payload = envelope.get("result", {}).get("structuredContent", {})
    else:
        response = client.post(base + "/v1/finance/query", json=request)
        envelope = response.json()
        payload = envelope
    elapsed = round((time.monotonic() - started) * 1000, 3)
    problems = payload_problems(payload, response.status_code)
    return {"case": case, "transport": transport, "request": request,
        "http_status": response.status_code, "client_elapsed_ms": elapsed,
        "problems": problems, "response": payload, "wire_response": envelope,
        "finished_at": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://ai-agent.kingdomai.com/fin_agent")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="Only after reviewing the pilot")
    parser.add_argument("--case-ids", help="Subset of existing IDs for incremental manual review")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--record-query-errors", action="store_true", help="Keep application query failures as outcomes; still stop on transport or incomplete detail")
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    key = os.getenv("FINANCE_API_KEY") or values.get("FINANCE_API_KEY")
    if not key:
        keys = json.loads(values.get("FINANCE_API_KEYS_JSON") or "{}")
        key = next(iter(keys.values()), None)
    if not key:
        raise SystemExit("Configure a financial API key; do not pass it on the command line")
    all_cases = load_cases()
    wanted = args.case_ids.split(",") if args.case_ids else (None if args.full else PILOT_IDS)
    by_id = {c["case_id"]: c for c in all_cases}
    cases = [by_id[cid] for cid in wanted] if wanted else all_cases
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=args.timeout, headers={"X-API-Key": key, "Accept":"application/json, text/event-stream"}) as client:
        for case in cases:
            path = args.output_dir / (case["case_id"] + ".json")
            if path.exists():
                previous = json.loads(path.read_text())
                if blocking_problems(previous, args.record_query_errors):
                    raise SystemExit(f"Prior failed case {case['case_id']} requires review; not continuing")
                continue
            transport = "mcp" if case["case_id"].startswith("RTE") else "api"
            try:
                result = call_case(client, args.base_url.rstrip("/"), case, transport)
            except Exception as exc:
                # No automatic replay of a timed-out billable request.
                result = {"case":case, "transport":transport, "problems":["transport_error"], "error_type":type(exc).__name__}
            with path.open("x") as out:
                json.dump(result, out, ensure_ascii=False, indent=2)
            response = result.get("response", {})
            print(json.dumps({"case_id":case["case_id"], "transport":transport,
                "http_status":result.get("http_status"), "turns":response.get("detail",{}).get("turns"),
                "tokens":response.get("detail",{}).get("total_tokens"), "rows":response.get("execution",{}).get("total_rows"),
                "client_elapsed_ms":result.get("client_elapsed_ms"), "problems":result["problems"]}, ensure_ascii=False), flush=True)
            if blocking_problems(result, args.record_query_errors):
                raise SystemExit(2)


def blocking_problems(result, record_query_errors=False):
    problems = list(result.get("problems", []))
    if record_query_errors and result.get("http_status") in (200, 502) and (result.get("response", {}).get("error") or {}).get("code") == "finance_query_failed":
        problems = [p for p in problems if p != "request_failed"]
    return problems


if __name__ == "__main__":
    main()
