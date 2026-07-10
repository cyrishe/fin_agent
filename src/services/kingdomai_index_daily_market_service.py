from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiIndexDailyMarketService:
    """Read-only index daily market provider backed by kingdomai tables."""

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
            identity = self._resolve_index_identity(db=db, subject=raw_subject)
            index_code = self._trim(identity.get("idx_code")) or raw_subject
            if not index_code:
                raise ValueError(f"cannot resolve index subject: {subject}")
            rows = self._query_market_rows(
                db=db,
                index_code=index_code,
                start=start_date,
                end=end_date,
                limit=row_limit,
            )
            rows = list(reversed(rows))
            normalized_rows = [self._normalize_market_row(row) for row in rows]
            valuation_rows = [
                row
                for row in normalized_rows
                if any(
                    row.get(field) != 0
                    for field in (
                        "pe_lyr",
                        "pe_ttm",
                        "pb_mrq",
                        "pcf_lyr",
                        "pcf_ttm",
                        "ps_lyr",
                        "ps_ttm",
                        "total_mv",
                        "float_mv",
                    )
                )
            ]
            return {
                "source": ["kcrp_index_price", "kcrp_index_pricevaluate"],
                "subject": raw_subject,
                "index_code": index_code,
                "index_short_name": self._trim(identity.get("index_short_name")),
                "date_range": self._date_range(rows),
                "row_count": len(normalized_rows),
                "rows": normalized_rows,
                "coverage": {
                    "requested_start": start_date,
                    "requested_end": end_date,
                    "exact_date": exact_date,
                    "returned_rows": len(normalized_rows),
                    "latest_trade_date": self._date_to_text(rows[-1].get("trade_date")) if rows else "",
                    "identity_available": bool(identity),
                    "valuation_rows": len(valuation_rows),
                    "valuation_available": bool(valuation_rows),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _resolve_index_identity(self, *, db: Any, subject: str) -> Mapping[str, Any]:
        code_candidates = self._index_code_candidates(subject)
        if code_candidates:
            placeholders = ", ".join(["%s"] * len(code_candidates))
            sql = f"""
                SELECT idx_code, index_short_name
                FROM kcrp_index_price
                WHERE idx_code IN ({placeholders})
                ORDER BY FIELD(idx_code, {placeholders}), trade_date DESC
                LIMIT 1
            """
            row = self._fetchone(db, sql, tuple(code_candidates + code_candidates))
            if row:
                return row
        sql = """
            SELECT idx_code, index_short_name
            FROM kcrp_index_price
            WHERE index_short_name = %s
               OR index_short_name LIKE %s
            ORDER BY
              CASE
                WHEN index_short_name = %s THEN 0
                ELSE 9
              END,
              trade_date DESC
            LIMIT 1
        """
        like_subject = f"%{subject}%"
        return self._fetchone(db, sql, (subject, like_subject, subject))

    def _index_code_candidates(self, subject: str) -> List[str]:
        text = self._trim(subject).upper()
        if not text:
            return []
        if "." in text:
            return [text]
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) != 6:
            return []
        return [f"{digits}.SH", f"{digits}.SZ", f"{digits}.BJ", digits]

    def _query_market_rows(
        self,
        *,
        db: Any,
        index_code: str,
        start: str,
        end: str,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        params: List[Any] = [index_code]
        date_clause = ""
        if start and end:
            if start == end:
                date_clause = "AND p.trade_date = %s"
                params.append(start)
            else:
                date_clause = "AND p.trade_date BETWEEN %s AND %s"
                params.extend([start, end])
        params.append(limit)
        sql = f"""
            SELECT p.trade_date, p.idx_code,
                   COALESCE(p.index_short_name, v.index_short_name) AS index_short_name,
                   p.preclose, p.open, p.high, p.low, p.close, p.rise_fall_rate,
                   p.volume, p.amount, p.turn_ratio,
                   v.pe_lyr, v.pe_ttm, v.pb_mrq, v.pcf_lyr, v.pcf_ttm,
                   v.ps_lyr, v.ps_ttm, v.total_mv, v.float_mv
            FROM kcrp_index_price p
            LEFT JOIN kcrp_index_pricevaluate v
              ON v.idx_code = p.idx_code
             AND v.trade_date = p.trade_date
            WHERE p.idx_code = %s
              {date_clause}
            ORDER BY p.trade_date DESC
            LIMIT %s
        """
        return self._fetchall(db, sql, tuple(params))

    def _fetchone(self, db: Any, sql: str, params: tuple[Any, ...]) -> Mapping[str, Any]:
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return row if isinstance(row, Mapping) else {}

    def _fetchall(self, db: Any, sql: str, params: tuple[Any, ...]) -> List[Mapping[str, Any]]:
        conn = getattr(db, "conn", db)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [row for row in rows if isinstance(row, Mapping)]

    def _date_range(self, rows: List[Mapping[str, Any]]) -> Dict[str, str]:
        if not rows:
            return {"start": "", "end": ""}
        return {
            "start": self._date_to_text(rows[0].get("trade_date")),
            "end": self._date_to_text(rows[-1].get("trade_date")),
        }

    def _normalize_market_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "index_code": self._trim(row.get("idx_code")),
            "index_short_name": self._trim(row.get("index_short_name")),
            "preclose": self._number(row.get("preclose")),
            "open": self._number(row.get("open")),
            "high": self._number(row.get("high")),
            "low": self._number(row.get("low")),
            "close": self._number(row.get("close")),
            "rise_fall_rate": self._number(row.get("rise_fall_rate")),
            "volume": self._number(row.get("volume")),
            "amount": self._number(row.get("amount")),
            "turn_ratio": self._number(row.get("turn_ratio")),
            "pe_lyr": self._number(row.get("pe_lyr")),
            "pe_ttm": self._number(row.get("pe_ttm")),
            "pb_mrq": self._number(row.get("pb_mrq")),
            "pcf_lyr": self._number(row.get("pcf_lyr")),
            "pcf_ttm": self._number(row.get("pcf_ttm")),
            "ps_lyr": self._number(row.get("ps_lyr")),
            "ps_ttm": self._number(row.get("ps_ttm")),
            "total_mv": self._number(row.get("total_mv")),
            "float_mv": self._number(row.get("float_mv")),
        }
