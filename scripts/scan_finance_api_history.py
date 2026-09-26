#!/usr/bin/env python3
"""Scan saved evaluation artifacts for model-authored finance API strings.

This is an intrinsic contract scan.  Historical result rows are not replayed;
referenced rN.column names are synthesized only to avoid classifying an absent
session as an API/dataview error.  Bare-reference syntax and all other
mechanical validation still run normally.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


CALL_LINE_RE = re.compile(
    r"(?m)^\s*(r\d+\s*=\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\s*\([^\r\n]*\)\s*->\s*[^\r\n]+?)\s*$"
)
REF_RE = re.compile(r"\b(r\d+)\.([A-Za-z_]\w*)\b")


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _synthetic_previous(request: str, current_result_id: str) -> dict[str, ResultHandle]:
    columns: dict[str, set[str]] = defaultdict(set)
    for result_id, field in REF_RE.findall(request):
        if result_id != current_result_id:
            columns[result_id].add(field)
    return {
        result_id: ResultHandle(
            name=result_id,
            api="historical.synthetic",
            columns=sorted(fields),
            data={"status": "ok", "columns": sorted(fields), "rows": []},
        )
        for result_id, fields in columns.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "finance_api_history_static_scan_20260825.json",
    )
    args = parser.parse_args()

    occurrences: dict[str, set[str]] = defaultdict(set)
    json_file_count = 0
    unreadable_file_count = 0
    for base in args.paths:
        files = [base] if base.is_file() else base.rglob("*.json")
        for path in files:
            if path.name.startswith("._"):
                continue
            json_file_count += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                unreadable_file_count += 1
                continue
            for text in _strings(payload):
                for match in CALL_LINE_RE.finditer(text):
                    request = match.group(1).strip()
                    resolved_path = path.resolve()
                    try:
                        source_name = str(resolved_path.relative_to(ROOT))
                    except ValueError:
                        source_name = str(resolved_path)
                    occurrences[request].add(source_name)

    rows: list[dict[str, Any]] = []
    for request, source_files in sorted(occurrences.items()):
        row: dict[str, Any] = {
            "request": request,
            "occurrence_file_count": len(source_files),
            "source_files": sorted(source_files),
            "parse_status": "pending",
            "static_status": "pending",
            "static_errors": [],
        }
        try:
            call = parse_api_call(request)
        except Exception as exc:  # noqa: BLE001 - preserve historical evidence.
            row["parse_status"] = "failed"
            row["static_status"] = "failed"
            row["static_errors"] = [f"PARSE_ERROR: {exc}"]
        else:
            row["parse_status"] = "passed"
            previous = _synthetic_previous(request, call.result_id)
            validation = validate_call(call, previous)
            row["static_status"] = "passed" if validation.ok else "failed"
            row["static_errors"] = list(validation.errors)
        rows.append(row)

    static_failed = [row for row in rows if row["static_status"] == "failed"]
    output = {
        "scan_name": "finance_api_historical_intrinsic_contract_scan_20260825",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "paths": [str(path) for path in args.paths],
        "scope_note": (
            "Unique API strings extracted from saved JSON artifacts. Result-reference "
            "columns are synthesized; this scan does not evaluate user-intent equivalence "
            "or replay live Providers."
        ),
        "summary": {
            "json_file_count": json_file_count,
            "unreadable_file_count": unreadable_file_count,
            "unique_api_string_count": len(rows),
            "static_pass_count": len(rows) - len(static_failed),
            "static_fail_count": len(static_failed),
        },
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
