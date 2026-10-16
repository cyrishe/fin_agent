#!/usr/bin/env python3
"""Build a reproducible CC / DSH Low / DSH Opt financial-QA comparison."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


API_RE = re.compile(
    r"(?:^|[;\n])\s*(?:[A-Za-z_]\w*\s*=\s*)?"
    r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\("
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
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


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _compile_api_mapping(catalog: Mapping[str, Any]) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for subject, subject_body in _mapping(catalog.get("subjects")).items():
        if not isinstance(subject_body, Mapping):
            continue
        for dataview, view_body in subject_body.items():
            if dataview == "_meta" or not isinstance(view_body, Mapping):
                continue
            entry = f"{subject}.{dataview}"
            for api in view_body.get("api") or []:
                if not isinstance(api, Mapping):
                    continue
                api_name = _text(api.get("api_name"))
                if not api_name:
                    continue
                expression = "".join(
                    r"[^.()\s]+" if part.startswith("<") else re.escape(part)
                    for part in re.split(r"(<[^>]+>)", api_name)
                    if part
                )
                patterns.append((re.compile(rf"^{expression}$"), entry))
    patterns.sort(key=lambda item: len(item[0].pattern), reverse=True)
    return patterns


def _api_from_call(call: Mapping[str, Any]) -> str:
    explicit = _text(call.get("api"))
    if explicit:
        return explicit
    request = _text(call.get("request") or call.get("submitted_request"))
    match = API_RE.search(request)
    return match.group(1) if match else ""


def _entry_from_api(
    api: str,
    mappings: list[tuple[re.Pattern[str], str]],
) -> str:
    for pattern, entry in mappings:
        if pattern.fullmatch(api):
            return entry
    parts = api.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else ""


def _catalog_signature(call: Mapping[str, Any]) -> str:
    subject = _text(call.get("subject"))
    dataview = _text(call.get("dataview"))
    operation = _text(call.get("operation"))
    if not subject and not dataview:
        return "*"
    if dataview.startswith(subject + "."):
        dataview = dataview.split(".", 1)[1]
    route = f"{subject}.{dataview}" if subject and dataview else f"{subject or '*' }.*"
    return f"{route}:{operation or '?'}"


def _error_text(case: Mapping[str, Any], financial: Mapping[str, Any]) -> str:
    error = case.get("error") or financial.get("error")
    if isinstance(error, Mapping):
        return _text(error.get("message") or error.get("type"))
    return _text(error)


def _run_row(
    case: Mapping[str, Any],
    mappings: list[tuple[re.Pattern[str], str]],
) -> dict[str, Any]:
    financial = _mapping(case.get("financial_qa"))
    usage = _mapping(case.get("llm_usage"))
    calls = [dict(item) for item in financial.get("tool_calls") or [] if isinstance(item, Mapping)]
    catalog_calls = [item for item in calls if _text(item.get("tool")) == "read_finance_catalog"]
    query_calls = [item for item in calls if _text(item.get("tool")) == "finance_query"]
    load_calls = [item for item in calls if _text(item.get("tool")) == "load_finance_result"]
    catalog_sequence = [_catalog_signature(item) for item in catalog_calls]
    concrete_catalog_entries = [item.split(":", 1)[0] for item in catalog_sequence if "." in item and not item.startswith("*.")]
    api_sequence = [_api_from_call(item) for item in query_calls]
    api_sequence = [item for item in api_sequence if item]
    api_entries = [_entry_from_api(item, mappings) for item in api_sequence]
    observed_entries: list[str] = []
    for entry in [*concrete_catalog_entries, *api_entries]:
        if entry and entry not in observed_entries:
            observed_entries.append(entry)
    source = _mapping(case.get("source_case"))
    acceptable = {_text(item) for item in source.get("acceptable_first_entries") or [] if _text(item)}
    required = {_text(item) for item in source.get("required_entries") or [] if _text(item)}
    first_entry = observed_entries[0] if observed_entries else ""
    loop_policy = _mapping(financial.get("loop_policy"))
    requests = [dict(item) for item in loop_policy.get("requests") or [] if isinstance(item, Mapping)]
    cap_hits = [item for item in requests if bool(item.get("max_token_hit"))]
    prompt_requests = [item for item in requests if "prompt_injected" in item]
    refs = [dict(item) for item in financial.get("result_refs") or [] if isinstance(item, Mapping)]
    answer = _text(_mapping(case.get("done_result")).get("message"))
    return {
        "status": _text(case.get("status")),
        "error": _error_text(case, financial),
        "model": _text(_mapping(case.get("done_result")).get("model_name")),
        "reasoning_effort": _text(financial.get("reasoning_effort")),
        "total_elapsed_ms": float(case.get("total_elapsed_ms") or 0),
        "runtime_duration_ms": float(financial.get("duration_ms") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cumulative_context_tokens": int(usage.get("cumulative_context_tokens") or 0),
        "mean_context_tokens_per_call": float(usage.get("mean_context_tokens_per_call") or 0),
        "max_context_tokens_per_call": int(usage.get("max_context_tokens_per_call") or 0),
        "final_context_tokens": int(usage.get("final_context_tokens") or 0),
        "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
        "non_reasoning_completion_tokens": int(usage.get("non_reasoning_completion_tokens") or 0),
        "model_call_count": int(usage.get("call_count") or 0),
        "tool_call_count": len(calls),
        "catalog_read_count": len(catalog_calls),
        "finance_query_count": len(query_calls),
        "result_load_count": len(load_calls),
        "catalog_sequence": catalog_sequence,
        "catalog_operation_sequence": [_text(item.get("operation")) or "?" for item in catalog_calls],
        "first_catalog_entry": concrete_catalog_entries[0] if concrete_catalog_entries else "",
        "first_data_api": api_sequence[0] if api_sequence else "",
        "api_sequence": api_sequence,
        "api_set": sorted(set(api_sequence)),
        "entry_sequence": observed_entries,
        "first_entry": first_entry,
        "first_entry_correct": bool(first_entry and first_entry in acceptable),
        "eventual_acceptable": bool(set(observed_entries).intersection(acceptable)),
        "all_required_entries": required.issubset(set(observed_entries)),
        "zero_row_query_count": sum(
            1 for item in query_calls if "row_count" in item and int(item.get("row_count") or 0) == 0
        ),
        "result_ref_count": len(refs),
        "result_row_count": sum(int(item.get("row_count") or 0) for item in refs),
        "max_token_hit_count": len(cap_hits),
        "max_token_reasoning_tokens": sum(int(item.get("reasoning_tokens") or 0) for item in cap_hits),
        "max_token_visible_tokens": sum(int(item.get("visible_output_tokens") or 0) for item in cap_hits),
        "prompt_observed_request_count": len(prompt_requests),
        "prompt_injected_request_count": sum(bool(item.get("prompt_injected")) for item in prompt_requests),
        "prompt_injection_rate": (
            round(sum(bool(item.get("prompt_injected")) for item in prompt_requests) / len(prompt_requests), 6)
            if prompt_requests
            else None
        ),
        "stage_trace": [
            {
                key: item.get(key)
                for key in (
                    "request_index", "stage", "stage_reason", "reasoning_effort",
                    "max_tokens", "context_tokens", "output_tokens", "reasoning_tokens",
                    "visible_output_tokens", "max_token_hit", "prompt_injected",
                    "prompt_surface", "prompt_sha256", "called_tools",
                )
            }
            for item in requests
        ],
        "query_requests": [
            _text(item.get("request") or item.get("submitted_request"))
            for item in query_calls
        ],
        "answer": answer,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = (
        "total_elapsed_ms", "runtime_duration_ms", "prompt_tokens",
        "completion_tokens", "total_tokens", "cache_read_tokens",
        "cumulative_context_tokens", "reasoning_tokens",
        "non_reasoning_completion_tokens", "model_call_count", "tool_call_count",
        "catalog_read_count", "finance_query_count", "result_load_count",
        "max_token_hit_count",
    )
    return {
        "case_count": len(rows),
        "ok_count": sum(item["status"] == "ok" for item in rows),
        "error_count": sum(item["status"] != "ok" for item in rows),
        "models": sorted({item["model"] for item in rows if item["model"]}),
        "first_entry_correct_count": sum(item["first_entry_correct"] for item in rows),
        "eventual_acceptable_count": sum(item["eventual_acceptable"] for item in rows),
        "all_required_entries_count": sum(item["all_required_entries"] for item in rows),
        "zero_row_case_count": sum(item["zero_row_query_count"] > 0 for item in rows),
        "prompt_observed_request_count": sum(item["prompt_observed_request_count"] for item in rows),
        "prompt_injected_request_count": sum(item["prompt_injected_request_count"] for item in rows),
        "metrics": {
            key: _stats([float(item[key]) for item in rows])
            for key in numeric
        },
    }


def _pairwise(
    rows: list[dict[str, Any]], left: str, right: str,
) -> dict[str, Any]:
    both_have_api = [row for row in rows if row["runs"][left]["api_sequence"] and row["runs"][right]["api_sequence"]]
    latency_delta = [
        row["runs"][right]["total_elapsed_ms"] - row["runs"][left]["total_elapsed_ms"]
        for row in rows
    ]
    token_delta = [
        row["runs"][right]["total_tokens"] - row["runs"][left]["total_tokens"]
        for row in rows
    ]
    return {
        "left": left,
        "right": right,
        "same_first_data_api_count": sum(
            row["runs"][left]["first_data_api"] == row["runs"][right]["first_data_api"]
            and bool(row["runs"][left]["first_data_api"])
            for row in rows
        ),
        "same_api_set_count": sum(
            row["runs"][left]["api_set"] == row["runs"][right]["api_set"]
            and bool(row["runs"][left]["api_set"])
            for row in rows
        ),
        "same_api_sequence_count": sum(
            row["runs"][left]["api_sequence"] == row["runs"][right]["api_sequence"]
            and bool(row["runs"][left]["api_sequence"])
            for row in rows
        ),
        "both_have_api_count": len(both_have_api),
        "right_faster_count": sum(value < 0 for value in latency_delta),
        "right_lower_token_count": sum(value < 0 for value in token_delta),
        "latency_delta_ms": _stats(latency_delta),
        "reported_total_token_delta": _stats(token_delta),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cc", type=Path, required=True)
    parser.add_argument("--dsh-low", type=Path, required=True)
    parser.add_argument("--dsh-opt", type=Path, required=True)
    parser.add_argument("--dsh-opt-first-pass", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = _load(args.cases)
    source_by_id = {str(item["case_id"]): item for item in source.get("cases") or []}
    catalog = _load(args.catalog)
    mappings = _compile_api_mapping(catalog)
    paths = {"cc": args.cc, "dsh_low": args.dsh_low, "dsh_opt": args.dsh_opt}
    payloads = {name: _load(path) for name, path in paths.items()}
    case_maps = {
        name: {str(item["case_id"]): item for item in payload.get("cases") or []}
        for name, payload in payloads.items()
    }
    common_ids = set.intersection(*(set(items) for items in case_maps.values()))
    selected_ids = [
        str(item["case_id"])
        for item in payloads["cc"].get("cases") or []
        if str(item["case_id"]) in common_ids
    ]
    rows: list[dict[str, Any]] = []
    for case_id in selected_ids:
        source_case = source_by_id[case_id]
        runs = {
            name: _run_row(case_maps[name][case_id], mappings)
            for name in paths
        }
        first_apis = [runs[name]["first_data_api"] for name in paths]
        api_sets = [runs[name]["api_set"] for name in paths]
        rows.append(
            {
                "case_id": case_id,
                "question": _text(source_case.get("question")),
                "category": _text(source_case.get("category")),
                "primary_entry": _text(source_case.get("primary_entry")),
                "acceptable_first_entries": list(source_case.get("acceptable_first_entries") or []),
                "required_entries": list(source_case.get("required_entries") or []),
                "runs": runs,
                "all_three_same_first_data_api": bool(first_apis[0] and len(set(first_apis)) == 1),
                "all_three_same_api_set": bool(api_sets[0] and api_sets[0] == api_sets[1] == api_sets[2]),
            }
        )

    first_pass = _load(args.dsh_opt_first_pass) if args.dsh_opt_first_pass else {}
    first_pass_cases = list(first_pass.get("cases") or [])
    prompt_assets = {
        "global_system": {
            "path": "src/scenarios/financial_qa/dsh_system.md",
            "sha256": __import__("hashlib").sha256(
                Path("src/scenarios/financial_qa/dsh_system.md").read_bytes()
            ).hexdigest(),
        },
        "stage_policy": {
            "path": "src/scenarios/financial_qa/dsh_loop_policy.mjs",
            "sha256": __import__("hashlib").sha256(
                Path("src/scenarios/financial_qa/dsh_loop_policy.mjs").read_bytes()
            ).hexdigest(),
        },
    }
    output = {
        "analysis": "financial_qa_cc_dsh_low_opt_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": {
            "seed": args.seed,
            "source_case_path": str(args.cases.resolve()),
            "source_candidate_count": len(source_by_id),
            "sampling_pool_rule": "currently-supported mainland-listed-company cases, excluding any stock.news requirement",
            "excluded_news_case_ids": ["RTE006", "RTE010", "RTE060"],
            "sample_size": len(rows),
            "case_ids": selected_ids,
            "concurrency": 3,
            "per_case_timeout_seconds": 600,
            "models": {name: _summary([row["runs"][name] for row in rows])["models"] for name in paths},
            "configurations": {
                "cc": "CC financial QA, effort=low; no explicit max_tokens in ClaudeAgentOptions",
                "dsh_low": "deepseek-v4-flash, reasoning=low, max_tokens=8192, loop policy disabled",
                "dsh_opt": "deepseek-v4-flash, current stage-policy orchestration defaults",
            },
            "prompt_assets": prompt_assets,
            "sources": {name: str(path.resolve()) for name, path in paths.items()},
        },
        "summaries": {
            name: _summary([row["runs"][name] for row in rows])
            for name in paths
        },
        "pairwise": {
            "cc_vs_dsh_low": _pairwise(rows, "cc", "dsh_low"),
            "cc_vs_dsh_opt": _pairwise(rows, "cc", "dsh_opt"),
            "dsh_low_vs_dsh_opt": _pairwise(rows, "dsh_low", "dsh_opt"),
        },
        "all_three": {
            "same_first_data_api_count": sum(row["all_three_same_first_data_api"] for row in rows),
            "same_api_set_count": sum(row["all_three_same_api_set"] for row in rows),
        },
        "dsh_opt_first_pass": {
            "source": str(args.dsh_opt_first_pass.resolve()) if args.dsh_opt_first_pass else "",
            "case_count": len(first_pass_cases),
            "ok_count": sum(item.get("status") == "ok" for item in first_pass_cases),
            "error_count": sum(item.get("status") != "ok" for item in first_pass_cases),
            "max_token_case_ids": [
                str(item.get("case_id"))
                for item in first_pass_cases
                if "max-tokens" in _error_text(item, _mapping(item.get("financial_qa")))
            ],
        },
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "summaries": output["summaries"],
        "pairwise": output["pairwise"],
        "all_three": output["all_three"],
        "dsh_opt_first_pass": output["dsh_opt_first_pass"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
