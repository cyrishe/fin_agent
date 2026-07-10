import datetime as dt
from typing import Any, Dict, List

import pymysql

from src.subject_catalog import SubjectCatalog
from src.utils.mysql_utils import StockInfoDbUtils


TABLE_NAME = "aiia_indicator_series"
TOOL_NAME = "indicator_series_query"
DEFAULT_SUBJECT_CODE = "000001.SH"


class IndicatorSeriesTool:
    @staticmethod
    def _normalize_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
        try:
            normalized = int(value)
        except Exception:
            normalized = int(default)
        return max(minimum, min(maximum, normalized))

    def _normalize_indicator_ids(
        self,
        indicator_ids: List[Any] | None = None,
        indicator_names: List[Any] | None = None,
        indicator_name: str = "",
    ) -> List[str]:
        normalized_indicator_ids: List[str] = []
        seen = set()
        raw_items: List[Any] = []
        raw_items.extend(indicator_ids or [])
        raw_items.extend(indicator_names or [])
        if indicator_name:
            raw_items.append(indicator_name)
        for raw in raw_items:
            name = str(raw or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            normalized_indicator_ids.append(name)
        if not normalized_indicator_ids:
            raise ValueError("indicator_ids 不能为空")
        return normalized_indicator_ids

    def _resolve_subject(self, subject: str = "", subject_code: str = "", subject_name: str = "", subject_type: str = "") -> Dict[str, str]:
        record = SubjectCatalog.resolve(subject or "", subject_code=subject_code, subject_name=subject_name)
        if record:
            return {
                "subject_type": record.subject_type,
                "subject_code": record.subject_code,
                "subject_name": record.subject_name,
            }
        code = str(subject_code or "").strip()
        name = str(subject_name or "").strip()
        normalized_type = str(subject_type or "index").strip() or "index"
        if code:
            return {"subject_type": normalized_type, "subject_code": code, "subject_name": name}
        raise ValueError("当前必须提供可解析的标的；目录来自 stock_name.tsv 与 data/index_subjects.tsv 的合并解析")

    def _resolve_subjects(
        self,
        *,
        subject_codes: List[Any] | None = None,
        subject_code: str = "",
        subject: str = "",
        subject_name: str = "",
        subject_type: str = "",
    ) -> List[Dict[str, str]]:
        raw_subjects: List[Any] = []
        raw_subjects.extend(subject_codes or [])
        if subject_code:
            raw_subjects.append(subject_code)
        if subject:
            raw_subjects.append(subject)
        if subject_name:
            raw_subjects.append(subject_name)
        if not raw_subjects:
            raw_subjects.append(DEFAULT_SUBJECT_CODE)

        resolved_subjects: List[Dict[str, str]] = []
        seen = set()
        for raw in raw_subjects:
            resolved = self._resolve_subject(
                subject=str(raw or "").strip(),
                subject_code=str(raw or "").strip(),
                subject_name=str(raw or "").strip(),
                subject_type=subject_type,
            )
            subject_key = str(resolved["subject_code"]).upper()
            if subject_key in seen:
                continue
            seen.add(subject_key)
            resolved_subjects.append(resolved)
        return resolved_subjects

    def build_payload(
        self,
        *,
        indicator_ids: List[Any] | None = None,
        indicator_name: str = "",
        indicator_names: List[str] | None = None,
        range_days: int = 30,
        subject_codes: List[Any] | None = None,
        subject: str = "",
        subject_code: str = "",
        subject_name: str = "",
        subject_type: str = "",
    ) -> Dict[str, Any]:
        normalized_indicator_ids = self._normalize_indicator_ids(
            indicator_ids=indicator_ids,
            indicator_names=indicator_names,
            indicator_name=indicator_name,
        )
        resolved_subjects = self._resolve_subjects(
            subject_codes=subject_codes,
            subject_code=subject_code,
            subject=subject,
            subject_name=subject_name,
            subject_type=subject_type,
        )
        normalized_range_days = self._normalize_int(range_days, 30, minimum=1, maximum=365)
        start_date = dt.date.today() - dt.timedelta(days=max(5, normalized_range_days * 3))

        db = StockInfoDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                indicator_placeholders = ",".join(["%s"] * len(normalized_indicator_ids))
                subject_code_placeholders = ",".join(["%s"] * len(resolved_subjects))
                subject_codes = [item["subject_code"] for item in resolved_subjects]
                cursor.execute(
                    f"""
                    SELECT
                      trade_date, subject_type, subject_code, subject_name,
                      indicator_name, indicator_family, measure_name, window_size,
                      value_decimal, unit, snapshot_time, minute_index, is_finalized,
                      calc_mode, source_summary
                    FROM {TABLE_NAME}
                    WHERE subject_code IN ({subject_code_placeholders})
                      AND indicator_name IN ({indicator_placeholders})
                      AND trade_date >= %s
                    ORDER BY trade_date DESC, subject_code ASC, indicator_name ASC
                    LIMIT %s
                    """,
                    tuple(
                        [
                            *subject_codes,
                            *normalized_indicator_ids,
                            start_date,
                            normalized_range_days * max(1, len(normalized_indicator_ids)) * max(1, len(resolved_subjects)),
                        ]
                    ),
                )
                rows = cursor.fetchall()
        finally:
            db.close_db()

        items: List[Dict[str, Any]] = []
        for row in reversed(rows or []):
            snapshot_time = row.get("snapshot_time")
            items.append(
                {
                    "trade_date": row["trade_date"].strftime("%Y-%m-%d"),
                    "indicator_name": row["indicator_name"],
                    "indicator_value": float(row["value_decimal"]) if row.get("value_decimal") is not None else None,
                    "subject_type": row["subject_type"],
                    "subject_code": row["subject_code"],
                    "subject_name": row["subject_name"],
                    "measure_name": row.get("measure_name") or "",
                    "window_size": int(row.get("window_size") or 0),
                    "unit": row.get("unit") or "",
                    "snapshot_time": snapshot_time.strftime("%Y-%m-%d %H:%M:%S") if snapshot_time else "",
                    "minute_index": int(row["minute_index"]) if row.get("minute_index") is not None else None,
                    "is_finalized": bool(row.get("is_finalized")),
                    "calc_mode": row.get("calc_mode") or "",
                    "source_summary": row.get("source_summary") or "",
                }
            )
        series_map: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            indicator_key = str(item.get("indicator_name") or "").strip()
            subject_code_key = str(item.get("subject_code") or "").strip()
            if not indicator_key or not subject_code_key:
                continue
            composite_key = f"{subject_code_key}:{indicator_key}"
            series_map.setdefault(composite_key, []).append(
                {
                    "trade_date": item["trade_date"],
                    "value": item["indicator_value"],
                }
            )
        primary_subject = resolved_subjects[0]
        return {
            "data": items,
            "series_map": series_map,
            "meta": {
                "indicator_name": normalized_indicator_ids[0],
                "indicator_names": normalized_indicator_ids,
                "indicator_ids": normalized_indicator_ids,
                "subject_type": primary_subject["subject_type"],
                "subject_code": primary_subject["subject_code"],
                "subject_name": primary_subject["subject_name"],
                "subject_codes": [item["subject_code"] for item in resolved_subjects],
                "subject_names": [item["subject_name"] for item in resolved_subjects],
                "range_days": normalized_range_days,
                "returned_points": len(items),
            },
        }


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    tool = IndicatorSeriesTool()
    try:
        payload = tool.build_payload(
            indicator_ids=params.get("indicator_ids") or [],
            indicator_name=params.get("indicator_name", ""),
            indicator_names=params.get("indicator_names") or [],
            range_days=params.get("range_days", 30),
            subject_codes=params.get("subject_codes") or [],
            subject=str(params.get("subject") or "").strip(),
            subject_code=str(params.get("subject_code") or "").strip(),
            subject_name=str(params.get("subject_name") or "").strip(),
            subject_type=str(params.get("subject_type") or "").strip(),
        )
        return {
            "tool": TOOL_NAME,
            "ok": True,
            "data": payload["data"],
            "series_map": payload.get("series_map") or {},
            "meta": payload["meta"],
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": TOOL_NAME,
            "ok": False,
            "data": [],
            "meta": {},
            "error": str(exc),
        }
