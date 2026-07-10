from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.services.file_artifact_service import FileArtifactService


class QuantDataProviderError(Exception):
    def __init__(self, failure_kind: str, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message


QueryExecutor = Callable[[Dict[str, Any], Dict[str, Any]], List[Dict[str, Any]]]


class QuantDataProviderService:
    TOOL_NAME = "quant_data_provider"
    DEFAULT_REGISTRY_PATH = Path("docs/dbinfo/quant_screening_dbinfo.json")
    SOURCE_ROLE_MAP: Dict[str, List[str]] = {
        "daily_price": ["daily_price_volume"],
        "minute_price": ["intraday_market_snapshot"],
        "moneyflow": ["capital_flow"],
        "valuation": ["valuation"],
        "financial_indicator": ["financial_factor"],
        "cashflow": ["financial_statement_cashflow"],
        "concept_signal": ["stock_concept_signal", "stock_plate_membership", "concept_hotness"],
        "news": ["news_or_web_evidence", "legacy_url_evidence"],
    }
    FINANCIAL_SOURCES = {"financial_indicator", "cashflow"}

    def __init__(
        self,
        *,
        data_root: str | Path = "data",
        registry_path: str | Path | None = None,
        file_artifact_service: Optional[FileArtifactService] = None,
        query_executor: Optional[QueryExecutor] = None,
        source_status_overrides: Optional[Dict[str, str]] = None,
    ) -> None:
        self.file_artifact_service = file_artifact_service or FileArtifactService(data_root=data_root)
        self.registry_path = Path(registry_path) if registry_path else self.DEFAULT_REGISTRY_PATH
        self.query_executor = query_executor
        self.source_status_overrides = dict(source_status_overrides or {})
        self._registry = self._load_registry()
        self._sources = self._build_source_registry(self._registry)

    def prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        max_preview_rows = FileArtifactService.bounded_preview_count(payload.get("max_preview_rows"))
        universe_rows = self._normalize_universe(payload.get("universe"))
        if not universe_rows:
            raise QuantDataProviderError("empty_universe", "universe must contain at least one valid stock code")

        date_range = self._normalize_date_range(payload.get("date_range"))
        report_period = self._normalize_period_range(payload.get("report_period"))
        required_sources = self._normalize_required_sources(payload.get("required_data"))

        artifacts: List[Dict[str, Any]] = []
        schemas: Dict[str, Any] = {}
        samples: Dict[str, List[Dict[str, Any]]] = {}
        source_coverage: Dict[str, Any] = {}
        gaps: List[Dict[str, Any]] = []

        universe_manifest = self._write_table(
            source_id="universe",
            rows=universe_rows,
            columns=self._columns_from_rows(universe_rows),
            max_preview_rows=max_preview_rows,
            source_meta={
                "source_type": "quant_provider",
                "source_id": "universe",
                "file_name": "universe.jsonl",
                "mime_type": FileArtifactService.TABLE_CONTENT_TYPE,
                "size_bytes": 0,
                "sha256": "",
            },
        )
        artifacts.append(self._artifact_summary("universe", universe_manifest))
        schemas["universe"] = {"columns": [column["name"] for column in universe_manifest.get("columns", [])]}
        samples["universe"] = universe_rows[:max_preview_rows]

        for source_id in required_sources:
            if source_id == "universe":
                continue
            source = self._sources.get(source_id)
            if not source:
                gap = self._gap(source_id, "unsupported_source", f"unsupported quant data source: {source_id}")
                source_coverage[source_id] = gap
                gaps.append(gap)
                samples[source_id] = []
                continue
            executable, failure_kind, message = self._is_executable_source(source)
            if not executable:
                gap = self._gap(source_id, failure_kind, message, source=source)
                source_coverage[source_id] = gap
                gaps.append(gap)
                samples[source_id] = []
                continue
            rows, query_failure = self._query_source(
                source=source,
                universe_rows=universe_rows,
                date_range=date_range,
                report_period=report_period,
            )
            if query_failure:
                source_coverage[source_id] = query_failure
                gaps.append(query_failure)
                samples[source_id] = []
                continue
            rows = self._filter_rows(source=source, rows=rows, universe_rows=universe_rows, date_range=date_range, report_period=report_period)
            if not rows:
                gap = self._gap(source_id, "empty_result", "query returned no rows after provider filtering", source=source)
                source_coverage[source_id] = gap
                gaps.append(gap)
                samples[source_id] = []
                continue
            columns = self._columns_from_rows(rows, fallback_fields=source.get("core_fields") or [])
            manifest = self._write_table(
                source_id=source_id,
                rows=rows,
                columns=columns,
                max_preview_rows=max_preview_rows,
                source_meta=self._source_file_meta(source),
            )
            artifacts.append(self._artifact_summary(source_id, manifest, source=source))
            schemas[source_id] = {"columns": [column["name"] for column in columns]}
            samples[source_id] = rows[:max_preview_rows]
            source_coverage[source_id] = {
                "source_id": source_id,
                "status": "materialized",
                "failure_kind": "",
                "row_count": manifest.get("row_count", 0),
                "dbinfo_status": source.get("status"),
                "database": source.get("database"),
                "table": source.get("table"),
                "grain": source.get("grain"),
                "physical_format": manifest.get("physical_format"),
            }

        return {
            "artifacts": artifacts,
            "data_coverage": {
                "universe": {
                    "input_count": self._input_universe_count(payload.get("universe")),
                    "normalized_count": len(universe_rows),
                    "normalized_codes": [row["stk_code"] for row in universe_rows],
                },
                "date_range": date_range,
                "report_period": report_period,
                "required_data": required_sources,
                "gaps": gaps,
            },
            "source_coverage": source_coverage,
            "schemas": schemas,
            "samples": samples,
            "render_blocks": self._render_blocks(artifacts=artifacts, source_coverage=source_coverage),
        }

    def _load_registry(self) -> Dict[str, Any]:
        if not self.registry_path.exists():
            return {}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _build_source_registry(self, registry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        tables = []
        for database in registry.get("databases") or []:
            if not isinstance(database, dict):
                continue
            for table in database.get("tables") or []:
                if isinstance(table, dict):
                    tables.append(table)

        sources: Dict[str, Dict[str, Any]] = {}
        for source_id, roles in self.SOURCE_ROLE_MAP.items():
            match = next((table for table in tables if table.get("data_role") in roles), None)
            if not match:
                continue
            source = dict(match)
            source["source_id"] = source_id
            if source_id in self.source_status_overrides:
                source["status"] = self.source_status_overrides[source_id]
            sources[source_id] = source
        return sources

    def _is_executable_source(self, source: Dict[str, Any]) -> tuple[bool, str, str]:
        source_id = str(source.get("source_id") or "")
        status = str(source.get("status") or "").strip()
        if status != "verified":
            return False, "source_unverified", f"source {source_id} is {status or 'unknown'}, not verified"
        if not self.query_executor:
            return False, "query_executor_missing", "no provider-owned query executor is configured"
        missing = []
        if not source.get("entity_fields"):
            missing.append("entity_fields")
        if not source.get("core_fields"):
            missing.append("core_fields")
        if source_id not in self.FINANCIAL_SOURCES and not source.get("time_fields"):
            missing.append("time_fields")
        if missing:
            return False, "missing_field", f"source registry is missing required fields: {', '.join(missing)}"
        return True, "", ""

    def _query_source(
        self,
        *,
        source: Dict[str, Any],
        universe_rows: List[Dict[str, Any]],
        date_range: Dict[str, str],
        report_period: Dict[str, str],
    ) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        request = {
            "source_id": source.get("source_id"),
            "database": source.get("database"),
            "table": source.get("table"),
            "entity_fields": source.get("entity_fields") or [],
            "time_fields": source.get("time_fields") or [],
            "core_fields": source.get("core_fields") or [],
            "universe_codes": [row["stk_code"] for row in universe_rows],
            "date_range": date_range,
            "report_period": report_period,
        }
        try:
            rows = self.query_executor(source, request) if self.query_executor else []
        except Exception as exc:
            return [], self._gap(str(source.get("source_id") or ""), "query_failed", str(exc), source=source)
        if not isinstance(rows, list):
            return [], self._gap(str(source.get("source_id") or ""), "query_failed", "query executor must return a list of rows", source=source)
        return [dict(row) for row in rows if isinstance(row, dict)], None

    def _filter_rows(
        self,
        *,
        source: Dict[str, Any],
        rows: List[Dict[str, Any]],
        universe_rows: List[Dict[str, Any]],
        date_range: Dict[str, str],
        report_period: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        allowed_codes = {row["code"] for row in universe_rows} | {row["stk_code"] for row in universe_rows}
        filtered = []
        for row in rows:
            row_code = self._row_code(row, source.get("entity_fields") or [])
            if row_code:
                normalized = self._normalize_stock_code(row_code)
                if not normalized or (normalized["code"] not in allowed_codes and normalized["stk_code"] not in allowed_codes):
                    continue
            if date_range and not self._row_in_date_range(row, source.get("time_fields") or [], date_range):
                continue
            if source.get("source_id") in self.FINANCIAL_SOURCES and report_period:
                if not self._row_in_report_period(row, report_period):
                    continue
            filtered.append(row)
        return filtered

    def _row_code(self, row: Dict[str, Any], entity_fields: List[str]) -> str:
        for field in [*entity_fields, "stk_code", "stock_code", "code"]:
            value = str(row.get(field) or "").strip()
            if value:
                return value
        return ""

    def _row_in_date_range(self, row: Dict[str, Any], time_fields: List[str], date_range: Dict[str, str]) -> bool:
        if not date_range:
            return True
        candidates = [field for field in [*time_fields, "trade_date", "date"] if field in row]
        if not candidates:
            return True
        for field in candidates:
            normalized = self._normalize_date(row.get(field))
            if not normalized:
                continue
            if date_range.get("start") and normalized < date_range["start"]:
                continue
            if date_range.get("end") and normalized > date_range["end"]:
                continue
            return True
        return False

    def _row_in_report_period(self, row: Dict[str, Any], report_period: Dict[str, str]) -> bool:
        candidates = ["report_period", "report_date", "end_date", "enddate"]
        present = [field for field in candidates if field in row]
        if not present:
            return True
        for field in present:
            normalized = self._normalize_period(row.get(field))
            if not normalized:
                continue
            if report_period.get("start") and normalized < report_period["start"]:
                continue
            if report_period.get("end") and normalized > report_period["end"]:
                continue
            return True
        return False

    def _write_table(
        self,
        *,
        source_id: str,
        rows: List[Dict[str, Any]],
        columns: List[Dict[str, Any]],
        max_preview_rows: int,
        source_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.file_artifact_service.write_table_artifact(
            columns=columns,
            rows=rows,
            preview_rows=rows[:max_preview_rows],
            source_file=source_meta,
            created_by_tool=self.TOOL_NAME,
            table_id=source_id,
        )

    def _artifact_summary(self, source_id: str, manifest: Dict[str, Any], *, source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        summary = {
            "source_id": source_id,
            "artifact_ref": manifest.get("artifact_ref"),
            "artifact_id": manifest.get("artifact_id"),
            "kind": manifest.get("kind"),
            "physical_format": manifest.get("physical_format"),
            "row_count": manifest.get("row_count", 0),
            "columns": [column.get("name") for column in manifest.get("columns", []) if isinstance(column, dict)],
        }
        if source:
            summary.update({"database": source.get("database"), "table": source.get("table"), "dbinfo_status": source.get("status")})
        return summary

    def _source_file_meta(self, source: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source_type": "dbinfo_registry",
            "source_id": str(source.get("source_id") or ""),
            "file_name": f"{source.get('database')}.{source.get('table')}",
            "mime_type": FileArtifactService.TABLE_CONTENT_TYPE,
            "size_bytes": 0,
            "sha256": "",
        }

    def _gap(self, source_id: str, failure_kind: str, message: str, *, source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        gap = {
            "source_id": source_id,
            "status": "gap",
            "failure_kind": failure_kind,
            "message": message,
            "row_count": 0,
        }
        if source:
            gap.update(
                {
                    "dbinfo_status": source.get("status"),
                    "database": source.get("database"),
                    "table": source.get("table"),
                    "grain": source.get("grain"),
                    "caveats": source.get("caveats") or [],
                }
            )
        return gap

    def _normalize_required_sources(self, raw: Any) -> List[str]:
        if raw is None or raw == "":
            return []
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            items = []
        normalized = []
        for item in items:
            value = str(item or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _normalize_universe(self, raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, dict):
            for key in ("stocks", "codes", "items", "universe"):
                if isinstance(raw.get(key), list):
                    raw = raw.get(key)
                    break
            else:
                raw = [raw]
        elif isinstance(raw, str):
            raw = [item for item in re.split(r"[,，\s]+", raw) if item]
        elif not isinstance(raw, (list, tuple)):
            raw = []

        rows = []
        seen = set()
        for item in raw:
            original = item
            if isinstance(item, dict):
                code_value = item.get("stk_code") or item.get("stock_code") or item.get("code") or item.get("symbol")
                original = code_value
            else:
                code_value = item
            normalized = self._normalize_stock_code(code_value)
            if not normalized or normalized["stk_code"] in seen:
                continue
            seen.add(normalized["stk_code"])
            rows.append(
                {
                    "code": normalized["code"],
                    "stk_code": normalized["stk_code"],
                    "exchange": normalized["exchange"],
                    "input_code": str(original or "").strip(),
                }
            )
        return rows

    def _normalize_stock_code(self, value: Any) -> Optional[Dict[str, str]]:
        raw = str(value or "").strip().upper()
        if not raw:
            return None
        raw = raw.replace("_", ".")
        prefix_match = re.match(r"^(SH|SZ|BJ)(\d{6})$", raw)
        if prefix_match:
            exchange, code = prefix_match.groups()
        else:
            suffix_match = re.match(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", raw)
            if not suffix_match:
                return None
            code, exchange = suffix_match.groups()
            if not exchange:
                exchange = self._infer_exchange(code)
        if not exchange:
            return None
        return {"code": code, "stk_code": f"{code}.{exchange}", "exchange": exchange}

    def _infer_exchange(self, code: str) -> str:
        if code.startswith(("6", "9")):
            return "SH"
        if code.startswith(("0", "2", "3")):
            return "SZ"
        if code.startswith(("4", "8")):
            return "BJ"
        return ""

    def _normalize_date_range(self, raw: Any) -> Dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        start = self._normalize_date(raw.get("start") or raw.get("start_date") or raw.get("from"))
        end = self._normalize_date(raw.get("end") or raw.get("end_date") or raw.get("to"))
        if start and end and start > end:
            raise QuantDataProviderError("invalid_date_range", "date_range start must be <= end")
        result = {}
        if start:
            result["start"] = start
        if end:
            result["end"] = end
        return result

    def _normalize_date(self, value: Any) -> str:
        if isinstance(value, dt.datetime):
            return value.date().isoformat()
        if isinstance(value, dt.date):
            return value.isoformat()
        raw = str(value or "").strip()
        if not raw:
            return ""
        if re.match(r"^\d{8}$", raw):
            return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return raw
        return ""

    def _normalize_period_range(self, raw: Any) -> Dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        start = self._normalize_period(raw.get("start") or raw.get("start_period") or raw.get("from"))
        end = self._normalize_period(raw.get("end") or raw.get("end_period") or raw.get("to"))
        if start and end and start > end:
            raise QuantDataProviderError("invalid_report_period", "report_period start must be <= end")
        result = {}
        if start:
            result["start"] = start
        if end:
            result["end"] = end
        return result

    def _normalize_period(self, value: Any) -> str:
        raw = str(value or "").strip().upper()
        if not raw:
            return ""
        quarter_match = re.match(r"^(\d{4})Q([1-4])$", raw)
        if quarter_match:
            year, quarter = quarter_match.groups()
            quarter_end = {"1": "0331", "2": "0630", "3": "0930", "4": "1231"}[quarter]
            return f"{year}{quarter_end}"
        if re.match(r"^\d{8}$", raw):
            return raw
        normalized_date = self._normalize_date(raw)
        if normalized_date:
            return normalized_date.replace("-", "")
        return ""

    def _columns_from_rows(self, rows: List[Dict[str, Any]], fallback_fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        names = []
        for row in rows:
            for key in row.keys():
                if key not in names:
                    names.append(key)
        for field in fallback_fields or []:
            if field not in names:
                names.append(str(field))
        return [{"name": name, "type": self._infer_type([row.get(name) for row in rows]), "index": index} for index, name in enumerate(names)]

    def _infer_type(self, values: List[Any]) -> str:
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool):
                return "boolean"
            if isinstance(value, int):
                return "integer"
            if isinstance(value, float):
                return "number"
            return "string"
        return "string"

    def _input_universe_count(self, raw: Any) -> int:
        if isinstance(raw, str):
            return len([item for item in re.split(r"[,，\s]+", raw) if item])
        if isinstance(raw, dict):
            for key in ("stocks", "codes", "items", "universe"):
                if isinstance(raw.get(key), list):
                    return len(raw.get(key))
            return 1
        if isinstance(raw, (list, tuple)):
            return len(raw)
        return 0

    def _render_blocks(self, *, artifacts: List[Dict[str, Any]], source_coverage: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        for artifact in artifacts:
            rows.append(
                {
                    "source_id": artifact.get("source_id"),
                    "status": "materialized",
                    "rows": artifact.get("row_count"),
                    "artifact_ref": artifact.get("artifact_ref"),
                }
            )
        for source_id, coverage in source_coverage.items():
            if coverage.get("status") == "gap":
                rows.append(
                    {
                        "source_id": source_id,
                        "status": "gap",
                        "failure_kind": coverage.get("failure_kind"),
                        "message": coverage.get("message"),
                    }
                )
        return [{"type": "table", "title": "Quant data provider coverage", "columns": ["source_id", "status", "rows", "artifact_ref", "failure_kind", "message"], "rows": rows}]

