#!/usr/bin/env python3
"""Merge compatible DSH financial-QA analyses and recompute aggregate metrics."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


METRICS = (
    "elapsed_ms",
    "runtime_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cumulative_context_tokens",
    "reasoning_tokens",
    "model_calls",
    "tool_calls",
    "catalog_reads",
    "finance_queries",
    "result_loads",
)


def percentile_inc(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    rank = (len(values) - 1) * p
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (rank - lo)


def describe(values: list[float]) -> dict:
    values = [float(v or 0) for v in values]
    ordered = sorted(values)
    count = len(ordered)
    total = sum(ordered)
    median = percentile_inc(ordered, 0.5)
    return {
        "count": count,
        "sum": round(total, 6),
        "mean": round(total / count, 6) if count else 0,
        "median": round(median, 6),
        "p95": round(percentile_inc(ordered, 0.95), 6),
        "min": round(ordered[0], 6) if count else 0,
        "max": round(ordered[-1], 6) if count else 0,
    }


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit("usage: merge_financial_qa_dsh_analyses.py output.json input1.json input2.json [...]")
    output = Path(sys.argv[1])
    sources = [Path(p) for p in sys.argv[2:]]
    docs = [json.loads(p.read_text()) for p in sources]
    cases = [case for doc in docs for case in doc.get("cases", [])]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate case_id found in merged analyses")

    # Preserve the original benchmark order using the numeric portion of source ids.
    cases.sort(key=lambda c: (int("".join(ch for ch in c["case_id"] if ch.isdigit()) or 0), c["case_id"]))
    summary = {
        "case_count": len(cases),
        "first_pass_ok": sum(c.get("first_pass_status") == "ok" for c in cases),
        "first_pass_error": sum(c.get("first_pass_status") != "ok" for c in cases),
        "effective_ok": sum(c.get("status") == "ok" for c in cases),
        "effective_error": sum(c.get("status") != "ok" for c in cases),
        "first_entry_correct": sum(bool(c.get("first_entry_correct")) for c in cases),
        "eventual_acceptable": sum(bool(c.get("eventual_acceptable")) for c in cases),
        "all_required_entries": sum(bool(c.get("all_required_entries")) for c in cases),
        "zero_row_cases": sum((c.get("zero_row_queries") or 0) > 0 for c in cases),
        "metrics": {metric: describe([c.get(metric, 0) for c in cases]) for metric in METRICS},
    }
    merged = {
        "analysis": "dsh_opt_mainland_report_full_no_news_184",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_path": "/Volumes/ext/fin_agent/outputs/financial_qa_mainland_eval_20260902/研报评测集_大陆上市公司版_20260902.xlsx",
        "source_metadata": {
            "mother_set": 200,
            "excluded_pure_news": 16,
            "evaluated_no_news": len(cases),
            "components": [str(p.resolve()) for p in sources],
        },
        "selection": {
            "concurrency": 3,
            "timeout_seconds": 600.0,
            "financial_qa_runtime": "dsh",
            "profile": "opt",
        },
        "summary": summary,
        "first_pass_failures": [item for doc in docs for item in doc.get("first_pass_failures", [])],
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
