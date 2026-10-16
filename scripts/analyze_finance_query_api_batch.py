#!/usr/bin/env python3
"""Post-process the real financial-query API benchmark into ``analysis.json``.

This module is intentionally an *observer*.  It does not call the application,
change prompts, or modify runtime state.  It joins three independently useful
evidence sources:

* client-side HTTP/SSE observations from ``eval_finance_query_api_batch.py``;
* provider/harness turn records from ``outputs/financial_qa_cc/events.jsonl``;
* benchmark expectations and the previous baseline workbook extraction.

The output separates measured values, client-estimated stage intervals, and
heuristic audits.  In particular, semantic answer correctness is never claimed
from these mechanical checks alone.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = (
    ROOT / "outputs" / "d4f10504-8df6-435e-9316-3d89b5fd1015"
)
DEFAULT_BATCH = DEFAULT_DIR / "finance_query_api_batch_results.json"
DEFAULT_SOURCE = DEFAULT_DIR / "source_cases.json"
DEFAULT_BASELINE = DEFAULT_DIR / "old_baseline_cases.json"
DEFAULT_PROVIDER_EVENTS = ROOT / "outputs" / "financial_qa_cc" / "events.jsonl"
DEFAULT_OUTPUT = DEFAULT_DIR / "analysis.json"

CATEGORIES = (
    "STRUCTURAL_EFFECTIVE",
    "SEMANTIC_NECESSARY",
    "REASONABLE_RECOVERY",
    "DESIGN_WASTE",
    "UNKNOWN",
)
TERMINAL_PUNCTUATION = set("。！？.!?；;）)]】》」』\"'`")
GENERIC_INABILITY_MARKERS = (
    "无法直接访问实时数据",
    "无法访问实时数据",
    "无法进行网络搜索",
    "作为通用助手",
    "不能直接查询",
)
NO_DATA_MARKERS = (
    "未查询到",
    "没有查询到",
    "暂无数据",
    "无数据",
    "没有符合",
    "未找到",
    "0 条",
    "0条",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                # An actively appended JSONL file may expose its final partial
                # line.  Earlier valid provider evidence remains usable.
                continue
            if isinstance(value, Mapping):
                record = dict(value)
                record["_source_line"] = line_number
                records.append(record)
    return records


def _percentile(values: Iterable[float | int | None], percentile: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 2)
    rank = (len(clean) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(clean[lower], 2)
    value = clean[lower] + (clean[upper] - clean[lower]) * (rank - lower)
    return round(value, 2)


def _mean(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return round(sum(clean) / len(clean), 2) if clean else None


def _distribution(values: Iterable[float | int | None]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    return {
        "count": len(clean),
        "min": round(min(clean), 2) if clean else None,
        "mean": _mean(clean),
        "p50": _percentile(clean, 0.50),
        "p90": _percentile(clean, 0.90),
        "p95": _percentile(clean, 0.95),
        "max": round(max(clean), 2) if clean else None,
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


def _parse_expected_dataviews(value: Any) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", _text(value))
        )
    )


def _api_from_request(request: Any) -> str:
    match = re.search(
        r"(?:^|=)\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\s*\(",
        _text(request),
    )
    return match.group(1) if match else ""


def _api_matches_expected(actual: str, expected: str) -> bool:
    """Treat a documented derived operator as coverage of its base dataview.

    For example ``stock.quote.kd_amplitude_avg`` is a specialized operator
    exposed under ``stock.quote``.  The benchmark expectation stores the base
    dataview, so exact-only comparison would create a false coverage failure.
    """

    return actual == expected or actual.startswith(f"{expected}.")


def _normalize_query(request: Any) -> str:
    text = _text(request).lower()
    text = re.sub(r"^\s*(?:result|r\d+)\s*=\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_scalar(value: Any, *, field: str = "") -> str:
    text = _text(value).strip("'\"").lower()
    if field.lower() in {"code", "stock_code", "fund_code", "bond_code"}:
        text = re.sub(r"\.(?:sh|sz|bj|csi)$", "", text)
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        try:
            number = float(text)
            return str(int(number)) if number.is_integer() else f"{number:.12g}"
        except ValueError:
            pass
    return text.replace("/", "-")


def _equality_predicates(filter_text: Any) -> list[tuple[str, str]]:
    text = _text(filter_text)
    pattern = re.compile(
        r"(?<![<>!])\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"(?:'([^']*)'|\"([^\"]*)\"|([^\s,)]+))",
        flags=re.IGNORECASE,
    )
    predicates: list[tuple[str, str]] = []
    for match in pattern.finditer(text):
        value = next(
            (part for part in match.groups()[1:] if part is not None), ""
        )
        predicates.append((match.group(1), value))
    return predicates


def _sample_rows(result_ref: Mapping[str, Any]) -> list[dict[str, Any]]:
    sample = _mapping(result_ref.get("sample"))
    return _list_of_mappings(sample.get("rows"))


def _selection_sample_conflicts(result_ref: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence = _mapping(result_ref.get("step_evidence"))
    selection = _mapping(evidence.get("selection_applied"))
    predicates = _equality_predicates(selection.get("filter"))
    rows = _sample_rows(result_ref)
    conflicts: list[dict[str, Any]] = []
    for field, expected in predicates:
        comparable = [row.get(field) for row in rows if field in row]
        if not comparable:
            continue
        normalized_expected = _normalize_scalar(expected, field=field)
        matches = [
            _normalize_scalar(actual, field=field) == normalized_expected
            for actual in comparable
        ]
        if matches and not any(matches):
            conflicts.append(
                {
                    "field": field,
                    "predicate_value": expected,
                    "sample_values": comparable[:5],
                    "sample_complete": bool(
                        result_ref.get("sample_complete")
                        or evidence.get("sample_complete")
                    ),
                }
            )
    return conflicts


def _provider_events_by_session(
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        session_id = _text(raw.get("session_id"))
        if session_id:
            grouped[session_id].append(dict(raw))
    selected: dict[str, dict[str, Any]] = {}
    multiplicity: dict[str, int] = {}
    for session_id, items in grouped.items():
        # A session should have one terminal turn record.  If append/replay
        # produced duplicates, prefer the latest record instead of summing and
        # accidentally double-counting transport requests.
        items.sort(key=lambda item: (_text(item.get("timestamp")), int(item.get("_source_line") or 0)))
        selected[session_id] = items[-1]
        multiplicity[session_id] = len(items)
    return selected, multiplicity


def _result_ref_for_tool(
    tool: Mapping[str, Any], result_refs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    request = _normalize_query(tool.get("request") or tool.get("submitted_request"))
    if request:
        exact = [
            dict(ref)
            for ref in result_refs
            if _normalize_query(ref.get("request")) == request
        ]
        if len(exact) == 1:
            return exact[0]
        # Result names may be reused after a failed attempt (for example three
        # consecutive calls all expecting ``r1``).  Once a concrete request is
        # observable, falling back to the name would attach a later success to
        # the earlier failed operation and corrupt row/sample evidence.
        return {}
    result_name = _text(tool.get("result_name") or tool.get("assigned_result_name"))
    if result_name:
        by_name = [
            dict(ref) for ref in result_refs if _text(ref.get("result_name")) == result_name
        ]
        if len(by_name) == 1:
            return by_name[0]
    return {}


def _operation_error(tool: Mapping[str, Any]) -> str:
    validation = tool.get("validation_errors")
    if isinstance(validation, list) and validation:
        return "; ".join(_text(item) for item in validation if _text(item))
    return _text(tool.get("execution_error") or tool.get("error"))


def _classify_operations(
    *,
    case_id: str,
    tools: Sequence[Mapping[str, Any]],
    result_refs: Sequence[Mapping[str, Any]],
    expected_dataviews: Sequence[str],
) -> list[dict[str, Any]]:
    expected = set(expected_dataviews)
    expected_subjects = {item.split(".", 1)[0] for item in expected}
    refs_by_uri = {
        _text(ref.get("result_ref")): dict(ref)
        for ref in result_refs
        if _text(ref.get("result_ref"))
    }

    catalog_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    subject_catalogs: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, tool in enumerate(tools):
        if _text(tool.get("tool")) != "read_finance_catalog":
            continue
        subject = _text(tool.get("subject"))
        dataview = _text(tool.get("dataview"))
        catalog_indices[(subject, dataview)].append(index)
        subject_catalogs[subject].append((index, dataview))
    broad_ladder_indices = {
        index
        for subject, entries in subject_catalogs.items()
        for index, dataview in entries
        if not dataview
        and any(later_index > index and later_dataview for later_index, later_dataview in entries)
    }

    seen_catalog: set[tuple[str, str]] = set()
    seen_queries: set[str] = set()
    seen_loads: set[str] = set()
    prior_queries: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []

    for index, raw_tool in enumerate(tools, start=1):
        tool = dict(raw_tool)
        name = _text(tool.get("tool")) or "unknown_tool"
        error = _operation_error(tool)
        api = _api_from_request(tool.get("request") or tool.get("submitted_request"))
        linked_ref = _result_ref_for_tool(tool, result_refs) if name == "finance_query" else {}
        row_count = tool.get("row_count")
        if row_count is None and linked_ref:
            row_count = linked_ref.get("row_count")
        result_ref = _text(linked_ref.get("result_ref") or tool.get("result_ref"))
        category = "UNKNOWN"
        outcome = "unclassified"
        rule = "insufficient_observable_evidence"
        confidence = "low"
        evidence: dict[str, Any] = {}

        if name == "read_finance_catalog":
            subject = _text(tool.get("subject"))
            dataview = _text(tool.get("dataview"))
            key = (subject, dataview)
            full_api = f"{subject}.{dataview}" if subject and dataview else ""
            if key in seen_catalog:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "duplicate_catalog_read",
                    "repeated_identical_catalog",
                    "high",
                )
            elif index - 1 in broad_ladder_indices:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "broad_catalog_then_specific_catalog",
                    "catalog_ladder",
                    "high",
                )
            elif full_api and full_api in expected:
                category, outcome, rule, confidence = (
                    "STRUCTURAL_EFFECTIVE",
                    "target_catalog_located",
                    "expected_dataview_catalog",
                    "high",
                )
            elif subject in expected_subjects:
                category, outcome, rule, confidence = (
                    "SEMANTIC_NECESSARY",
                    "subject_catalog_discovery",
                    "expected_subject_catalog",
                    "medium",
                )
            else:
                category, outcome, rule, confidence = (
                    "UNKNOWN",
                    "catalog_relevance_not_provable",
                    "catalog_outside_expected_dataview",
                    "low",
                )
            seen_catalog.add(key)
            evidence = {"subject": subject, "dataview": dataview}

        elif name == "finance_query":
            normalized = _normalize_query(tool.get("request") or tool.get("submitted_request"))
            selection_conflicts = _selection_sample_conflicts(linked_ref) if linked_ref else []
            previous_same_api = next(
                (item for item in reversed(prior_queries) if item.get("api") == api),
                None,
            )
            terminal_zero_before = bool(
                previous_same_api
                and previous_same_api.get("row_count") == 0
                and previous_same_api.get("sample_complete")
            )
            follows_failed_same_api = bool(
                previous_same_api and previous_same_api.get("error")
            )
            sample_complete = bool(
                linked_ref.get("sample_complete")
                or _mapping(linked_ref.get("step_evidence")).get("sample_complete")
            )
            if tool.get("validation_errors"):
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "validation_error",
                    "query_validation_error",
                    "high",
                )
            elif error:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "execution_error",
                    "query_execution_error",
                    "high",
                )
            elif normalized and normalized in seen_queries:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "duplicate_query",
                    "repeated_identical_query",
                    "high",
                )
            elif terminal_zero_before:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "requery_after_terminal_zero",
                    "terminal_zero_requery",
                    "high",
                )
            elif selection_conflicts and sample_complete:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "selection_sample_conflict",
                    "selection_applied_equality_conflicts_with_complete_sample",
                    "high",
                )
            elif follows_failed_same_api:
                category, outcome, rule, confidence = (
                    "REASONABLE_RECOVERY",
                    "changed_query_after_failure",
                    "successful_same_api_recovery",
                    "medium",
                )
            elif any(_api_matches_expected(api, item) for item in expected):
                category, outcome, rule, confidence = (
                    "STRUCTURAL_EFFECTIVE",
                    "target_api_executed",
                    "expected_dataview_coverage",
                    "high",
                )
            elif api.split(".", 1)[0] in expected_subjects if api else False:
                category, outcome, rule, confidence = (
                    "SEMANTIC_NECESSARY",
                    "same_subject_dependency",
                    "supporting_api_for_expected_subject",
                    "medium",
                )
            elif api:
                category, outcome, rule, confidence = (
                    "SEMANTIC_NECESSARY",
                    "cross_subject_dependency",
                    "supporting_api_relevance_requires_semantic_review",
                    "low",
                )
            evidence = {
                "normalized_request": normalized,
                "selection_sample_conflicts": selection_conflicts,
                "linked_result_ref": result_ref,
                "sample_complete": sample_complete,
            }
            if normalized:
                seen_queries.add(normalized)
            prior_queries.append(
                {
                    "api": api,
                    "normalized_request": normalized,
                    "row_count": row_count,
                    "sample_complete": sample_complete,
                    "error": error,
                }
            )

        elif name == "load_finance_result":
            referenced = refs_by_uri.get(result_ref, {})
            sample_complete = bool(
                referenced.get("sample_complete")
                or _mapping(referenced.get("step_evidence")).get("sample_complete")
            )
            if result_ref and result_ref in seen_loads:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "duplicate_result_load",
                    "repeated_identical_result_load",
                    "high",
                )
            elif referenced and sample_complete:
                category, outcome, rule, confidence = (
                    "DESIGN_WASTE",
                    "load_after_complete_inline_sample",
                    "sample_complete_result_loaded_again",
                    "high",
                )
            elif referenced:
                category, outcome, rule, confidence = (
                    "SEMANTIC_NECESSARY",
                    "load_truncated_result",
                    "sample_incomplete_requires_full_result",
                    "high",
                )
            else:
                category, outcome, rule, confidence = (
                    "UNKNOWN",
                    "result_reference_not_joinable",
                    "missing_result_ref_evidence",
                    "low",
                )
            if result_ref:
                seen_loads.add(result_ref)
            evidence = {
                "result_ref_found": bool(referenced),
                "sample_complete": sample_complete,
            }

        elif error:
            category, outcome, rule, confidence = (
                "DESIGN_WASTE",
                "tool_error",
                "non_query_tool_error",
                "high",
            )

        operations.append(
            {
                "case_id": case_id,
                "sequence": index,
                "tool": name,
                "category": category,
                "outcome": outcome,
                "rule": rule,
                "confidence": confidence,
                "api": api,
                "goal": _text(tool.get("goal")),
                "request": _text(tool.get("request") or tool.get("submitted_request")),
                "subject": _text(tool.get("subject")),
                "dataview": _text(tool.get("dataview")),
                "result_name": _text(tool.get("result_name") or tool.get("assigned_result_name")),
                "result_ref": result_ref,
                "row_count": row_count,
                "error": error,
                "expected_dataviews": list(expected_dataviews),
                "evidence": evidence,
            }
        )
    return operations


def _stage_rows(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Create occurrence-level client estimates from public SSE milestones.

    The runtime may replay completed progress blocks in the terminal surface
    payload.  Identical terminal-only replays are discarded.  Repeated
    ``running -> completed`` cycles with the same block id remain separate,
    because they represent real repeated query attempts.
    """

    milestones = _list_of_mappings(case.get("progress_milestones"))
    if not milestones:
        return [
            {
                "case_id": _text(case.get("case_id")),
                "occurrence": index,
                **dict(item),
                "timing_source": "precomputed_client_estimate",
            }
            for index, item in enumerate(
                _list_of_mappings(case.get("stage_timing_estimates")), start=1
            )
        ]

    case_id = _text(case.get("case_id"))
    total_ms = float(case.get("total_elapsed_ms") or 0.0)
    open_steps: dict[str, dict[str, Any]] = {}
    terminal_fingerprints: set[tuple[str, str, str]] = set()
    occurrence_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    previous_end = 0.0

    def append_row(
        item: Mapping[str, Any],
        *,
        start_ms: float,
        end_ms: float,
        start_basis: str,
        end_basis: str,
    ) -> None:
        nonlocal previous_end
        block_id = _text(item.get("block_id")) or _text(item.get("title")) or "unknown"
        occurrence_counts[block_id] += 1
        start_ms = max(0.0, start_ms)
        end_ms = max(start_ms, end_ms)
        gap = max(0.0, start_ms - previous_end)
        rows.append(
            {
                "case_id": case_id,
                "occurrence": occurrence_counts[block_id],
                "stage": _text(item.get("stage")),
                "block_id": block_id,
                "title": _text(item.get("title")),
                "summary": _text(item.get("summary")),
                "start_ms": round(start_ms, 2),
                "end_ms": round(end_ms, 2),
                "duration_ms": round(end_ms - start_ms, 2),
                "unattributed_gap_before_ms": round(gap, 2),
                "start_basis": start_basis,
                "end_basis": end_basis,
                "timing_source": "public_sse_client_estimate",
            }
        )
        previous_end = max(previous_end, end_ms)

    for item in milestones:
        block_id = _text(item.get("block_id")) or _text(item.get("title")) or "unknown"
        status = _text(item.get("status")) or "running"
        elapsed = float(item.get("elapsed_ms") or 0.0)
        if status == "running":
            if block_id in open_steps:
                previous = open_steps.pop(block_id)
                append_row(
                    previous,
                    start_ms=float(previous.get("elapsed_ms") or elapsed),
                    end_ms=elapsed,
                    start_basis="first_public_running_progress",
                    end_basis="next_running_same_block_fallback",
                )
            open_steps[block_id] = dict(item)
            continue

        fingerprint = (block_id, status, _text(item.get("summary")))
        if block_id in open_steps:
            started = open_steps.pop(block_id)
            append_row(
                item,
                start_ms=float(started.get("elapsed_ms") or elapsed),
                end_ms=elapsed,
                start_basis="first_public_running_progress",
                end_basis="public_terminal_progress",
            )
            terminal_fingerprints.add(fingerprint)
        elif fingerprint in terminal_fingerprints:
            # Terminal result surfaces replay previously observed progress.
            continue
        else:
            append_row(
                item,
                start_ms=previous_end if rows else elapsed,
                end_ms=elapsed,
                start_basis=(
                    "previous_public_step_end_estimate"
                    if rows
                    else "completed_only_public_observation"
                ),
                end_basis="public_terminal_progress",
            )
            terminal_fingerprints.add(fingerprint)

    for item in open_steps.values():
        append_row(
            item,
            start_ms=float(item.get("elapsed_ms") or total_ms),
            end_ms=total_ms,
            start_basis="first_public_running_progress",
            end_basis="request_end_fallback",
        )
    return rows


