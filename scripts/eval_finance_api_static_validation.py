#!/usr/bin/env python3
"""Evaluate post-DSL JSON structuring and static finance API validation.

The case manifest is intentionally composed of API strings produced in prior
finance evaluations plus a few ordinary business extensions.  The evaluator
does not compare the request with the user's natural-language intent; it only
measures parsing, catalog/dataview validation, and provider execution status.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_structure import structure_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver
from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService


DEFAULT_CASES = ROOT / "tests" / "fixtures" / "finance_api_static_validation_cases_20260825.json"
DEFAULT_OUTPUT = ROOT / "tmp" / "finance_api_static_validation_results_20260825.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _previous_results(payload: Any) -> dict[str, ResultHandle]:
    if not isinstance(payload, Mapping):
        return {}
    rows: dict[str, ResultHandle] = {}
    for name, item in payload.items():
        if not isinstance(item, Mapping):
            continue
        columns = [str(value) for value in item.get("columns") or []]
        data_rows = list(item.get("rows") or [])
        rows[str(name)] = ResultHandle(
            name=str(name),
            api=str(item.get("api") or ""),
            columns=columns,
            data={
                "status": "ok",
                "columns": columns,
                "rows": data_rows,
                "row_count": len(data_rows),
            },
        )
    return rows


def _row_count(result: Mapping[str, Any]) -> int | None:
    result_payload = result.get("result")
    if not isinstance(result_payload, Mapping):
        return None
    data = result_payload.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get("row_count")
    if isinstance(value, int):
        return value
    rows = data.get("rows")
    return len(rows) if isinstance(rows, list) else None


@contextmanager
def _execution_timeout(seconds: float):
    """Bound a live Provider call so one slow query cannot hide all evidence."""

    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"provider execution exceeded {seconds:g}s")

    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _timed_out_by_evaluator(*, reason: str, timeout_seconds: float) -> bool:
    return timeout_seconds > 0 and "provider execution exceeded" in str(reason).lower()


def evaluate_case(
    case: Mapping[str, Any],
    *,
    runtime: FinanceDataToolRuntimeService,
    execute: bool,
    timeout_seconds: float = 0,
) -> dict[str, Any]:
    started = time.perf_counter()
    request = str(case.get("request") or "").strip()
    expected_static = str(case.get("expected_static") or "pass").strip().lower()
    previous = _previous_results(case.get("previous_results"))
    result: dict[str, Any] = {
        "case_id": str(case.get("case_id") or ""),
        "source": str(case.get("source") or ""),
        "user_query": str(case.get("user_query") or ""),
        "request": request,
        "expected_static": expected_static,
        "parse_status": "pending",
        "static_status": "pending",
        "static_errors": [],
        "execution_status": "not_run",
        "execution_reason": "",
        "execution_attempted": False,
        "execution_ok": None,
        "execution_timed_out": False,
        "row_count": None,
    }
    try:
        call = parse_api_call(request)
        result["parse_status"] = "passed"
        result["structured_call"] = structure_call(call)
    except Exception as exc:  # noqa: BLE001 - evaluator must preserve evidence.
        result["parse_status"] = "failed"
        result["static_status"] = "failed"
        result["static_errors"] = [f"PARSE_ERROR: {exc}"]
        result["execution_status"] = "not_run_parse_failed"
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result["static_expected_match"] = expected_static == "fail"
        return result

    validation = validate_call(call, previous)
    validation_ok = validation.ok
    result["static_elapsed_ms"] = round(
        (time.perf_counter() - started) * 1000,
        2,
    )
    result["static_status"] = "passed" if validation_ok else "failed"
    result["static_errors"] = list(validation.errors)
    result["static_expected_match"] = (
        (expected_static == "pass" and validation_ok)
        or (expected_static == "fail" and not validation_ok)
    )
    if not validation_ok:
        result["execution_status"] = "not_run_static_failed"
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result
    if not execute:
        result["execution_status"] = "skipped"
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    result["execution_attempted"] = True
    try:
        with _execution_timeout(timeout_seconds):
            runtime_result = runtime.execute_request(
                request=request,
                previous_results=previous,
            )
    except Exception as exc:  # noqa: BLE001 - live execution evidence.
        result["execution_status"] = "exception"
        result["execution_reason"] = f"{type(exc).__name__}: {exc}"
        result["execution_ok"] = False
        result["execution_timed_out"] = isinstance(
            exc, TimeoutError
        ) or _timed_out_by_evaluator(
            reason=str(exc),
            timeout_seconds=timeout_seconds,
        )
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    result["structured_call"] = runtime_result.get("structured_call") or result["structured_call"]
    execution = runtime_result.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    execution_ok = bool(execution.get("ok"))
    result["execution_status"] = str(execution.get("status") or "unknown")
    result["execution_reason"] = str(execution.get("reason") or "")
    result["execution_ok"] = execution_ok
    result["execution_timed_out"] = _timed_out_by_evaluator(
        reason=result["execution_reason"],
        timeout_seconds=timeout_seconds,
    )
    result["row_count"] = _row_count(runtime_result)
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    expected_invalid = [row for row in rows if row.get("expected_static") == "fail"]
    expected_valid = [row for row in rows if row.get("expected_static") == "pass"]
    static_failed = [row for row in rows if row.get("static_status") == "failed"]
    invalid_blocked = [row for row in expected_invalid if row.get("static_status") == "failed"]
    valid_rejected = [row for row in expected_valid if row.get("static_status") == "failed"]
    execution_candidates = [
        row
        for row in expected_valid
        if row.get("static_status") == "passed"
    ]
    execution_attempted = [
        row for row in execution_candidates if row.get("execution_attempted") is True
    ]
    execution_ok = [row for row in execution_attempted if row.get("execution_ok") is True]
    execution_timed_out = [
        row for row in execution_attempted if row.get("execution_timed_out") is True
    ]
    execution_non_timeout_failed = [
        row
        for row in execution_attempted
        if row.get("execution_ok") is not True
        and row.get("execution_timed_out") is not True
    ]
    status_counts = Counter(
        str(row.get("execution_status") or "unknown")
        for row in execution_attempted
    )
    static_matches = [row for row in rows if row.get("static_expected_match")]
    return {
        "case_count": total,
        "expected_valid_count": len(expected_valid),
        "expected_invalid_count": len(expected_invalid),
        "static_pass_count": total - len(static_failed),
        "static_fail_count": len(static_failed),
        "static_decision_accuracy": _ratio(len(static_matches), total),
        "invalid_detection_rate": _ratio(len(invalid_blocked), len(expected_invalid)),
        "false_rejection_rate": _ratio(len(valid_rejected), len(expected_valid)),
        "static_failure_precision": _ratio(len(invalid_blocked), len(static_failed)),
        "post_static_execution_candidate_count": len(execution_candidates),
        "post_static_execution_attempted_count": len(execution_attempted),
        "post_static_execution_ok_count": len(execution_ok),
        "post_static_execution_timeout_count": len(execution_timed_out),
        "post_static_execution_non_timeout_failure_count": len(
            execution_non_timeout_failed
        ),
        "post_static_execution_skipped_count": len(execution_candidates)
        - len(execution_attempted),
        "post_static_execution_success_rate": _ratio(
            len(execution_ok), len(execution_attempted)
        ),
        "post_static_execution_status_counts": dict(sorted(status_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument(
        "--case-timeout-seconds",
        type=float,
        default=0,
        help="Per-case live Provider timeout; 0 disables the timeout.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, Mapping) else None
    if not isinstance(cases, list):
        raise ValueError("case manifest must contain a cases array")
    runtime = FinanceDataToolRuntimeService(trade_date_resolver=TradeDateResolver())
    rows = []
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        row = evaluate_case(
            case,
            runtime=runtime,
            execute=not args.no_execute,
            timeout_seconds=args.case_timeout_seconds,
        )
        rows.append(row)
        print(
            f"[{len(rows)}/{len(cases)}] {row['case_id']} "
            f"static={row['static_status']} execution={row['execution_status']} "
            f"elapsed_ms={row['elapsed_ms']}",
            flush=True,
        )
    output = {
        "eval_name": str(payload.get("name") or "finance_api_static_validation"),
        "generated_at": _now(),
        "scope": str(payload.get("scope") or ""),
        "semantic_intent_checked": False,
        "execution_enabled": not args.no_execute,
        "case_timeout_seconds": args.case_timeout_seconds,
        "timeout_supported": hasattr(signal, "setitimer"),
        "summary": summarize(rows),
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
