#!/usr/bin/env python3
"""Build a reproducible 100-case routing sample from the report benchmark.

The report benchmark workbook already contains a human-reviewed protocol entry
for every question.  This script reads the checked-in inspection artifact so
the sampling step does not depend on Excel libraries, excludes the ten cases
used in the earlier three-model comparison, and emits explicit routing gold.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT
    / "outputs"
    / "445d783c-799f-4185-b3fe-740a296ac4e8"
    / "研报评测集_数据可达性逐题分析.xlsx.inspect.ndjson"
)
DEFAULT_EXCLUSIONS = (
    ROOT / "outputs" / "report_qwen35_27b_sample10_20260831" / "cases.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "report_routing_embedding_experiment_20260831"
    / "cases_seed20260831_n100.json"
)
ENTRY_RE = re.compile(
    r"\b(stock|index|industry|plate|fund|bond|hot_event)\.([a-z][a-z0-9_]*)"
    r"(?:\.agg)?\b"
)


def _load_benchmark_rows(path: Path) -> list[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("kind") != "table" or item.get("sheet") != "逐题评估":
            continue
        values = item.get("values") or []
        if not values:
            break
        headers = [str(value or "").strip() for value in values[0]]
        return [dict(zip(headers, row)) for row in values[1:] if any(row)]
    raise ValueError(f"Could not find the 逐题评估 table in {path}")


def _excluded_questions(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return {
        str(case.get("question") or "").strip()
        for case in cases
        if isinstance(case, dict) and str(case.get("question") or "").strip()
    }


def _routing_entries(protocol_entry: str) -> list[str]:
    entries: list[str] = []
    for subject, dataview in ENTRY_RE.findall(protocol_entry or ""):
        entry = f"{subject}.{dataview}"
        if entry not in entries:
            entries.append(entry)
    return entries


def build_cases(
    *, source_path: Path, exclusions_path: Path, seed: int, sample_size: int
) -> dict[str, Any]:
    rows = _load_benchmark_rows(source_path)
    exclusions = _excluded_questions(exclusions_path)
    candidates: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for row in rows:
        question = str(row.get("原问题") or "").strip()
        if not question or question in exclusions or question in seen_questions:
            continue
        seen_questions.add(question)
        candidates.append(row)
    if sample_size > len(candidates):
        raise ValueError(
            f"sample_size={sample_size} exceeds candidate_count={len(candidates)}"
        )
    sampled = random.Random(seed).sample(candidates, sample_size)
    cases: list[dict[str, Any]] = []
    for sample_ordinal, row in enumerate(sampled, start=1):
        original_ordinal = int(row["序号"])
        protocol_entry = str(row.get("建议协议入口") or "").strip()
        required_entries = _routing_entries(protocol_entry)
        if not required_entries:
            raise ValueError(
                f"Question {original_ordinal} has no catalog entry: {protocol_entry!r}"
            )
        cases.append(
            {
                "case_id": f"RTE{sample_ordinal:03d}",
                "source_ordinal": original_ordinal,
                "question": str(row.get("原问题") or "").strip(),
                "category": str(row.get("一级类别") or "").strip(),
                "data_shape": str(row.get("数据需求形态") or "").strip(),
                "protocol_entry_raw": protocol_entry,
                "accessibility": str(row.get("数据可达性") or "").strip(),
                "primary_metric_code": str(row.get("主指标代码") or "").strip(),
                "primary_entry": required_entries[0],
                "acceptable_first_entries": required_entries,
                "required_entries": required_entries,
                "gold_basis": "human_reviewed_protocol_entry",
            }
        )
    category_counts = Counter(case["category"] for case in cases)
    primary_counts = Counter(case["primary_entry"] for case in cases)
    return {
        "benchmark_name": "report_routing_embedding_experiment_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source_path),
        "source_case_count": len(rows),
        "excluded_case_count": len(exclusions),
        "candidate_count": len(candidates),
        "sample_seed": seed,
        "sample_size": sample_size,
        "sampling_method": (
            "simple_random_without_replacement_after_excluding_prior_10_"
            "and_deduplicating_question_text"
        ),
        "category_counts": dict(sorted(category_counts.items())),
        "primary_entry_counts": dict(sorted(primary_counts.items())),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--sample-size", type=int, default=100)
    args = parser.parse_args()
    payload = build_cases(
        source_path=args.source.resolve(),
        exclusions_path=args.exclusions.resolve(),
        seed=args.seed,
        sample_size=args.sample_size,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in payload.items() if key != "cases"}, ensure_ascii=False, indent=2))
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
