"""Deterministic read views over saved results; originals and references stay intact."""
from __future__ import annotations

from typing import Any, Mapping

from src.experiments.staged_data_protocol.phase2 import python_filter as pf
from src.experiments.staged_data_protocol.phase2.call_structure import parse_filter_expression, parse_order


def select_result_page(
    materialized: Mapping[str, Any], *, filter_text: str = "", order: str = "",
    offset: int = 0, limit: int = 10, previous_results: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if materialized.get("data_type") != "table":
        raise ValueError("filter/order require a saved table result")
    rows = list(materialized.get("rows") or [])
    manifest = materialized.get("manifest") or {}
    fields = {c["name"] for c in (manifest.get("schema") or {}).get("columns", []) if isinstance(c, Mapping) and c.get("name")}
    fields.update(key for row in rows for key in row)
    expression = parse_filter_expression(filter_text)
    if expression is not None:
        expression = pf.bind_references(expression, previous_results or {})
    for predicate in pf.predicates(expression):
        if predicate["field"] not in fields:
            raise ValueError(f"unknown result field={predicate['field']}; available={sorted(fields)}")
        if predicate["operator"] not in {"=", "==", "!=", ">", ">=", "<", "<=", "in", "not in", "contains"}:
            raise ValueError(f"unsupported filter operator={predicate['operator']}")
    ordering = parse_order(order)
    for item in ordering:
        if item["field"] not in fields:
            raise ValueError(f"unknown order field={item['field']}; available={sorted(fields)}")
    selected = [row for row in rows if pf.evaluate(expression, row)]
    # Stable multi-column ordering with missing values last in either direction.
    for item in reversed(ordering):
        field = item["field"]
        valued = [row for row in selected if row.get(field) is not None]
        missing = [row for row in selected if row.get(field) is None]
        try:
            selected = sorted(valued, key=lambda row: row[field], reverse=item["direction"] == "desc") + missing
        except TypeError as exc:
            raise ValueError(f"result field={field} contains incomparable values") from exc
    start, size = max(0, int(offset)), max(1, min(50, int(limit)))
    page = selected[start:start + size]
    return {
        "rows": page,
        "page": {"offset": start, "limit": size, "returned": len(page), "total": len(selected), "has_more": start + len(page) < len(selected)},
        "selection": {"filter": filter_text, "order": order, "source_row_count": len(rows)},
    }
