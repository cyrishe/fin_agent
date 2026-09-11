#!/usr/bin/env python3
"""Combine CC first-entry traces with catalog embedding retrieval results."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "cases_seed20260831_n100.json"
)
DEFAULT_CC = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "deepseek_v4_flash_results.json"
)
DEFAULT_EMBEDDING = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "qwen37_embedding_top3.json"
)
DEFAULT_CATALOG = ROOT / "src" / "tools" / "finance_data" / "catalog" / "api_view_catalog.json"
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "analysis.json"
)
API_RE = re.compile(r"(?:^|[;\n])\s*(?:[A-Za-z_]\w*\s*=\s*)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pct(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[min(len(ordered) - 1, max(0, index))]


def _stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": round(mean(values), 3) if values else None,
        "median": round(median(values), 3) if values else None,
        "p95": round(_pct(values, 0.95), 3) if values else None,
        "sum": round(sum(values), 3) if values else 0.0,
    }


def _compile_api_mapping(catalog: Mapping[str, Any]) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for subject, subject_body in (catalog.get("subjects") or {}).items():
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
    request = _text(call.get("request") or call.get("submitted_request"))
    match = API_RE.search(request)
    return match.group(1) if match else ""


def _entry_from_api(api: str, mappings: list[tuple[re.Pattern[str], str]]) -> tuple[str, str]:
    for pattern, entry in mappings:
        if pattern.fullmatch(api):
            return entry, "catalog_mapping"
    fallback = re.fullmatch(
        r"(stock|index|industry|plate|fund|bond|hot_event)\.([a-z][a-z0-9_]*)"
        r"(?:\..+)?",
        api,
    )
    if fallback:
        candidate = f"{fallback.group(1)}.{fallback.group(2)}"
        if candidate in {entry for _, entry in mappings}:
            return candidate, "fallback_known_entry"
        return f"invalid_api:{api}", "unregistered"
    return "", "failed"


def _catalog_attempt(call: Mapping[str, Any]) -> tuple[str, str]:
    subject = _text(call.get("subject"))
    dataview = _text(call.get("dataview"))
    if subject and dataview:
        if dataview.startswith(subject + "."):
            dataview = dataview.split(".", 1)[1]
        return f"{subject}.{dataview}", "concrete"
    if subject:
        return f"{subject}.*", "subject_browse"
    return "*", "index_browse"


def _entry_trace(
    tool_calls: list[dict[str, Any]], mappings: list[tuple[re.Pattern[str], str]]
) -> dict[str, Any]:
    known_entries = {entry for _, entry in mappings}
    first_catalog_attempt = ""
    first_concrete_catalog_entry = ""
    first_data_api = ""
    first_data_entry = ""
    first_data_parse_status = ""
    observed: list[dict[str, Any]] = []
    catalog_browse_count = 0
    for call_index, call in enumerate(tool_calls, start=1):
        tool = _text(call.get("tool"))
        if tool == "read_finance_catalog":
            entry, mode = _catalog_attempt(call)
            catalog_browse_count += 1
            if not first_catalog_attempt:
                first_catalog_attempt = entry
            if mode == "concrete":
                if entry not in known_entries:
                    entry = f"invalid_catalog:{entry}"
                if not first_concrete_catalog_entry:
                    first_concrete_catalog_entry = entry
                observed.append(
                    {
                        "call_index": call_index,
                        "source": "catalog",
                        "entry": entry,
                    }
                )
            continue
        if tool != "finance_query":
            continue
        api = _api_from_call(call)
        entry, parse_status = _entry_from_api(api, mappings) if api else ("", "failed")
        if not first_data_api:
            first_data_api = api
            first_data_entry = entry
            first_data_parse_status = parse_status
        if entry:
            observed.append(
                {
                    "call_index": call_index,
                    "source": "data_api",
                    "entry": entry,
                    "api": api,
                    "parse_status": parse_status,
                }
            )
    first = observed[0] if observed else {}
    entry_sequence: list[str] = []
    for item in observed:
        entry = item["entry"]
        if not entry_sequence or entry_sequence[-1] != entry:
            entry_sequence.append(entry)
    return {
        "first_catalog_attempt": first_catalog_attempt,
        "first_concrete_catalog_entry": first_concrete_catalog_entry,
        "first_data_api": first_data_api,
        "first_data_entry": first_data_entry,
        "first_data_parse_status": first_data_parse_status,
        "first_entry": _text(first.get("entry")),
        "first_entry_source": _text(first.get("source")),
        "first_entry_call_index": first.get("call_index"),
        "catalog_browse_count": catalog_browse_count,
        "entry_sequence": entry_sequence,
        "entry_switch_count": max(0, len(entry_sequence) - 1),
        "observed_entries": observed,
    }


def _compact_answer(case: Mapping[str, Any]) -> str:
    done = case.get("done_result") or {}
    return _text(done.get("message") or done.get("answer_summary"))


def _analyze_cc_case(
    case: Mapping[str, Any],
    gold: Mapping[str, Any],
    mappings: list[tuple[re.Pattern[str], str]],
) -> dict[str, Any]:
    financial = case.get("financial_qa") or {}
    tool_calls = [dict(item) for item in financial.get("tool_calls") or [] if isinstance(item, Mapping)]
    finance_calls = [item for item in tool_calls if _text(item.get("tool")) == "finance_query"]
    trace = _entry_trace(tool_calls, mappings)
    acceptable = set(gold.get("acceptable_first_entries") or [])
    required = set(gold.get("required_entries") or [])
    first_entry = trace["first_entry"]
    first_catalog = trace["first_concrete_catalog_entry"]
    first_data = trace["first_data_entry"]
    observed_entry_set = set(trace["entry_sequence"])
    validation_failures = [item for item in finance_calls if item.get("validation_errors")]
    execution_failures = [
        item for item in finance_calls if item.get("execution_error") or item.get("error")
    ]
    api_execution_ms = sum(float(item.get("api_execution_ms") or 0.0) for item in finance_calls)
    static_validation_ms = sum(float(item.get("static_validation_ms") or 0.0) for item in finance_calls)
    cc_duration_ms = float(financial.get("duration_ms") or 0.0)
    llm_non_api_estimate_ms = max(0.0, cc_duration_ms - api_execution_ms)
    api_sequence = [_api_from_call(item) for item in finance_calls]
    api_sequence = [api for api in api_sequence if api]
    return {
        "case_id": case.get("case_id"),
        "source_ordinal": gold.get("source_ordinal"),
        "question": gold.get("question") or case.get("question"),
        "category": gold.get("category"),
        "data_shape": gold.get("data_shape"),
        "primary_entry": gold.get("primary_entry"),
        "acceptable_first_entries": list(gold.get("acceptable_first_entries") or []),
        "required_entries": list(gold.get("required_entries") or []),
        "cc_status": case.get("status"),
        "model_name": _text((case.get("done_result") or {}).get("model_name")),
        **trace,
        "first_entry_correct": first_entry in acceptable if first_entry else False,
        "first_entry_is_primary": first_entry == gold.get("primary_entry") if first_entry else False,
        "catalog_first_entry_correct": first_catalog in acceptable if first_catalog else None,
        "first_data_entry_correct": first_data in acceptable if first_data else None,
        "entry_immediate": bool(trace["first_entry_call_index"] == 1 and first_entry),
        "eventual_acceptable_entry_found": bool(observed_entry_set.intersection(acceptable)),
        "all_required_entries_found": required.issubset(observed_entry_set),
        "entry_recovered": bool(
            first_entry and first_entry not in acceptable and observed_entry_set.intersection(acceptable)
        ),
        "total_elapsed_ms": float(case.get("total_elapsed_ms") or 0.0),
        "cc_duration_ms": cc_duration_ms,
        "api_execution_ms": round(api_execution_ms, 3),
        "static_validation_ms": round(static_validation_ms, 3),
        "llm_non_api_estimate_ms": round(llm_non_api_estimate_ms, 3),
        "assistant_message_count": int(financial.get("assistant_message_count") or 0),
        "tool_result_message_count": int(financial.get("tool_result_message_count") or 0),
        "meaningful_round_proxy": int(financial.get("tool_result_message_count") or 0) + 1,
        "tool_call_count": len(tool_calls),
        "finance_query_count": len(finance_calls),
        "static_failure_count": len(validation_failures),
        "execution_failure_count": len(execution_failures),
        "zero_row_query_count": sum(
            1 for item in finance_calls if "row_count" in item and int(item.get("row_count") or 0) == 0
        ),
        "api_sequence": api_sequence,
        "api_requests": [
            {
                "goal": _text(item.get("goal")),
                "api": _api_from_call(item),
                "submitted_request": _text(item.get("submitted_request")),
                "normalized_request": _text(item.get("request")),
                "validation_errors": list(item.get("validation_errors") or []),
                "execution_error": _text(item.get("execution_error") or item.get("error")),
                "static_validation_ms": float(item.get("static_validation_ms") or 0.0),
                "api_execution_ms": float(item.get("api_execution_ms") or 0.0),
                "row_count": item.get("row_count"),
            }
            for item in finance_calls
        ],
        "answer": _compact_answer(case),
        "error": case.get("error") or financial.get("error"),
    }


def _group_summary(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_text(row.get(field)) or "<none>"].append(row)
    output: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        output.append(
            {
                field: key,
                "case_count": len(items),
                "first_entry_correct_count": sum(item["first_entry_correct"] for item in items),
                "first_entry_correct_rate": round(
                    sum(item["first_entry_correct"] for item in items) / len(items), 6
                ),
                "eventual_correct_count": sum(item["eventual_acceptable_entry_found"] for item in items),
                "eventual_correct_rate": round(
                    sum(item["eventual_acceptable_entry_found"] for item in items) / len(items), 6
                ),
                "total_elapsed_ms": _stats([item["total_elapsed_ms"] for item in items]),
                "cc_duration_ms": _stats([item["cc_duration_ms"] for item in items]),
                "api_execution_ms": _stats([item["api_execution_ms"] for item in items]),
                "assistant_message_count": _stats(
                    [item["assistant_message_count"] for item in items]
                ),
            }
        )
    return output


def _threshold_rows(embedding_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for score_threshold in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55):
        for margin_threshold in (0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.08, 0.10):
            selected = [
                row
                for row in embedding_rows
                if float(row["top1"]["score"]) >= score_threshold
                and float(row["top1_margin"]) >= margin_threshold
            ]
            if not selected:
                continue
            output.append(
                {
                    "score_threshold": score_threshold,
                    "margin_threshold": margin_threshold,
                    "selected_count": len(selected),
                    "coverage": round(len(selected) / len(embedding_rows), 6),
                    "top1_precision": round(
                        sum(row["acceptable_hit_at_1"] for row in selected)
                        / len(selected),
                        6,
                    ),
                    "top2_recall_within_selected": round(
                        sum(row["acceptable_hit_at_2"] for row in selected)
                        / len(selected),
                        6,
                    ),
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--cc-results", type=Path, default=DEFAULT_CC)
    parser.add_argument("--embedding-results", type=Path, default=DEFAULT_EMBEDDING)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    gold_payload = json.loads(args.cases.read_text(encoding="utf-8"))
    gold_cases = list(gold_payload.get("cases") or [])
    gold_by_id = {item["case_id"]: item for item in gold_cases}
    cc_payload = json.loads(args.cc_results.read_text(encoding="utf-8"))
    cc_cases = list(cc_payload.get("cases") or [])
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    mappings = _compile_api_mapping(catalog)
    cc_rows = [
        _analyze_cc_case(case, gold_by_id[case["case_id"]], mappings)
        for case in cc_cases
        if case.get("case_id") in gold_by_id
    ]
    cc_rows.sort(key=lambda item: item["case_id"])

    embedding_payload = json.loads(args.embedding_results.read_text(encoding="utf-8"))
    embedding_rows = list(embedding_payload.get("cases") or [])
    embedding_by_id = {item["case_id"]: item for item in embedding_rows}

    merged_rows: list[dict[str, Any]] = []
    for cc_row in cc_rows:
        embedding = embedding_by_id.get(cc_row["case_id"], {})
        merged_rows.append(
            {
                **cc_row,
                "embedding_top1": _text((embedding.get("top1") or {}).get("entry")),
                "embedding_top1_score": (embedding.get("top1") or {}).get("score"),
                "embedding_top1_margin": embedding.get("top1_margin"),
                "embedding_top2_entries": [
                    item.get("entry") for item in embedding.get("top2") or []
                ],
                "embedding_top3_entries": [
                    item.get("entry") for item in embedding.get("top3") or []
                ],
                "embedding_hit_at_1": embedding.get("acceptable_hit_at_1"),
                "embedding_hit_at_2": embedding.get("acceptable_hit_at_2"),
                "embedding_hit_at_3": embedding.get("acceptable_hit_at_3"),
                "embedding_required_coverage_at_2": embedding.get(
                    "required_coverage_at_2"
                ),
            }
        )

    first_correct = [row for row in cc_rows if row["first_entry_correct"]]
    first_wrong = [row for row in cc_rows if not row["first_entry_correct"]]
    silent_wrong = [
        row
        for row in first_wrong
        if row["static_failure_count"] == 0
        and row["execution_failure_count"] == 0
        and any(
            int(request.get("row_count") or 0) > 0
            for request in row.get("api_requests") or []
        )
    ]
    confusion = Counter(
        (row["primary_entry"], row["first_entry"] or "<none>")
        for row in first_wrong
    )
    comparison = Counter()
    for row in merged_rows:
        cc_ok = bool(row["first_entry_correct"])
        emb_ok = bool(row["embedding_hit_at_2"])
        comparison[
            "both_correct"
            if cc_ok and emb_ok
            else "cc_only"
            if cc_ok
            else "embedding_only"
            if emb_ok
            else "neither"
        ] += 1

    cc_summary = {
        "case_count": len(cc_rows),
        "completed_case_count": sum(row["cc_status"] == "ok" for row in cc_rows),
        "model_names": dict(Counter(row["model_name"] for row in cc_rows)),
        "first_entry_observed_count": sum(bool(row["first_entry"]) for row in cc_rows),
        "first_entry_correct_count": len(first_correct),
        "first_entry_correct_rate": round(len(first_correct) / len(cc_rows), 6)
        if cc_rows
        else None,
        "eventual_acceptable_entry_count": sum(
            row["eventual_acceptable_entry_found"] for row in cc_rows
        ),
        "eventual_acceptable_entry_rate": round(
            sum(row["eventual_acceptable_entry_found"] for row in cc_rows)
            / len(cc_rows),
            6,
        )
        if cc_rows
        else None,
        "entry_recovered_count": sum(row["entry_recovered"] for row in cc_rows),
        "first_wrong_without_static_or_execution_failure_count": sum(
            row["static_failure_count"] == 0 and row["execution_failure_count"] == 0
            for row in first_wrong
        ),
        "first_wrong_silent_nonempty_count": len(silent_wrong),
        "first_wrong_silent_nonempty_never_recovered_count": sum(
            not row["eventual_acceptable_entry_found"] for row in silent_wrong
        ),
        "catalog_first_correct_count": sum(
            row["catalog_first_entry_correct"] is True for row in cc_rows
        ),
        "data_api_first_correct_count": sum(
            row["first_data_entry_correct"] is True for row in cc_rows
        ),
        "all_cases_total_elapsed_ms": _stats(
            [row["total_elapsed_ms"] for row in cc_rows]
        ),
        "all_cases_cc_duration_ms": _stats([row["cc_duration_ms"] for row in cc_rows]),
        "all_cases_llm_non_api_estimate_ms": _stats(
            [row["llm_non_api_estimate_ms"] for row in cc_rows]
        ),
        "all_cases_api_execution_ms": _stats(
            [row["api_execution_ms"] for row in cc_rows]
        ),
        "first_correct_total_elapsed_ms": _stats(
            [row["total_elapsed_ms"] for row in first_correct]
        ),
        "first_wrong_total_elapsed_ms": _stats(
            [row["total_elapsed_ms"] for row in first_wrong]
        ),
        "first_correct_cc_duration_ms": _stats(
            [row["cc_duration_ms"] for row in first_correct]
        ),
        "first_wrong_cc_duration_ms": _stats(
            [row["cc_duration_ms"] for row in first_wrong]
        ),
        "first_correct_assistant_messages": _stats(
            [row["assistant_message_count"] for row in first_correct]
        ),
        "first_wrong_assistant_messages": _stats(
            [row["assistant_message_count"] for row in first_wrong]
        ),
        "static_failure_case_count": sum(row["static_failure_count"] > 0 for row in cc_rows),
        "execution_failure_case_count": sum(
            row["execution_failure_count"] > 0 for row in cc_rows
        ),
        "first_entry_confusions": [
            {"gold_primary_entry": gold, "first_entry": predicted, "count": count}
            for (gold, predicted), count in confusion.most_common()
        ],
    }

    threshold_rows = _threshold_rows(embedding_rows)
    high_precision_candidates = [
        row for row in threshold_rows if row["top1_precision"] >= 0.90
    ]
    best_high_precision = (
        max(high_precision_candidates, key=lambda row: row["selected_count"])
        if high_precision_candidates
        else None
    )
    output = {
        "experiment": "cc_vs_catalog_embedding_routing_v1",
        "generated_at": _now_iso(),
        "sources": {
            "cases": str(args.cases.resolve()),
            "cc_results": str(args.cc_results.resolve()),
            "embedding_results": str(args.embedding_results.resolve()),
            "catalog": str(args.catalog.resolve()),
        },
        "sampling": {key: value for key, value in gold_payload.items() if key != "cases"},
        "label_definition": {
            "first_entry": (
                "earliest concrete read_finance_catalog entry or recognized finance_query "
                "entry in ordered financial_qa.tool_calls"
            ),
            "correct": "first_entry belongs to human-reviewed acceptable_first_entries",
            "index_browse": (
                "catalog reads without a concrete dataview are retained as navigation but "
                "are not mislabeled as a wrong business entry"
            ),
            "observation_limit": (
                "tool uses rejected before entering a tool handler are not present in the "
                "current financial_qa.tool_calls trace"
            ),
        },
        "cc_summary": cc_summary,
        "cc_by_category": _group_summary(cc_rows, "category"),
        "cc_by_first_entry_correct": _group_summary(cc_rows, "first_entry_correct"),
        "embedding_summary": embedding_payload.get("summary") or {},
        "embedding_thresholds": threshold_rows,
        "best_embedding_gate_at_90pct_precision": best_high_precision,
        "cc_vs_embedding_top2": dict(comparison),
        "cases": merged_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "cc_summary": cc_summary,
        "embedding_summary": output["embedding_summary"],
        "cc_vs_embedding_top2": output["cc_vs_embedding_top2"],
        "best_embedding_gate_at_90pct_precision": best_high_precision,
    }, ensure_ascii=False, indent=2))
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
