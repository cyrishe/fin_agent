#!/usr/bin/env python3
"""Summarize one DSH financial-QA benchmark, retaining first-pass failures."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.services.session_variable_store_service import SessionVariableStoreService


API_RE = re.compile(
    r"(?:^|[;\n])\s*(?:[A-Za-z_]\w*\s*=\s*)?"
    r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\("
)


def text(value: Any) -> str:
    return str(value or "").strip()


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[min(len(ordered) - 1, max(0, index))]


def stats(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "sum": round(sum(values), 3),
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "median": round(statistics.median(values), 3) if values else 0.0,
        "p95": round(percentile(values, 0.95), 3),
        "min": round(min(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
    }


def api_from_call(call: Mapping[str, Any]) -> str:
    explicit = text(call.get("api"))
    if explicit:
        return explicit
    match = API_RE.search(text(call.get("request") or call.get("submitted_request")))
    return match.group(1) if match else ""


def catalog_signature(call: Mapping[str, Any]) -> str:
    subject = text(call.get("subject"))
    dataview = text(call.get("dataview"))
    operation = text(call.get("operation")) or "?"
    if dataview.startswith(subject + "."):
        dataview = dataview.split(".", 1)[1]
    return f"{subject}.{dataview}:{operation}" if subject and dataview else f"*.*:{operation}"


def raw_results(financial: Mapping[str, Any], store: SessionVariableStoreService) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in financial.get("result_refs") or []:
        if not isinstance(item, Mapping):
            continue
        ref = text(item.get("result_ref"))
        materialized: dict[str, Any] = {}
        if ref:
            try:
                session_id, _ = store.parse_data_ref(ref)
                materialized = store.materialize_data_ref(session_id=session_id, data_ref=ref)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                materialized = {}
        sample = mapping(item.get("sample"))
        rows = materialized.get("rows") if isinstance(materialized.get("rows"), list) else sample.get("rows") or []
        results.append({
            "flow_step": item.get("flow_step"),
            "result_name": text(item.get("result_name")),
            "goal": text(item.get("goal")),
            "api": text(item.get("api")),
            "result_ref": ref,
            "row_count": int(item.get("row_count") or 0),
            "schema": dict(item.get("schema")) if isinstance(item.get("schema"), Mapping) else {},
            "rows_complete": bool(materialized) or bool(item.get("sample_complete")),
            "rows": [dict(value) for value in rows if isinstance(value, Mapping)],
        })
    return results


def row(case: Mapping[str, Any], *, first_pass_status: str, store: SessionVariableStoreService) -> dict[str, Any]:
    financial = mapping(case.get("financial_qa"))
    usage = mapping(case.get("llm_usage"))
    calls = [dict(item) for item in financial.get("tool_calls") or [] if isinstance(item, Mapping)]
    catalogs = [item for item in calls if text(item.get("tool")) == "read_finance_catalog"]
    queries = [item for item in calls if text(item.get("tool")) == "finance_query"]
    loads = [item for item in calls if text(item.get("tool")) == "load_finance_result"]
    catalog_sequence = [catalog_signature(item) for item in catalogs]
    catalog_entries = [item.split(":", 1)[0] for item in catalog_sequence if not item.startswith("*.*")]
    api_sequence = [api_from_call(item) for item in queries]
    api_sequence = [item for item in api_sequence if item]
    api_entries = [".".join(item.split(".")[:2]) for item in api_sequence]
    observed: list[str] = []
    for entry in [*catalog_entries, *api_entries]:
        if entry and entry not in observed:
            observed.append(entry)
    source = mapping(case.get("source_case"))
    acceptable = {text(item) for item in source.get("acceptable_first_entries") or [] if text(item)}
    required = {text(item) for item in source.get("required_entries") or [] if text(item)}
    requests = [dict(item) for item in mapping(financial.get("loop_policy")).get("requests") or [] if isinstance(item, Mapping)]
    refs = [dict(item) for item in financial.get("result_refs") or [] if isinstance(item, Mapping)]
    error = case.get("error") or financial.get("error")
    if isinstance(error, Mapping):
        error = error.get("message") or error.get("type")
    return {
        "case_id": text(case.get("case_id")),
        "question": text(case.get("question") or source.get("question")),
        "category": text(source.get("category")),
        "primary_entry": text(source.get("primary_entry")),
        "acceptable_entries": sorted(acceptable),
        "required_entries": sorted(required),
        "first_pass_status": first_pass_status,
        "status": text(case.get("status")),
        "error": text(error),
        "model": text(mapping(case.get("done_result")).get("model_name")),
        "reasoning_effort": text(financial.get("reasoning_effort")),
        "elapsed_ms": float(case.get("total_elapsed_ms") or 0),
        "runtime_ms": float(financial.get("duration_ms") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cumulative_context_tokens": int(usage.get("cumulative_context_tokens") or 0),
        "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
        "model_calls": int(usage.get("call_count") or 0),
        "tool_calls": len(calls),
        "catalog_reads": len(catalogs),
        "finance_queries": len(queries),
        "result_loads": len(loads),
        "zero_row_queries": sum(1 for item in queries if "row_count" in item and int(item.get("row_count") or 0) == 0),
        "result_rows": sum(int(item.get("row_count") or 0) for item in refs),
        "catalog_sequence": catalog_sequence,
        "api_sequence": api_sequence,
        "query_requests": [text(item.get("request") or item.get("submitted_request")) for item in queries],
        "first_entry": observed[0] if observed else "",
        "first_entry_correct": bool(observed and observed[0] in acceptable),
        "eventual_acceptable": bool(set(observed).intersection(acceptable)),
        "all_required_entries": required.issubset(set(observed)),
        "answer": text(mapping(case.get("done_result")).get("message")),
        "raw_results": raw_results(financial, store),
        "stage_trace": requests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-pass", type=Path, required=True)
    parser.add_argument("--retry", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    first = load(args.first_pass)
    retry = load(args.retry) if args.retry else {"cases": []}
    first_cases = {text(item.get("case_id")): item for item in first.get("cases") or []}
    retry_cases = {text(item.get("case_id")): item for item in retry.get("cases") or []}
    effective = dict(first_cases)
    effective.update(retry_cases)
    ordered_ids = [text(item.get("case_id")) for item in first.get("cases") or []]
    store = SessionVariableStoreService()
    rows = [row(effective[case_id], first_pass_status=text(first_cases[case_id].get("status")), store=store) for case_id in ordered_ids]
    numeric = [
        "elapsed_ms", "runtime_ms", "prompt_tokens", "completion_tokens", "total_tokens",
        "cache_read_tokens", "cumulative_context_tokens", "reasoning_tokens", "model_calls",
        "tool_calls", "catalog_reads", "finance_queries", "result_loads",
    ]
    summary = {
        "case_count": len(rows),
        "first_pass_ok": sum(text(item.get("status")) == "ok" for item in first_cases.values()),
        "first_pass_error": sum(text(item.get("status")) != "ok" for item in first_cases.values()),
        "effective_ok": sum(item["status"] == "ok" for item in rows),
        "effective_error": sum(item["status"] != "ok" for item in rows),
        "first_entry_correct": sum(item["first_entry_correct"] for item in rows),
        "eventual_acceptable": sum(item["eventual_acceptable"] for item in rows),
        "all_required_entries": sum(item["all_required_entries"] for item in rows),
        "zero_row_cases": sum(item["zero_row_queries"] > 0 for item in rows),
        "metrics": {key: stats([float(item[key]) for item in rows]) for key in numeric},
    }
    output = {
        "analysis": "financial_qa_dsh_opt_single_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "first_pass_path": str(args.first_pass.resolve()),
        "retry_path": str(args.retry.resolve()) if args.retry else "",
        "source_path": text(first.get("source_path")),
        "source_metadata": first.get("source_metadata") or {},
        "selection": first.get("selection") or {},
        "summary": summary,
        "first_pass_failures": [
            {"case_id": case_id, "status": text(item.get("status")), "error": text(item.get("error")), "elapsed_ms": float(item.get("total_elapsed_ms") or 0)}
            for case_id, item in first_cases.items() if text(item.get("status")) != "ok"
        ],
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
