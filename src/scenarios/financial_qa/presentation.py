from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from src.services.finance_data_tool_catalog_service import (
    FinanceDataToolCatalogService,
)

_TIME_COLUMNS = (
    "minute_time",
    "snapshot_time",
    "time",
    "datetime",
    "timestamp",
    "report_date",
    "tradedate",
    "trade_date",
    "date",
)
_IDENTITY_COLUMNS = {
    "code",
    "name",
    "symbol",
    "security_code",
    "security_name",
    "stock_code",
    "stock_name",
    *_TIME_COLUMNS,
}
_CANDLE_VALUE_COLUMNS = ("open", "high", "low", "close")
_CANDLE_OPTIONAL_COLUMNS = {
    "volume": ("volume", "volumn", "vol"),
    "amount": ("amount",),
    "pct": ("pct", "change_pct"),
}
_METRIC_PRIORITY_TOKENS = (
    "close",
    "price",
    "pct",
    "change",
    "differ",
    "amount",
    "balance",
    "net",
    "value",
)


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


class FinancialQaPresentationService:
    """Compile verified Financial QA result samples into Agent Surface blocks."""

    max_metrics = 6

    def __init__(
        self,
        *,
        catalog: FinanceDataToolCatalogService | None = None,
    ) -> None:
        self.catalog = catalog or FinanceDataToolCatalogService()

    def build(
        self,
        message: str,
        result_refs: Sequence[Mapping[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        evidence_blocks: list[dict[str, Any]] = []
        for index, result_ref in enumerate(result_refs or [], start=1):
            if not isinstance(result_ref, Mapping):
                continue
            evidence_blocks.extend(self._evidence_blocks(result_ref, index=index))
        narrative = self._display_narrative(
            str(message or ""),
            has_structured_evidence=bool(evidence_blocks),
        )
        return [self._narrative_block(narrative), *evidence_blocks]

    @classmethod
    def _display_narrative(
        cls,
        message: str,
        *,
        has_structured_evidence: bool,
    ) -> str:
        if not has_structured_evidence or not message.strip():
            return message
        lines = message.splitlines()
        kept: list[str] = []
        index = 0
        while index < len(lines):
            if (
                index + 1 < len(lines)
                and cls._looks_like_table_row(lines[index])
                and cls._looks_like_table_separator(lines[index + 1])
            ):
                while kept and not kept[-1].strip():
                    kept.pop()
                if kept and re.match(r"^\s{0,3}#{1,6}\s+", kept[-1]):
                    kept.pop()
                while index < len(lines) and cls._looks_like_table_row(lines[index]):
                    index += 1
                continue
            kept.append(lines[index])
            index += 1
        compact = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        return compact or message

    @staticmethod
    def _looks_like_table_row(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3

    @staticmethod
    def _looks_like_table_separator(line: str) -> bool:
        stripped = line.strip().strip("|")
        cells = [cell.strip() for cell in stripped.split("|")]
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)

    @staticmethod
    def _narrative_block(message: str) -> dict[str, Any]:
        return {
            "block_id": "financial_qa_answer",
            "block_type": "narrative",
            "kind": "narrative",
            "semantic": "finance.answer",
            "mode": "replace",
            "content": message,
            "payload": {
                "format": "markdown",
                "text": message,
            },
        }

    def _evidence_blocks(
        self,
        result_ref: Mapping[str, Any],
        *,
        index: int,
    ) -> list[dict[str, Any]]:
        columns, column_specs = self._columns(result_ref)
        rows = self._rows(result_ref)
        if not rows:
            return []

        api = _trim(result_ref.get("api"))
        goal = _trim(result_ref.get("goal"))
        catalog_specs = self._catalog_column_specs(api)
        column_specs = {
            name: {
                **dict(catalog_specs.get(name, {})),
                **dict(column_specs.get(name, {})),
            }
            for name in {*catalog_specs, *column_specs}
        }
        selection = (
            result_ref.get("step_evidence", {}).get("selection_applied", {})
            if isinstance(result_ref.get("step_evidence"), Mapping)
            and isinstance(
                result_ref.get("step_evidence", {}).get("selection_applied"),
                Mapping,
            )
            else {}
        )
        if (
            api == "stock.quote"
            and selection.get("realtime") == 2
            and "close" in column_specs
        ):
            column_specs["close"] = {
                **dict(column_specs["close"]),
                "label": "最新价",
            }
        row_count = self._row_count(result_ref, rows)
        meta = self._meta(
            row_count=row_count,
            sample_rows=len(rows),
            sample_complete=bool(result_ref.get("sample_complete")),
        )
        domain_context = self._domain_context(api, rows, columns)
        candles = self._candles(rows, columns)
        if candles:
            intraday = self._is_intraday(api, columns)
            semantic = "finance.intraday" if intraday else "finance.ohlcv"
            return [
                {
                    "block_id": f"financial_qa_evidence_{index}_trend",
                    "block_type": "data",
                    "kind": "data",
                    "semantic": semantic,
                    "mode": "replace",
                    "title": goal or "价格走势",
                    "payload": {
                        "shape": "timeseries",
                        "content_type": semantic,
                        "data": {"candles": candles},
                    },
                    "presentation_hint": {
                        "preferred_renderer": (
                            "finance.intraday" if intraday else "finance.kline"
                        ),
                    },
                    "domain_context": domain_context,
                    "meta": meta,
                }
            ]

        if len(rows) == 1 and not self._is_report_api(api):
            row = self._row_mapping(rows[0], columns)
            items = self._metric_items(row, columns, column_specs)
            if items:
                return [
                    {
                        "block_id": f"financial_qa_evidence_{index}_metrics",
                        "block_type": "data",
                        "kind": "data",
                        "semantic": self._metrics_semantic(api),
                        "mode": "replace",
                        "title": goal or "关键数据",
                        "payload": {
                            "shape": "record",
                            "content_type": "finance.metrics",
                            "data": {"items": items},
                        },
                        "presentation_hint": {
                            "preferred_renderer": "data.metrics",
                            "density": "compact",
                        },
                        "domain_context": domain_context,
                        "meta": meta,
                    }
                ]

        return [
            {
                "block_id": f"financial_qa_evidence_{index}_table",
                "block_type": "data",
                "kind": "data",
                "semantic": self._records_semantic(api),
                "mode": "replace",
                "title": goal or "数据证据",
                "payload": {
                    "shape": "records",
                    "content_type": self._records_semantic(api),
                    "data": {
                        "columns": columns,
                        "column_labels": {
                            name: _trim(column_specs.get(name, {}).get("label"))
                            or name
                            for name in columns
                        },
                        "column_meta": {
                            name: {
                                key: value
                                for key, value in {
                                    "label": _trim(
                                        column_specs.get(name, {}).get("label")
                                    )
                                    or name,
                                    "unit": _trim(
                                        column_specs.get(name, {}).get("unit")
                                    ),
                                }.items()
                                if value
                            }
                            for name in columns
                        },
                        "rows": deepcopy(rows),
                        "row_count": row_count,
                    },
                },
                "presentation_hint": {
                    "preferred_renderer": "data.table",
                    "density": "compact",
                },
                "domain_context": domain_context,
                "meta": meta,
            }
        ]

    @staticmethod
    def _columns(
        result_ref: Mapping[str, Any],
    ) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
        schema = (
            result_ref.get("schema")
            if isinstance(result_ref.get("schema"), Mapping)
            else {}
        )
        raw_columns = (
            schema.get("columns")
            if isinstance(schema.get("columns"), list)
            else []
        )
        columns: list[str] = []
        specs: dict[str, Mapping[str, Any]] = {}
        for item in raw_columns:
            if isinstance(item, Mapping):
                name = _trim(item.get("name"))
                if name and name not in columns:
                    columns.append(name)
                    specs[name] = item
            else:
                name = _trim(item)
                if name and name not in columns:
                    columns.append(name)

        if columns:
            return columns, specs

        sample = (
            result_ref.get("sample")
            if isinstance(result_ref.get("sample"), Mapping)
            else {}
        )
        rows = sample.get("rows") if isinstance(sample.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for name in row:
                text = _trim(name)
                if text and text not in columns:
                    columns.append(text)
        return columns, specs

    @staticmethod
    def _rows(result_ref: Mapping[str, Any]) -> list[Any]:
        sample = result_ref.get("sample")
        if isinstance(sample, Mapping):
            raw_rows = sample.get("rows")
        elif isinstance(sample, list):
            raw_rows = sample
        else:
            raw_rows = []
        if not isinstance(raw_rows, list):
            return []
        return [
            deepcopy(row)
            for row in raw_rows
            if isinstance(row, (Mapping, list, tuple))
        ]

    @staticmethod
    def _row_count(result_ref: Mapping[str, Any], rows: Sequence[Any]) -> int:
        value = result_ref.get("row_count")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return len(rows)

    @staticmethod
    def _meta(
        *,
        row_count: int,
        sample_rows: int,
        sample_complete: bool,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "row_count": row_count,
            "sample_row_count": sample_rows,
            "sample_complete": sample_complete,
        }
        if row_count > sample_rows:
            meta["partial"] = True
        return meta

    def _catalog_column_specs(
        self,
        api: str,
    ) -> dict[str, Mapping[str, Any]]:
        parts = [part for part in api.split(".") if part]
        if len(parts) < 2:
            return {}
        try:
            dataview = self.catalog.get_dataview(parts[0], parts[1])
        except (FileNotFoundError, KeyError, ValueError):
            return {}
        specs: dict[str, Mapping[str, Any]] = {}
        for field in dataview.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            name = _trim(field.get("name"))
            if not name:
                continue
            aliases = [
                _trim(item)
                for item in field.get("aliases") or []
                if _trim(item)
            ]
            label = self._field_label(name, aliases)
            unit = self._field_unit(aliases)
            specs[name] = {
                **dict(field),
                "label": label,
                **({"unit": unit} if unit else {}),
            }
        return specs

    @staticmethod
    def _field_label(name: str, aliases: Sequence[str]) -> str:
        for alias in aliases:
            label = re.split(r"[（(]", alias, maxsplit=1)[0].strip()
            if label and label.lower() != name.lower():
                return label
        return name

    @staticmethod
    def _field_unit(aliases: Sequence[str]) -> str:
        for alias in aliases:
            match = re.search(r"[（(]([^）)]+)[）)]", alias)
            if not match:
                continue
            candidate = re.split(r"[；;，,]", match.group(1), maxsplit=1)[0].strip()
            if candidate in {"元", "%", "股", "手", "万元", "亿元", "倍"}:
                return candidate
        return ""

    @classmethod
    def _domain_context(
        cls,
        api: str,
        rows: Sequence[Any],
        columns: Sequence[str],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {}
        if api:
            context["source"] = api
        if not rows:
            return context
        row = cls._row_mapping(rows[-1], columns)
        trade_date = _trim(row.get("tradedate") or row.get("trade_date") or row.get("date"))
        point_time = _trim(
            row.get("snapshot_time")
            or row.get("minute_time")
            or row.get("datetime")
            or row.get("timestamp")
            or row.get("report_date")
        )
        as_of = (
            f"{trade_date} {point_time}"
            if trade_date and point_time and not point_time.startswith(trade_date)
            else point_time or trade_date
        )
        if as_of:
            context["as_of"] = as_of
        return context

    @staticmethod
    def _row_mapping(row: Any, columns: Sequence[str]) -> dict[str, Any]:
        if isinstance(row, Mapping):
            return dict(row)
        if isinstance(row, (list, tuple)):
            return {
                name: row[index]
                for index, name in enumerate(columns)
                if index < len(row)
            }
        return {}

    def _metric_items(
        self,
        row: Mapping[str, Any],
        columns: Sequence[str],
        column_specs: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        ordered_names = list(columns) or [_trim(name) for name in row]
        original_positions = {
            name: index for index, name in enumerate(ordered_names)
        }
        ordered_names.sort(
            key=lambda name: (
                next(
                    (
                        index
                        for index, token in enumerate(_METRIC_PRIORITY_TOKENS)
                        if token in name.lower()
                    ),
                    len(_METRIC_PRIORITY_TOKENS),
                ),
                original_positions[name],
            )
        )
        items: list[dict[str, Any]] = []
        for name in ordered_names:
            if not name or name.lower() in _IDENTITY_COLUMNS or name not in row:
                continue
            value = row[name]
            if value is None or isinstance(value, (Mapping, list, tuple)):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue
            if isinstance(value, str) and (
                len(value) > 80 or "\n" in value or "\r" in value
            ):
                continue
            spec = column_specs.get(name, {})
            item: dict[str, Any] = {
                "id": name,
                "label": _trim(
                    spec.get("label")
                    or spec.get("title")
                    or spec.get("display_name")
                )
                or name,
                "value": deepcopy(value),
            }
            unit = _trim(spec.get("unit"))
            if unit:
                item["unit"] = unit
            items.append(item)
            if len(items) >= self.max_metrics:
                break
        return items

    def _candles(
        self,
        rows: Sequence[Any],
        columns: Sequence[str],
    ) -> list[dict[str, Any]]:
        if len(rows) < 2:
            return []
        mapped_rows = [self._row_mapping(row, columns) for row in rows]
        if any(not row for row in mapped_rows):
            return []

        available_names: list[str] = list(columns)
        for row in mapped_rows:
            for name in row:
                text = _trim(name)
                if text and text not in available_names:
                    available_names.append(text)
        name_by_lower = {name.lower(): name for name in available_names}
        time_name = next(
            (name_by_lower[name] for name in _TIME_COLUMNS if name in name_by_lower),
            "",
        )
        value_names = {
            name: name_by_lower.get(name, "")
            for name in _CANDLE_VALUE_COLUMNS
        }
        if not time_name or any(not value for value in value_names.values()):
            return []

        optional_names = {
            target: next(
                (
                    name_by_lower[candidate]
                    for candidate in candidates
                    if candidate in name_by_lower
                ),
                "",
            )
            for target, candidates in _CANDLE_OPTIONAL_COLUMNS.items()
        }
        candles: list[dict[str, Any]] = []
        for row in mapped_rows:
            time_value = row.get(time_name)
            values = {
                target: row.get(source)
                for target, source in value_names.items()
            }
            if not _trim(time_value) or any(
                not _is_finite_number(value) for value in values.values()
            ):
                return []
            candle: dict[str, Any] = {
                "time": _trim(time_value),
                **values,
            }
            for target, source in optional_names.items():
                value = row.get(source) if source else None
                if _is_finite_number(value):
                    candle[target] = value
            candles.append(candle)
        return candles

    @staticmethod
    def _is_intraday(api: str, columns: Sequence[str]) -> bool:
        normalized = api.lower()
        normalized_columns = {column.lower() for column in columns}
        return bool(
            normalized_columns.intersection(
                {"minute_time", "snapshot_time", "minute_index", "snapshot_slot"}
            )
        ) or any(
            token in normalized
            for token in ("intraday", "minute", "timeshare", "time_share")
        )

    @staticmethod
    def _is_report_api(api: str) -> bool:
        normalized = api.lower()
        return normalized == "report" or normalized.endswith(".report")

    @staticmethod
    def _metrics_semantic(api: str) -> str:
        normalized = api.lower()
        if normalized == "quote" or normalized.endswith(".quote"):
            return "finance.quote.metrics"
        if normalized == "margin" or normalized.endswith(".margin"):
            return "finance.margin.metrics"
        return "finance.metrics"

    @staticmethod
    def _records_semantic(api: str) -> str:
        normalized = api.lower()
        if normalized == "quote" or normalized.endswith(".quote"):
            return "finance.quote.records"
        if normalized == "margin" or normalized.endswith(".margin"):
            return "finance.margin.records"
        if normalized == "report" or normalized.endswith(".report"):
            return "finance.research.records"
        return "finance.records"
