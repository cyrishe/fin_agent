from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


AGG_METHODS = {"sum", "avg", "max", "min", "median", "count"}
AGG_SPEC_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*\(\s*([^)]+?)\s*\)\s*$")
AGG_TUPLE_RE = re.compile(r"^\s*\(\s*([A-Za-z_]\w*)\s*,\s*([^)]+?)\s*\)\s*$")


@dataclass(frozen=True)
class AggSpec:
    method: str
    metric: str

    @property
    def metric_column(self) -> str:
        return metric_column(self.metric)


def parse_agg_spec(args: Mapping[str, Any]) -> AggSpec:
    """Parse new `agg = avg(stock.quote.pct)` and legacy `metric + agg` forms."""
    raw_agg = str(args.get("agg") or "").strip()
    match = AGG_SPEC_RE.fullmatch(raw_agg) or AGG_TUPLE_RE.fullmatch(raw_agg)
    if match:
        method, metric = match.groups()
        return AggSpec(method=method.strip().lower(), metric=metric.strip())
    return AggSpec(method=raw_agg.lower(), metric=str(args.get("metric") or "").strip())


def metric_column(metric: str) -> str:
    text = str(metric or "").strip()
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def output_alias(outputs: list[str], *, default: str, exclude: set[str] | None = None) -> str:
    excluded = exclude or set()
    for output in outputs:
        text = str(output or "").strip()
        if " as " in text:
            return text.split(" as ", 1)[1].strip()
        if AGG_SPEC_RE.fullmatch(text):
            return metric_column(text)
        if re.fullmatch(r"[A-Za-z_]\w*", text) and text not in excluded:
            return text
    return default
