#!/usr/bin/env python3
"""Re-run persisted finance DSL as read-only SQL and produce a compact audit JSON."""

from __future__ import annotations

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call


CALL_RE = re.compile(
    r"(?ms)^\s*(r\d+\s*=\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\(.*?\)\s*->\s*.*?)(?=^\s*r\d+\s*=|\Z)"
)
_capture = threading.local()
_original_execute = pymysql.cursors.Cursor.execute


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def normalized_sql(sql: Any) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip()


def capture_execute(self: Any, query: Any, args: Any = None) -> Any:
    bucket = getattr(_capture, "queries", None)
    if bucket is not None:
        bucket.append(
            {
                "sql": normalized_sql(query),
                "params": [json_value(v) for v in (args or ())],
            }
        )
    return _original_execute(self, query, args)


def call_texts(case: dict[str, Any]) -> list[str]:
    calls: list[str] = []
    for request in case.get("query_requests") or []:
        matches = [m.group(1).strip() for m in CALL_RE.finditer(str(request))]
        if matches:
            calls.extend(matches)
        else:
            calls.append(str(request).strip())
    return [item for item in calls if item]


def compact_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for item in case.get("raw_results") or []:
        output.append(
            {
                "result_name": item.get("result_name"),
                "api": item.get("api"),
                "row_count": int(item.get("row_count") or 0),
                "schema": item.get("schema") or {},
                "examples": (item.get("rows") or [])[:2],
            }
        )
    return output


def audit_case(case: dict[str, Any]) -> dict[str, Any]:
    handles: dict[str, Any] = {}
    executions: list[dict[str, Any]] = []
    for text in call_texts(case):
        _capture.queries = []
        try:
            call = parse_api_call(text)
            handle = execute_api_call(call, handles)
            handles[handle.name] = handle
            data = handle.data if isinstance(handle.data, dict) else {}
            rows = data.get("rows") if isinstance(data.get("rows"), list) else []
            executions.append(
                {
                    "result_name": handle.name,
                    "api": handle.api,
                    "status": data.get("status", ""),
                    "row_count": int(data.get("row_count", len(rows)) or 0),
                    "sql": list(getattr(_capture, "queries", []) or []),
                    "error": str(data.get("reason") or ""),
                }
            )
        except Exception as exc:  # noqa: BLE001 - audit must preserve per-call evidence.
            executions.append(
                {
                    "result_name": "",
                    "api": "",
                    "status": "audit_error",
                    "row_count": None,
                    "sql": list(getattr(_capture, "queries", []) or []),
                    "error": str(exc),
                }
            )
        finally:
            _capture.queries = None

    original = compact_rows(case)
    original_counts = [item["row_count"] for item in original]
    current_counts = [item["row_count"] for item in executions if item["row_count"] is not None]
    old_zero = bool(original_counts) and sum(original_counts) == 0
    any_old_zero = any(value == 0 for value in original_counts)
    current_total = sum(current_counts)
    if old_zero and current_total == 0:
        verdict = "原0行成立"
        evidence = "当前直连SQL仍为0；完全匹配条件在库中无记录"
    elif old_zero and current_total > 0:
        verdict = "原0行不再成立"
        evidence = f"当前直连SQL命中{current_total}行；数据已补充或原运行时点/连接状态不同"
    elif any_old_zero and current_total > 0:
        verdict = "部分0行需分数据集看"
        evidence = f"原结果含0行数据集；当前全部SQL合计命中{current_total}行"
    elif current_total > 0:
        verdict = "非0结果有DB证据"
        evidence = f"当前直连SQL合计命中{current_total}行"
    else:
        verdict = "需复核"
        evidence = "当前SQL无命中或执行异常"

    return {
        "case_id": case.get("case_id"),
        "question": case.get("question"),
        "elapsed_ms": case.get("elapsed_ms"),
        "selected_tools": case.get("api_sequence") or [],
        "original_datasets": original,
        "db_executions": executions,
        "structured_analysis": {
            "目标": str(case.get("question") or "")[:160],
            "路由": " → ".join(case.get("api_sequence") or []),
            "证据": evidence,
            "结论": verdict,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis")
    parser.add_argument("output")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    source = json.loads(Path(args.analysis).read_text())
    cases = source.get("cases") or []
    pymysql.cursors.Cursor.execute = capture_execute
    completed: dict[str, dict[str, Any]] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(audit_case, case): case.get("case_id") for case in cases}
            for index, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                completed[str(result["case_id"])] = result
                if index % 20 == 0 or index == len(cases):
                    print(f"audited {index}/{len(cases)}", flush=True)
    finally:
        pymysql.cursors.Cursor.execute = _original_execute
    ordered = [completed[str(case.get("case_id"))] for case in cases]
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_analysis": str(Path(args.analysis).resolve()),
        "case_count": len(ordered),
        "cases": ordered,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, default=json_value, indent=2))


if __name__ == "__main__":
    main()
