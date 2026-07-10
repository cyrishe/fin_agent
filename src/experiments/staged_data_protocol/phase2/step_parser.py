from __future__ import annotations

from typing import Iterable, List

from src.experiments.staged_data_protocol.phase2.catalog import normalize_dataview
from src.experiments.staged_data_protocol.phase2.models import Step


def parse_step_line(line: str) -> Step:
    raw = str(line or "").strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 4:
        raise ValueError(f"step must have 4 pipe parts: {raw}")
    condition = parts[3]
    is_output = "(output)" in condition
    condition = condition.replace("(output)", "").strip()
    return Step(
        step_id=parts[0],
        subject=parts[1],
        dataview=normalize_dataview(parts[2]),
        condition_desc=condition,
        is_output=is_output,
        raw=raw,
    )


def parse_steps(lines: Iterable[str]) -> List[Step]:
    return [parse_step_line(line) for line in lines if str(line or "").strip()]