def _answer_for_case(case: Mapping[str, Any], provider: Mapping[str, Any]) -> str:
    return _text(
        provider.get("result")
        or _mapping(case.get("done_result")).get("message")
        or _mapping(case.get("done_event")).get("message")
    )


def _answer_truncation(answer: str) -> tuple[str, str, str]:
    if not answer:
        return "not_applicable", "answer_empty", "high"
    stripped = answer.rstrip()
    cap_like = len(stripped) in {2000, 4000, 8000, 16000}
    incomplete_terminal = bool(stripped) and stripped[-1] not in TERMINAL_PUNCTUATION
    incomplete_table = bool(stripped.splitlines()) and (
        stripped.splitlines()[-1].count("|") in {1, 2}
        or stripped.splitlines()[-1].rstrip().endswith("| 1")
    )
    unmatched_fence = stripped.count("```") % 2 == 1
    if cap_like and (incomplete_terminal or incomplete_table):
        return "likely_truncated", "answer_hits_length_cap_with_incomplete_ending", "high"
    if unmatched_fence:
        return "possibly_truncated", "unclosed_markdown_fence", "medium"
    return "not_detected", "no_high_confidence_truncation_signal", "medium"


def _window_expectation(question: str) -> int | None:
    chinese = {
        "一": 1,
        "两": 2,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    match = re.search(r"(?:近|最近|过去)\s*(\d+|[一二两三四五六七八九十])\s*个?(?:交易日|日|天)", question)
    if not match:
        return None
    token = match.group(1)
    return int(token) if token.isdigit() else chinese.get(token)


def _audit_case(
    *,
    case: Mapping[str, Any],
    provider: Mapping[str, Any],
    expected_dataviews: Sequence[str],
    operations: Sequence[Mapping[str, Any]],
    result_refs: Sequence[Mapping[str, Any]],
    answer: str,
) -> list[dict[str, Any]]:
    case_id = _text(case.get("case_id"))
    done_result = _mapping(case.get("done_result"))
    mode = _text(done_result.get("mode"))
    axes = _mapping(_mapping(case.get("dispatch_plan")).get("canonical_axes"))
    findings: list[dict[str, Any]] = []

    def add(
        check: str,
        status: str,
        severity: str,
        rule: str,
        evidence: Any,
        *,
        confidence: str = "high",
        dimension: str = "harness",
    ) -> None:
        findings.append(
            {
                "case_id": case_id,
                "check": check,
                "status": status,
                "severity": severity,
                "rule": rule,
                "confidence": confidence,
                "dimension": dimension,
                "evidence": evidence,
            }
        )

    route_ok = mode == "financial_qa_cc" and axes.get("domain") == "business"
    add(
        "route",
        "PASS" if route_ok else "FAIL",
        "info" if route_ok else "critical",
        "financial_query_route_contract",
        {"mode": mode, "canonical_axes": axes},
        dimension="system",
    )

    if not answer:
        add("answer", "FAIL", "critical", "empty_answer", {"answer_length": 0}, dimension="renderer")
    elif any(marker in answer for marker in GENERIC_INABILITY_MARKERS):
        add(
            "answer",
            "FAIL",
            "critical",
            "generic_inability_despite_finance_benchmark",
            {"answer_excerpt": answer[:240]},
            dimension="system",
        )
    else:
        add("answer", "PASS", "info", "answer_present", {"answer_length": len(answer)}, dimension="renderer")

    case_error = _mapping(case.get("error"))
    provider_error = _text(provider.get("error"))
    operation_errors = [op for op in operations if _text(op.get("error"))]
    if case_error or provider_error:
        add(
            "error",
            "FAIL",
            "critical",
            "terminal_case_or_provider_error",
            {"case_error": case_error, "provider_error": provider_error},
            dimension="system",
        )
    elif operation_errors:
        add(
            "error",
            "WARN",
            "major",
            "recovered_tool_errors",
            {"operation_sequences": [op.get("sequence") for op in operation_errors]},
            dimension="harness",
        )
    else:
        add("error", "PASS", "info", "no_observed_errors", {}, dimension="system")

    successful_apis = {
        _text(op.get("api"))
        for op in operations
        if _text(op.get("api")) and not _text(op.get("error"))
    }
    ref_apis = {_text(ref.get("api")) for ref in result_refs if _text(ref.get("api"))}
    covered = successful_apis | ref_apis
    missing = sorted(
        expected_api
        for expected_api in expected_dataviews
        if not any(_api_matches_expected(actual_api, expected_api) for actual_api in covered)
    )
    add(
        "expected_dataview",
        "PASS" if not missing else "FAIL",
        "info" if not missing else "critical",
        "expected_dataview_coverage",
        {
            "expected": list(expected_dataviews),
            "covered": sorted(covered),
            "missing": missing,
        },
        dimension="harness",
    )

    if result_refs:
        add(
            "result_refs",
            "PASS",
            "info",
            "evidence_refs_present",
            {"count": len(result_refs)},
            dimension="harness",
        )
    else:
        add(
            "result_refs",
            "FAIL",
            "critical",
            "no_financial_evidence_ref",
            {"count": 0},
            dimension="harness",
        )

    expected_window = _window_expectation(_text(case.get("question")))
    if expected_window is None:
        add(
            "window",
            "NOT_OBSERVABLE",
            "info",
            "no_simple_window_phrase_detected",
            {},
            confidence="low",
            dimension="audit",
        )
    else:
        request_text = "\n".join(_text(op.get("request")) for op in operations)
        visible = bool(
            re.search(
                rf"(?:(?:limit|k|window|period)\s*=\s*{expected_window}\b|[-+]\s*{expected_window}\b)",
                request_text,
                re.I,
            )
            or re.search(r"(?:tradedate|date|交易日)", request_text, re.I)
        )
        add(
            "window",
            "PASS" if visible else "WARN",
            "info" if visible else "major",
            "window_marker_visible_in_tool_requests" if visible else "window_not_visible_in_observed_requests",
            {"expected_days": expected_window, "request_excerpt": request_text[:1000]},
            confidence="medium",
            dimension="harness",
        )

    zero_refs = [ref for ref in result_refs if ref.get("row_count") == 0]
    if not zero_refs:
        add("zero_result", "PASS", "info", "no_zero_row_refs", {"count": 0}, dimension="tool")
    elif len(zero_refs) == len(result_refs) and any(marker in answer for marker in NO_DATA_MARKERS):
        add(
            "zero_result",
            "PASS",
            "info",
            "all_zero_results_acknowledged_in_answer",
            {"count": len(zero_refs)},
            dimension="renderer",
        )
    elif len(zero_refs) == len(result_refs):
        add(
            "zero_result",
            "FAIL",
            "critical",
            "all_evidence_zero_without_no_data_disclosure",
            {"count": len(zero_refs), "answer_excerpt": answer[:300]},
            dimension="renderer",
        )
    else:
        add(
            "zero_result",
            "WARN",
            "major",
            "zero_result_followed_by_other_evidence",
            {"zero_count": len(zero_refs), "total_refs": len(result_refs)},
            dimension="harness",
        )

    null_cells: list[dict[str, Any]] = []
    for ref in result_refs:
        for row_index, row in enumerate(_sample_rows(ref), start=1):
            for field, value in row.items():
                if value is None:
                    null_cells.append(
                        {
                            "result_name": _text(ref.get("result_name")),
                            "row": row_index,
                            "field": field,
                        }
                    )
    add(
        "null_sample",
        "WARN" if null_cells else "PASS",
        "minor" if null_cells else "info",
        "nulls_present_in_returned_sample" if null_cells else "no_sample_nulls",
        {"count": len(null_cells), "examples": null_cells[:10]},
        confidence="medium",
        dimension="tool",
    )

    duplicate_ops = [
        op
        for op in operations
        if _text(op.get("rule"))
        in {
            "repeated_identical_catalog",
            "repeated_identical_query",
            "repeated_identical_result_load",
        }
    ]
    add(
        "duplicate_operation",
        "FAIL" if duplicate_ops else "PASS",
        "major" if duplicate_ops else "info",
        "duplicate_operations_detected" if duplicate_ops else "no_exact_duplicate_operations",
        {"operation_sequences": [op.get("sequence") for op in duplicate_ops]},
        dimension="harness",
    )

    truncation, truncation_rule, truncation_confidence = _answer_truncation(answer)
    add(
        "answer_truncation",
        "FAIL" if truncation == "likely_truncated" else "WARN" if truncation == "possibly_truncated" else "PASS",
        "critical" if truncation == "likely_truncated" else "minor" if truncation == "possibly_truncated" else "info",
        truncation_rule,
        {"classification": truncation, "answer_length": len(answer), "ending": answer[-120:]},
        confidence=truncation_confidence,
        dimension="renderer",
    )

    conflict_ops = [
        op for op in operations if _text(op.get("rule")).startswith("selection_applied_equality_conflicts")
    ]
    add(
        "selection_sample_consistency",
        "FAIL" if conflict_ops else "PASS",
        "critical" if conflict_ops else "info",
        "selection_sample_conflict" if conflict_ops else "no_complete_sample_equality_conflict",
        {"operation_sequences": [op.get("sequence") for op in conflict_ops]},
        dimension="tool",
    )

    add(
        "semantic_correctness",
        "NOT_OBSERVABLE",
        "info",
        "requires_human_or_domain_specific_judge",
        {
            "boundary": (
                "Route, evidence coverage, zero/null consistency, duplicates, and truncation are audited; "
                "the financial meaning and numerical conclusion are not proven mechanically."
            )
        },
        confidence="high",
        dimension="audit",
    )
    return findings


def _group_summary(cases: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        value = _text(case.get(key)) or "(unknown)"
        grouped[value].append(case)
    rows: list[dict[str, Any]] = []
    for value, items in grouped.items():
        op_count = sum(int(item.get("operation_count") or 0) for item in items)
        waste = sum(int(item.get("design_waste_count") or 0) for item in items)
        rows.append(
            {
                key: value,
                "case_count": len(items),
                "ok_count": sum(1 for item in items if item.get("status") == "ok"),
                "error_count": sum(1 for item in items if item.get("status") != "ok"),
                "latency_ms": _distribution(item.get("total_elapsed_ms") for item in items),
                "cc_duration_ms": _distribution(item.get("cc_duration_ms") for item in items),
                "operation_count": op_count,
                "design_waste_count": waste,
                "design_waste_rate": _ratio(waste, op_count),
                "expected_dataview_failures": sum(
                    1
                    for item in items
                    if "expected_dataview" in (item.get("failed_audits") or [])
                ),
                "route_failures": sum(
                    1 for item in items if "route" in (item.get("failed_audits") or [])
                ),
            }
        )
    rows.sort(key=lambda item: (-item["case_count"], _text(item.get(key))))
    return rows


def analyze(
    *,
    batch_path: Path,
    source_path: Path,
    baseline_path: Path,
    provider_events_path: Path,
) -> dict[str, Any]:
    batch = _mapping(_read_json(batch_path))
    source_payload = _mapping(_read_json(source_path))
    baseline_payload = _mapping(_read_json(baseline_path))
    provider_records = _read_jsonl(provider_events_path)
    providers, provider_multiplicity = _provider_events_by_session(provider_records)

    source_cases = {
        _text(item.get("case_id")): item
        for item in _list_of_mappings(source_payload.get("cases"))
        if _text(item.get("case_id"))
    }
    baseline_rows = {
        _text(item.get("case_id")): item
        for item in _list_of_mappings(baseline_payload.get("rows"))
        if _text(item.get("case_id"))
    }

    case_rows: list[dict[str, Any]] = []
    all_operations: list[dict[str, Any]] = []
    all_stages: list[dict[str, Any]] = []
    all_audits: list[dict[str, Any]] = []
    unmatched_provider_sessions: list[str] = []

    for raw_case in _list_of_mappings(batch.get("cases")):
        case = dict(raw_case)
        case_id = _text(case.get("case_id"))
        source_case = _mapping(case.get("source_case")) or source_cases.get(case_id, {})
        expected_dataviews = _parse_expected_dataviews(source_case.get("dataview"))
        financial_qa = _mapping(case.get("financial_qa"))
        session_id = _text(financial_qa.get("session_id"))
        provider = providers.get(session_id, {})
        if session_id and not provider:
            unmatched_provider_sessions.append(session_id)

        provider_tools = _list_of_mappings(provider.get("tool_calls"))
        fallback_tools = _list_of_mappings(financial_qa.get("tool_calls"))
        tools = provider_tools or fallback_tools
        provider_refs = _list_of_mappings(provider.get("result_refs"))
        result_refs = provider_refs or _list_of_mappings(financial_qa.get("result_refs"))
        operations = _classify_operations(
            case_id=case_id,
            tools=tools,
            result_refs=result_refs,
            expected_dataviews=expected_dataviews,
        )
        stages = _stage_rows(case)
        answer = _answer_for_case(case, provider)
        audits = _audit_case(
            case=case,
            provider=provider,
            expected_dataviews=expected_dataviews,
            operations=operations,
            result_refs=result_refs,
            answer=answer,
        )

        usage = _mapping(provider.get("llm_usage")) or _mapping(case.get("llm_usage"))
        if not usage:
            usage = _mapping(_mapping(case.get("done_result")).get("agent_llm_usage"))
        planner_usage = _mapping(
            _mapping(case.get("dispatch_plan")).get("llm_usage")
        )
        cc_total_tokens = usage.get("total_tokens")
        planner_total_tokens = planner_usage.get("total_tokens")
        system_total_tokens = (
            int(cc_total_tokens or 0) + int(planner_total_tokens or 0)
            if cc_total_tokens is not None or planner_total_tokens is not None
            else None
        )
        reported_llm_call_count = (
            int(usage.get("call_count") or 0)
            + int(planner_usage.get("call_count") or 0)
            if usage.get("call_count") is not None
            or planner_usage.get("call_count") is not None
            else None
        )
        transport = _mapping(provider.get("provider_transport"))
        cc_duration = provider.get("duration_ms")
        if cc_duration is None:
            cc_duration = financial_qa.get("duration_ms")
        total_ms = float(case.get("total_elapsed_ms") or 0.0)
        cc_ms = float(cc_duration) if cc_duration is not None else None
        non_cc_ms = max(0.0, total_ms - cc_ms) if cc_ms is not None else None
        category_counts = Counter(_text(item.get("category")) for item in operations)
        operation_count = len(operations)
        strict_effective = (
            category_counts["STRUCTURAL_EFFECTIVE"]
            + category_counts["SEMANTIC_NECESSARY"]
        )
        acceptable = strict_effective + category_counts["REASONABLE_RECOVERY"]
        failed_audits = [
            _text(item.get("check")) for item in audits if item.get("status") == "FAIL"
        ]
        warning_audits = [
            _text(item.get("check")) for item in audits if item.get("status") == "WARN"
        ]
        actual_apis = list(
            dict.fromkeys(
                [_text(ref.get("api")) for ref in result_refs if _text(ref.get("api"))]
                + [_text(op.get("api")) for op in operations if _text(op.get("api"))]
            )
        )
        axes = _mapping(_mapping(case.get("dispatch_plan")).get("canonical_axes"))
        baseline = baseline_rows.get(case_id, {})
        old_duration = baseline.get("duration_ms")
        latency_delta = (
            round(total_ms - float(old_duration), 2)
            if old_duration is not None
            else None
        )
        first_feedback = _mapping(case.get("first_feedback"))
        first_progress = _mapping(case.get("first_progress_feedback"))
        case_row = {
            "case_id": case_id,
            "ordinal": case.get("ordinal"),
            "question": _text(case.get("question")),
            "subject": _text(source_case.get("subject")),
            "dataview": _text(source_case.get("dataview")),
            "expected_dataviews": expected_dataviews,
            "status": _text(case.get("status")),
            "answer": answer,
            "answer_length": len(answer),
            "total_elapsed_ms": round(total_ms, 2),
            "cc_duration_ms": round(cc_ms, 2) if cc_ms is not None else None,
            "non_cc_duration_ms": round(non_cc_ms, 2) if non_cc_ms is not None else None,
            "cc_share": round(cc_ms / total_ms, 4) if cc_ms is not None and total_ms else None,
            "first_feedback_ms": first_feedback.get("client_elapsed_ms"),
            "first_feedback_text": _text(first_feedback.get("text")),
            "first_progress_feedback_ms": first_progress.get("client_elapsed_ms"),
            "first_progress_feedback_text": _text(first_progress.get("text")),
            "sse_event_count": case.get("sse_event_count"),
            "provider_session_id": session_id,
            "provider_record_found": bool(provider),
            "provider_record_count_for_session": provider_multiplicity.get(session_id, 0),
            "provider_duration_ms": provider.get("duration_ms"),
            "provider_stream_event_count": provider.get("stream_event_count"),
            "provider_text_delta_count": provider.get("text_delta_count"),
            "provider_request_count": transport.get("request_count"),
            "provider_failure_count": transport.get("failure_count"),
            "provider_api_retry_count": provider.get("api_retry_count"),
            "provider_api_error_status": provider.get("api_error_status"),
            "provider_adapter": _text(transport.get("adapter")),
            "model_name": _text(provider.get("model_name") or _mapping(case.get("done_result")).get("model_name")),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "llm_call_count": usage.get("call_count"),
            "planner_prompt_tokens": planner_usage.get("prompt_tokens"),
            "planner_completion_tokens": planner_usage.get("completion_tokens"),
            "planner_total_tokens": planner_usage.get("total_tokens"),
            "planner_llm_call_count": planner_usage.get("call_count"),
            "system_total_tokens": system_total_tokens,
            "reported_llm_call_count": reported_llm_call_count,
            "route_mode": _text(_mapping(case.get("done_result")).get("mode")),
            "route_domain": _text(axes.get("domain")),
            "route_interaction_mode": _text(axes.get("interaction_mode")),
            "route_execution_path": _text(axes.get("execution_path")),
            "client_prewarmed": provider.get("client_prewarmed", financial_qa.get("client_prewarmed")),
            "client_reused": provider.get("client_reused", financial_qa.get("client_reused")),
            "operation_count": operation_count,
            "structural_effective_count": category_counts["STRUCTURAL_EFFECTIVE"],
            "semantic_necessary_count": category_counts["SEMANTIC_NECESSARY"],
            "reasonable_recovery_count": category_counts["REASONABLE_RECOVERY"],
            "design_waste_count": category_counts["DESIGN_WASTE"],
            "unknown_operation_count": category_counts["UNKNOWN"],
            "strict_effective_rate": _ratio(strict_effective, operation_count),
            "acceptable_operation_rate": _ratio(acceptable, operation_count),
            "design_waste_rate": _ratio(category_counts["DESIGN_WASTE"], operation_count),
            "actual_apis": actual_apis,
            "result_ref_count": len(result_refs),
            "result_row_counts": [ref.get("row_count") for ref in result_refs],
            "stage_count": len(stages),
            "failed_audits": failed_audits,
            "warning_audits": warning_audits,
            "case_error": _mapping(case.get("error")),
            "provider_error": _text(provider.get("error")),
            "historical_status": _text(baseline.get("historical_status")),
            "historical_duration_ms": old_duration,
            "historical_tool_rounds": baseline.get("tool_rounds"),
            "historical_invalid_rounds": baseline.get("invalid_rounds"),
            "latency_delta_vs_historical_ms": latency_delta,
        }
        case_rows.append(case_row)
        all_operations.extend(operations)
        all_stages.extend(stages)
        all_audits.extend(audits)

    operation_counts = Counter(_text(item.get("category")) for item in all_operations)
    operation_total = len(all_operations)
    strict_total = (
        operation_counts["STRUCTURAL_EFFECTIVE"]
        + operation_counts["SEMANTIC_NECESSARY"]
    )
    acceptable_total = strict_total + operation_counts["REASONABLE_RECOVERY"]
    audit_status = Counter(_text(item.get("status")) for item in all_audits)
    audit_rules = Counter(
        _text(item.get("rule"))
        for item in all_audits
        if item.get("status") in {"FAIL", "WARN"}
    )

    p95 = _percentile((item.get("total_elapsed_ms") for item in case_rows), 0.95)
    longtails = []
    for item in sorted(case_rows, key=lambda row: float(row.get("total_elapsed_ms") or 0), reverse=True):
        if p95 is not None and float(item.get("total_elapsed_ms") or 0) < p95:
            continue
        drivers = []
        if (item.get("design_waste_count") or 0) > 0:
            drivers.append(f"design_waste={item['design_waste_count']}")
        if (item.get("provider_request_count") or 0) >= 10:
            drivers.append(f"provider_requests={item['provider_request_count']}")
        if (item.get("total_tokens") or 0) >= 20000:
            drivers.append(f"tokens={item['total_tokens']}")
        if (item.get("non_cc_duration_ms") or 0) >= 10000:
            drivers.append(f"non_cc_ms={item['non_cc_duration_ms']}")
        if item.get("failed_audits"):
            drivers.append("audit_fail=" + ",".join(item["failed_audits"]))
        longtails.append(
            {
                "case_id": item["case_id"],
                "question": item["question"],
                "subject": item["subject"],
                "dataview": item["dataview"],
                "total_elapsed_ms": item["total_elapsed_ms"],
                "cc_duration_ms": item["cc_duration_ms"],
                "non_cc_duration_ms": item["non_cc_duration_ms"],
                "provider_request_count": item["provider_request_count"],
                "total_tokens": item["total_tokens"],
                "operation_count": item["operation_count"],
                "design_waste_count": item["design_waste_count"],
                "drivers": drivers or ["latency_tail_without_mechanically_identified_driver"],
            }
        )

    stage_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for stage in all_stages:
        stage_groups[_text(stage.get("title")) or _text(stage.get("block_id")) or "(unknown)"].append(stage)
    stage_summary = []
    for title, items in stage_groups.items():
        stage_summary.append(
            {
                "title": title,
                "occurrence_count": len(items),
                "case_count": len({_text(item.get("case_id")) for item in items}),
                "duration_ms": _distribution(item.get("duration_ms") for item in items),
                "unattributed_gap_before_ms": _distribution(
                    item.get("unattributed_gap_before_ms") for item in items
                ),
            }
        )
    stage_summary.sort(key=lambda item: (-item["occurrence_count"], item["title"]))

    completed_ids = {_text(item.get("case_id")) for item in case_rows}
    source_ids = set(source_cases)
    provider_found = sum(1 for item in case_rows if item.get("provider_record_found"))
    warm_cases = [item for item in case_rows if item.get("client_prewarmed") is True]
    non_warm_cases = [item for item in case_rows if item.get("client_prewarmed") is not True]

    return {
        "analysis_name": "finance_query_api_batch_analysis_v1",
        "generated_at": _now_iso(),
        "inputs": {
            "batch": str(batch_path),
            "source_cases": str(source_path),
            "old_baseline": str(baseline_path),
            "provider_events": str(provider_events_path),
            "batch_partial": bool(batch.get("partial")),
            "batch_summary": _mapping(batch.get("summary")),
        },
        "observability_contract": {
            "measured": [
                "total_elapsed_ms and first-feedback timestamps measured by the HTTP client",
                "provider duration/request/retry/stream/token counts joined by financial_qa.session_id",
                "planner token usage from dispatch_plan.llm_usage plus Finance CC token usage; background thread-title usage is not exposed",
                "tool calls, result refs, row counts, errors, selections, and returned samples recorded by the harness",
            ],
            "estimated": [
                "stage start/end/duration inferred from public SSE progress blocks",
                "non_cc_duration_ms computed as total_elapsed_ms minus provider CC duration",
                "operation utility and audit severity assigned by deterministic evidence rules",
            ],
            "not_observable": [
                "exact server-side spans inside routing, model reasoning, database execution, or rendering",
                "unpublished chain-of-thought",
                "complete semantic and numerical correctness without human/domain-specific judging",
                "whether a low-confidence supporting API was intellectually necessary",
            ],
            "provider_join_policy": (
                "Use the latest terminal provider record per exact session_id; do not sum duplicate records."
            ),
        },
        "classification_definitions": {
            "STRUCTURAL_EFFECTIVE": "Directly locates or executes an expected benchmark dataview.",
            "SEMANTIC_NECESSARY": "Supporting lookup/load that is observably useful but not the direct target API.",
            "REASONABLE_RECOVERY": "Changed, successful recovery after an observed failure; acceptable overhead, not a strict first-pass success.",
            "DESIGN_WASTE": "High-confidence avoidable work: duplicate/ladders, invalid calls, terminal-zero re-query, unnecessary complete-result load, or evidence conflict.",
            "UNKNOWN": "Insufficient observable evidence to judge utility; excluded from any claim of correctness.",
            "strict_effective_rate": "(STRUCTURAL_EFFECTIVE + SEMANTIC_NECESSARY) / all observed operations.",
            "acceptable_operation_rate": "Strict effective plus REASONABLE_RECOVERY / all observed operations.",
        },
        "summary": {
            "source_case_count": len(source_cases),
            "completed_case_count": len(case_rows),
            "missing_case_count": len(source_ids - completed_ids),
            "missing_case_ids": sorted(source_ids - completed_ids),
            "ok_case_count": sum(1 for item in case_rows if item.get("status") == "ok"),
            "error_case_count": sum(1 for item in case_rows if item.get("status") != "ok"),
            "provider_record_found_count": provider_found,
            "provider_record_missing_count": len(case_rows) - provider_found,
            "unmatched_provider_session_ids": unmatched_provider_sessions,
            "latency_ms": _distribution(item.get("total_elapsed_ms") for item in case_rows),
            "cc_duration_ms": _distribution(item.get("cc_duration_ms") for item in case_rows),
            "non_cc_duration_ms": _distribution(item.get("non_cc_duration_ms") for item in case_rows),
            "first_feedback_ms": _distribution(item.get("first_feedback_ms") for item in case_rows),
            "first_progress_feedback_ms": _distribution(item.get("first_progress_feedback_ms") for item in case_rows),
            "total_tokens": _distribution(item.get("total_tokens") for item in case_rows),
            "planner_total_tokens": _distribution(
                item.get("planner_total_tokens") for item in case_rows
            ),
            "system_total_tokens": _distribution(
                item.get("system_total_tokens") for item in case_rows
            ),
            "provider_request_count": _distribution(item.get("provider_request_count") for item in case_rows),
            "operation_count": operation_total,
            "operation_categories": {category: operation_counts[category] for category in CATEGORIES},
            "strict_effective_rate": _ratio(strict_total, operation_total),
            "acceptable_operation_rate": _ratio(acceptable_total, operation_total),
            "design_waste_rate": _ratio(operation_counts["DESIGN_WASTE"], operation_total),
            "audit_status_counts": dict(audit_status),
            "top_failed_or_warning_audit_rules": [
                {"rule": rule, "count": count} for rule, count in audit_rules.most_common(20)
            ],
            "prewarm": {
                "prewarmed_case_count": len(warm_cases),
                "not_observed_as_prewarmed_case_count": len(non_warm_cases),
                "prewarmed_latency_ms": _distribution(item.get("total_elapsed_ms") for item in warm_cases),
                "not_prewarmed_latency_ms": _distribution(item.get("total_elapsed_ms") for item in non_warm_cases),
            },
        },
        "stage_summary": stage_summary,
        "subject_summary": _group_summary(case_rows, "subject"),
        "dataview_summary": _group_summary(case_rows, "dataview"),
        "longtails": longtails,
        "cases": case_rows,
        "stages": all_stages,
        "operations": all_operations,
        "audits": all_audits,
    }


def _validate_analysis(analysis: Mapping[str, Any]) -> None:
    cases = _list_of_mappings(analysis.get("cases"))
    operations = _list_of_mappings(analysis.get("operations"))
    stages = _list_of_mappings(analysis.get("stages"))
    if not cases:
        raise ValueError("analysis contains no completed cases")
    unknown_categories = {
        _text(item.get("category")) for item in operations
    } - set(CATEGORIES)
    if unknown_categories:
        raise ValueError(f"unexpected operation categories: {sorted(unknown_categories)}")
    summary = _mapping(analysis.get("summary"))
    if summary.get("completed_case_count") != len(cases):
        raise ValueError("completed_case_count does not match case rows")
    if summary.get("operation_count") != len(operations):
        raise ValueError("operation_count does not match operation rows")
    case_ids = {_text(item.get("case_id")) for item in cases}
    if any(_text(item.get("case_id")) not in case_ids for item in stages):
        raise ValueError("stage row references an unknown case")


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default=str(DEFAULT_BATCH), help="Batch snapshot JSON")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source cases JSON")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="Old baseline cases JSON")
    parser.add_argument(
        "--provider-events",
        default=str(DEFAULT_PROVIDER_EVENTS),
        help="financial_qa_cc provider events JSONL",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output analysis JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    batch_path = Path(args.batch).expanduser().resolve()
    source_path = Path(args.source).expanduser().resolve()
    baseline_path = Path(args.baseline).expanduser().resolve()
    provider_events_path = Path(args.provider_events).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    for path in (batch_path, source_path, baseline_path, provider_events_path):
        if not path.exists():
            raise FileNotFoundError(path)
    analysis = analyze(
        batch_path=batch_path,
        source_path=source_path,
        baseline_path=baseline_path,
        provider_events_path=provider_events_path,
    )
    _validate_analysis(analysis)
    _atomic_write_json(output_path, analysis)
    summary = _mapping(analysis.get("summary"))
    print(
        json.dumps(
            {
                "output": str(output_path),
                "completed_cases": summary.get("completed_case_count"),
                "operations": summary.get("operation_count"),
                "design_waste_rate": summary.get("design_waste_rate"),
                "latency_p95_ms": _mapping(summary.get("latency_ms")).get("p95"),
                "provider_records_missing": summary.get("provider_record_missing_count"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
