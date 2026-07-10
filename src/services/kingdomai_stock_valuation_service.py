from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiStockValuationService:
    """Read-only stock valuation provider backed by kcrp_stock_pricevaluate."""

    def __init__(self, *, db_factory: Optional[DbFactory] = None) -> None:
        self.db_factory = db_factory or StockInfoDbUtils

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _date_to_text(value: Any) -> str:
        if value in (None, ""):
            return ""
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d")
        return str(value)

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    @staticmethod
    def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return max(min_value, min(max_value, parsed))

    @classmethod
    def _normalize_date_range(cls, *, start: str = "", end: str = "") -> tuple[str, str, bool]:
        start_text = cls._trim(start)
        end_text = cls._trim(end)
        if start_text and end_text:
            if start_text > end_text:
                start_text, end_text = end_text, start_text
            return start_text, end_text, start_text == end_text
        exact_date = start_text or end_text
        if exact_date:
            return exact_date, exact_date, True
        return "", "", False

    def query(
        self,
        *,
        subject: str,
        start: str = "",
        end: str = "",
        limit: int = 50,
    ) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        start_date, end_date, exact_date = self._normalize_date_range(start=start, end=end)
        row_limit = self._bounded_int(limit, default=50, min_value=1, max_value=500)

        db = self.db_factory()
        try:
            identity = db.resolve_stock_identity(raw_subject) if hasattr(db, "resolve_stock_identity") else None
            stk_code = self._trim((identity or {}).get("stk_code")) or raw_subject
            code6 = stk_code[:6]
            stk_name = self._trim((identity or {}).get("stk_name"))
            if not code6:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            rows = self._query_rows(
                db=db,
                stk_code=stk_code,
                code6=code6,
                start=start_date,
                end=end_date,
                limit=row_limit,
            )
            rows = list(reversed(rows))
            return {
                "source": "kcrp_stock_pricevaluate",
                "subject": raw_subject,
                "subject_code": stk_code,
                "subject_name": stk_name,
                "date_range": self._date_range(rows),
                "row_count": len(rows),
                "rows": [self._normalize_row(row) for row in rows],
                "coverage": {
                    "requested_start": start_date,
                    "requested_end": end_date,
                    "exact_date": exact_date,
                    "returned_rows": len(rows),
                    "latest_trade_date": self._date_to_text(rows[-1].get("trade_date")) if rows else "",
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _query_rows(
        self,
        *,
        db: Any,
        stk_code: str,
        code6: str,
        start: str,
        end: str,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        params: List[Any] = [stk_code, code6]
        date_clause = ""
        if start and end:
            if start == end:
                date_clause = "AND trade_date = %s"
                params.append(start)
            else:
                date_clause = "AND trade_date BETWEEN %s AND %s"
                params.extend([start, end])
        params.append(limit)
        sql = f"""
            SELECT trade_date, stk_code,
                   total_mv, free_float_mv, float_mv,
                   pe_lyr, pe_ttm, pe_ttm_deduct, pe_dynamic,
                   pb_mrq, pb_lf,
                   ps_lyr, ps_ttm
            FROM kcrp_stock_pricevaluate
            WHERE (stk_code = %s OR LEFT(stk_code, 6) = %s)
              {date_clause}
            ORDER BY trade_date DESC
            LIMIT %s
        """
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [row for row in rows if isinstance(row, Mapping)]

    def _date_range(self, rows: List[Mapping[str, Any]]) -> Dict[str, str]:
        if not rows:
            return {"start": "", "end": ""}
        return {
            "start": self._date_to_text(rows[0].get("trade_date")),
            "end": self._date_to_text(rows[-1].get("trade_date")),
        }

    def _normalize_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "subject_code": self._trim(row.get("stk_code")),
            "total_mv": self._number(row.get("total_mv")),
            "free_float_mv": self._number(row.get("free_float_mv")),
            "float_mv": self._number(row.get("float_mv")),
            "pe_lyr": self._number(row.get("pe_lyr")),
            "pe_ttm": self._number(row.get("pe_ttm")),
            "pe_ttm_deduct": self._number(row.get("pe_ttm_deduct")),
            "pe_dynamic": self._number(row.get("pe_dynamic")),
            "pb_mrq": self._number(row.get("pb_mrq")),
            "pb_lf": self._number(row.get("pb_lf")),
            "ps_lyr": self._number(row.get("ps_lyr")),
            "ps_ttm": self._number(row.get("ps_ttm")),
        }
