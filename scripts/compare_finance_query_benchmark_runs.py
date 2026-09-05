#!/usr/bin/env python3
"""Build a paired comparison for two finance-query benchmark runs."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = round((len(ordered) - 1) * fraction)
    return ordered[min(len(ordered) - 1, max(0, index))]


def _stats(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "median": round(statistics.median(values), 3) if values else 0.0,
        "p95": round(_percentile(values, 0.95), 3),
        "sum": round(sum(values), 3),
    }


def _raw_metrics(case: dict[str, Any]) -> dict[str, float]:
    financial = case.get("financial_qa") or {}
    usage = case.get("llm_usage") or {}
    calls = financial.get("tool_calls") or []
    prompt_tokens = float(usage.get("prompt_tokens") or 0)
    cache_read_tokens = float(usage.get("cache_read_tokens") or 0)
    model_call_count = float(usage.get("call_count") or 0)
    cumulative_context_tokens = float(
        usage.get("cumulative_context_tokens")
        or prompt_tokens + cache_read_tokens
    )
    return {
        "total_elapsed_ms": float(case.get("total_elapsed_ms") or 0),
        "runtime_duration_ms": float(financial.get("duration_ms") or 0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": float(usage.get("completion_tokens") or 0),
        "total_tokens": float(usage.get("total_tokens") or 0),
        "reasoning_tokens": float(usage.get("reasoning_tokens") or 0),
        "reported_cache_read_tokens": cache_read_tokens,
        "cumulative_context_tokens": cumulative_context_tokens,
        "mean_context_tokens_per_model_call": (
            cumulative_context_tokens / model_call_count
            if model_call_count
            else 0.0
        ),
        "model_call_count": model_call_count,
        "tool_call_count": float(len(calls)),
        "catalog_read_count": float(
            sum(c.get("tool") == "read_finance_catalog" for c in calls)
        ),
        "finance_query_count": float(sum(c.get("tool") == "finance_query" for c in calls)),
        "result_detail_load_count": float(
            sum(c.get("tool") == "load_finance_result" for c in calls)
        ),
        "assistant_message_count": float(financial.get("assistant_message_count") or 0),
        "tool_result_message_count": float(financial.get("tool_result_message_count") or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-results", type=Path, required=True)
    parser.add_argument("--candidate-results", type=Path, required=True)
    parser.add_argument("--baseline-analysis", type=Path, required=True)
    parser.add_argument("--candidate-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline_raw = _load(args.baseline_results)
    candidate_raw = _load(args.candidate_results)
    baseline_analysis = _load(args.baseline_analysis)
    candidate_analysis = _load(args.candidate_analysis)
    baseline_cases = {str(c["case_id"]): c for c in baseline_raw.get("cases", [])}
    candidate_cases = {str(c["case_id"]): c for c in candidate_raw.get("cases", [])}
    baseline_rows = {str(c["case_id"]): c for c in baseline_analysis.get("cases", [])}
    candidate_rows = {str(c["case_id"]): c for c in candidate_analysis.get("cases", [])}
    case_ids = sorted(set(baseline_cases) & set(candidate_cases))
    metric_names = list(_raw_metrics(baseline_cases[case_ids[0]]))

    rows: list[dict[str, Any]] = []
    for case_id in case_ids:
        old = _raw_metrics(baseline_cases[case_id])
        new = _raw_metrics(candidate_cases[case_id])
        rows.append({
            "case_id": case_id,
            "question": candidate_rows[case_id].get("question"),
            "baseline": old,
            "candidate": new,
            "delta": {key: round(new[key] - old[key], 3) for key in metric_names},
            "routing": {
                "baseline_first_entry_correct": baseline_rows[case_id].get("first_entry_correct"),
                "candidate_first_entry_correct": candidate_rows[case_id].get("first_entry_correct"),
                "baseline_eventual_acceptable": baseline_rows[case_id].get("eventual_acceptable_entry_found"),
                "candidate_eventual_acceptable": candidate_rows[case_id].get("eventual_acceptable_entry_found"),
            },
        })

    metrics: dict[str, Any] = {}
    for name in metric_names:
        old_values = [row["baseline"][name] for row in rows]
        new_values = [row["candidate"][name] for row in rows]
        deltas = [row["delta"][name] for row in rows]
        old_sum = sum(old_values)
        metrics[name] = {
            "baseline": _stats(old_values),
            "candidate": _stats(new_values),
            "paired_delta_candidate_minus_baseline": _stats(deltas),
            "aggregate_change_rate": round((sum(new_values) / old_sum - 1), 6) if old_sum else None,
            "improved_case_count": sum(delta < 0 for delta in deltas),
            "unchanged_case_count": sum(delta == 0 for delta in deltas),
            "regressed_case_count": sum(delta > 0 for delta in deltas),
        }

    old_summary = baseline_analysis.get("cc_summary") or {}
    new_summary = candidate_analysis.get("cc_summary") or {}
    output = {
        "comparison": "finance_query_compact_context_paired_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {key: str(getattr(args, key).resolve()) for key in (
            "baseline_results", "candidate_results", "baseline_analysis", "candidate_analysis"
        )},
        "case_count": len(rows),
        "completion": {
            "baseline": old_summary.get("completed_case_count"),
            "candidate": new_summary.get("completed_case_count"),
        },
        "routing_quality": {
            "baseline": {
                "first_entry_correct_rate": old_summary.get("first_entry_correct_rate"),
                "eventual_acceptable_entry_rate": old_summary.get("eventual_acceptable_entry_rate"),
            },
            "candidate": {
                "first_entry_correct_rate": new_summary.get("first_entry_correct_rate"),
                "eventual_acceptable_entry_rate": new_summary.get("eventual_acceptable_entry_rate"),
            },
        },
        "metrics": metrics,
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_count": len(rows), "routing_quality": output["routing_quality"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
