#!/usr/bin/env python3
"""Summarize DSH per-request context, reasoning, and loop usage."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = round((len(ordered) - 1) * fraction)
    return ordered[min(len(ordered) - 1, max(0, index))]


def _stats(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "sum": round(sum(values), 3),
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "median": round(statistics.median(values), 3) if values else 0.0,
        "p95": round(_percentile(values, 0.95), 3),
        "min": round(min(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
    }


def _case_row(case: Mapping[str, Any]) -> dict[str, Any]:
    financial = _mapping(case.get("financial_qa"))
    usage = _mapping(case.get("llm_usage"))
    steps = [
        dict(item)
        for item in financial.get("llm_step_usages") or []
        if isinstance(item, Mapping)
    ]
    call_count = int(usage.get("call_count") or len(steps))
    cumulative_context = int(
        usage.get("cumulative_context_tokens")
        or int(usage.get("prompt_tokens") or 0)
        + int(usage.get("cache_read_tokens") or 0)
    )
    output_tokens = int(usage.get("completion_tokens") or 0)
    reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
    return {
        "case_id": str(case.get("case_id") or ""),
        "question": str(case.get("question") or ""),
        "status": str(case.get("status") or ""),
        "reasoning_effort": str(financial.get("reasoning_effort") or ""),
        "total_elapsed_ms": float(case.get("total_elapsed_ms") or 0),
        "runtime_duration_ms": float(financial.get("duration_ms") or 0),
        "model_call_count": call_count,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cumulative_context_tokens": cumulative_context,
        "mean_context_tokens_per_model_call": round(
            cumulative_context / call_count, 3
        )
        if call_count
        else 0.0,
        "max_context_tokens_per_model_call": int(
            usage.get("max_context_tokens_per_call")
            or max((int(item.get("context_tokens") or 0) for item in steps), default=0)
        ),
        "final_context_tokens": int(
            usage.get("final_context_tokens")
            or (steps[-1].get("context_tokens") if steps else 0)
            or 0
        ),
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "reasoning_share_of_output": round(
            reasoning_tokens / output_tokens, 6
        )
        if output_tokens
        else 0.0,
        "tool_call_count": len(financial.get("tool_calls") or []),
        "llm_step_usages": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.results.read_text(encoding="utf-8"))
    rows = [_case_row(item) for item in payload.get("cases") or []]
    all_steps = [step for row in rows for step in row["llm_step_usages"]]
    by_request_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for step in all_steps:
        by_request_index[int(step.get("request_index") or 0)].append(step)

    total_calls = sum(row["model_call_count"] for row in rows)
    total_context = sum(row["cumulative_context_tokens"] for row in rows)
    total_output = sum(row["output_tokens"] for row in rows)
    total_reasoning = sum(row["reasoning_tokens"] for row in rows)
    output = {
        "analysis": "dsh_llm_context_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(args.results.resolve()),
        "case_count": len(rows),
        "step_telemetry_case_count": sum(
            bool(row["llm_step_usages"]) for row in rows
        ),
        "totals": {
            "model_call_count": total_calls,
            "cumulative_context_tokens": total_context,
            "context_tokens_per_model_call": round(
                total_context / total_calls, 3
            )
            if total_calls
            else 0.0,
            "output_tokens": total_output,
            "reasoning_tokens": total_reasoning,
            "reasoning_share_of_output": round(
                total_reasoning / total_output, 6
            )
            if total_output
            else 0.0,
        },
        "case_stats": {
            key: _stats([float(row[key]) for row in rows])
            for key in (
                "total_elapsed_ms",
                "runtime_duration_ms",
                "model_call_count",
                "cumulative_context_tokens",
                "mean_context_tokens_per_model_call",
                "max_context_tokens_per_model_call",
                "final_context_tokens",
                "prompt_tokens",
                "cache_read_tokens",
                "output_tokens",
                "reasoning_tokens",
                "tool_call_count",
            )
        },
        "all_model_calls": {
            "context_tokens": _stats(
                [float(item.get("context_tokens") or 0) for item in all_steps]
            ),
            "output_tokens": _stats(
                [float(item.get("output_tokens") or 0) for item in all_steps]
            ),
            "reasoning_tokens": _stats(
                [float(item.get("reasoning_tokens") or 0) for item in all_steps]
            ),
        },
        "by_request_index": [
            {
                "request_index": index,
                "case_count": len(steps),
                "context_tokens": _stats(
                    [float(item.get("context_tokens") or 0) for item in steps]
                ),
                "output_tokens": _stats(
                    [float(item.get("output_tokens") or 0) for item in steps]
                ),
                "reasoning_tokens": _stats(
                    [float(item.get("reasoning_tokens") or 0) for item in steps]
                ),
            }
            for index, steps in sorted(by_request_index.items())
        ],
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": output["case_count"],
        "step_telemetry_case_count": output["step_telemetry_case_count"],
        "totals": output["totals"],
        "all_model_calls": output["all_model_calls"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
