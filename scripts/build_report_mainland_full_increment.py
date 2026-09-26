#!/usr/bin/env python3
"""Build the full mainland-report benchmark and its not-yet-run no-news increment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def entries(value: Any) -> list[str]:
    result: list[str] = []
    for raw in str(value or "").split("|"):
        item = raw.strip()
        if item.endswith(".agg"):
            item = item[:-4]
        if item and item != "stock.news" and item not in result:
            result.append(item)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", type=Path, required=True)
    parser.add_argument("--already-run", type=Path, required=True)
    parser.add_argument("--full-output", type=Path, required=True)
    parser.add_argument("--increment-output", type=Path, required=True)
    args = parser.parse_args()
    values = None
    for line in args.inspect.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("kind") == "table" and item.get("sheet") == "全量Gold":
            values = item.get("values") or []
            break
    if not values or len(values) != 203:
        raise ValueError("expected the reviewed 200-row mainland Full Gold table")
    cases = []
    excluded_news = []
    for row in values[3:]:
        ordinal = int(row[0])
        acceptable = entries(row[6])
        required = entries(row[7])
        primary_candidates = entries(row[5])
        primary = primary_candidates[0] if primary_candidates else (acceptable[0] if acceptable else "")
        if not primary and not acceptable and not required:
            excluded_news.append(ordinal)
            continue
        if not acceptable:
            acceptable = list(required or primary_candidates)
        if not required:
            required = list(acceptable)
        cases.append({
            "case_id": f"RTEF{ordinal:03d}",
            "source_ordinal": ordinal,
            "question": str(row[1] or "").strip(),
            "category": str(row[2] or "").strip(),
            "data_source_nature": str(row[3] or "").strip(),
            "data_task_type": str(row[4] or "").strip(),
            "protocol_entry_raw": str(row[5] or "").strip(),
            "primary_entry": primary,
            "acceptable_first_entries": acceptable,
            "required_entries": required,
            "accessibility": str(row[12] or "").strip(),
            "current_data_gap": str(row[13] or "").strip(),
            "gold_basis": "human_reviewed_mainland_full_gold_20260902_no_news",
        })
    previous = json.loads(args.already_run.read_text(encoding="utf-8"))
    previous_ordinals = {int(item["source_ordinal"]) for item in previous.get("cases") or []}
    increment = [item for item in cases if item["source_ordinal"] not in previous_ordinals]
    common = {
        "generated_at": "2026-09-03",
        "source": str(args.inspect.resolve()),
        "source_case_count": 200,
        "news_only_excluded_count": len(excluded_news),
        "news_only_excluded_ordinals": excluded_news,
        "mainland_scope": {
            "rule": "all named company subjects are Shanghai, Shenzhen, or Beijing listed after the reviewed 26-case replacement audit",
            "replacement_count": 26,
        },
    }
    full = {**common, "benchmark_name": "report_mainland_full_no_news", "sample_size": len(cases), "cases": cases}
    delta = {
        **common,
        "benchmark_name": "report_mainland_full_no_news_increment_after_67",
        "already_run_count": len(previous_ordinals),
        "sample_size": len(increment),
        "cases": increment,
    }
    args.full_output.parent.mkdir(parents=True, exist_ok=True)
    args.full_output.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.increment_output.write_text(json.dumps(delta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"full": len(cases), "increment": len(increment), "news_only_excluded": len(excluded_news)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
