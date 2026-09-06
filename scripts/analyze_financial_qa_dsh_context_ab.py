#!/usr/bin/env python3
"""Compare paired artifacts from eval_financial_qa_dsh_context_ab.py.

API equality is not an accuracy score. Inspect requests/rows and answers too;
the report intentionally exposes query-volume differences and rejected calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def measure(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data["record"]
    usage = record.get("llm_usage") or {}
    refs = record.get("result_refs") or []
    calls = record.get("tool_calls") or []
    result = {
        "case": data["id"], "repeat": data["repeat"], "question": data["question"],
        "elapsed_ms": data["elapsed_ms"], "dsh_ms": record["duration_ms"],
        "query_ms": sum(c.get("api_execution_ms", 0) for c in calls),
        "context_tokens": usage.get("cumulative_context_tokens", 0),
        "uncached_input_tokens": usage.get("prompt_tokens", 0),
        "cached_input_tokens": usage.get("cache_read_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "llm_calls": usage.get("call_count", 0),
        "detail_calls": sum(c.get("tool") == "load_finance_result" for c in calls),
        "apis": sorted({r["api"] for r in refs}),
        "routes": [{k: c.get(k) for k in ("subject", "dataview", "operation")}
                   for c in calls if c.get("tool") == "read_finance_catalog"],
        "datasets": [{"api": r["api"], "rows": r.get("row_count"), "request": r.get("request")}
                     for r in refs],
        "error": record.get("error", ""), "summary": data["response"].get("summary", ""),
        "catalog_revision": record.get("finance_catalog_revision"),
    }
    session = next((path.parent / "runtime").rglob(record["session_id"] + "/session.jsonl"), None)
    if session:
        events = [json.loads(line) for line in session.read_text(encoding="utf-8").splitlines()]
        header = next(e["data"]["header"] for e in events if e["type"] == "request/header")
        first_messages = []
        for event in events:
            if event["type"] == "assistant/message":
                break
            if event["type"] == "user/message" and event.get("surfaceOp") == "append":
                first_messages.append(event["data"]["content"])
        first_input = {"system": header.get("system"), "tools": header.get("tools"),
                       "config": header.get("config"), "messages": first_messages}
        result["initial_input_sha256"] = hashlib.sha256(json.dumps(
            first_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        starts = {}
        latencies = []
        for event in events:
            if event["type"] == "step/start":
                starts[event["data"]["step"]] = event["time"]
            elif event["type"] == "assistant/message" and event["data"].get("step") in starts:
                latencies.append(event["time"] - starts[event["data"]["step"]])
        result["model_stage_ms"] = sum(latencies)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before = {p.name: measure(p) for p in args.before.glob("*.json")}
    after = {p.name: measure(p) for p in args.after.glob("*.json")}
    if set(before) != set(after):
        raise ValueError("A/B must contain exactly the same case/repetition pairs")
    pairs = [{"before": before[key], "after": after[key],
              "exact_api_set_equal": before[key]["apis"] == after[key]["apis"]}
             for key in sorted(before)]
    metrics = ("elapsed_ms", "query_ms", "context_tokens", "uncached_input_tokens",
               "cached_input_tokens", "output_tokens", "llm_calls", "detail_calls")
    if all("model_stage_ms" in p[side] for p in pairs for side in ("before", "after")):
        metrics += ("model_stage_ms",)
    totals = {side: {key: sum(p[side][key] for p in pairs) for key in metrics}
              for side in ("before", "after")}
    reductions = {key: round(1 - totals["after"][key] / totals["before"][key], 4)
                  for key in metrics if totals["before"][key]}
    case_metrics = []
    for case_id in dict.fromkeys(p["before"]["case"] for p in pairs):
        selected = [p for p in pairs if p["before"]["case"] == case_id]
        row = {"case": case_id, "question": selected[0]["before"]["question"], "repetitions": len(selected)}
        for side in ("before", "after"):
            row[side] = {key: round(statistics.mean(p[side][key] for p in selected), 1) for key in metrics}
        case_metrics.append(row)
    result = {"pairs": pairs, "case_metrics": case_metrics, "totals": totals,
              "reductions": reductions, "exact_api_set_equal": sum(p["exact_api_set_equal"] for p in pairs)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "pairs"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
