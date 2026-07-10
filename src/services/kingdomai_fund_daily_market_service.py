from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiFundDailyMarketService:
    """Read-only fund daily market provider backed by kingdomai tables."""

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
    def _bool(value: Any) -> bool:
        text = str(value or "").strip().lower()
        if text in {"1", "true", "y", "yes", "是"}:
            return True
        if text in {"0", "false", "n", "no", "否", ""}:
            return False
        return bool(value)

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
            identity = self._resolve_fund_identity(db=db, subject=raw_subject)
            fund_code = self._trim(identity.get("fund_code")) or raw_subject
            if not fund_code:
                raise ValueError(f"cannot resolve fund subject: {subject}")
            rows = self._query_price_rows(
                db=db,
                fund_code=fund_code,
                start=start_date,
                end=end_date,
                limit=row_limit,
            )
            rows = list(reversed(rows))
            return {
                "source": ["kcrp_fund_baseinfo", "kcrp_fund_price"],
                "subject": raw_subject,
                "code": fund_code,
                "name": self._trim(identity.get("fund_name")),
                "date_range": self._date_range(rows),
                "row_count": len(rows),
                "rows": [self._normalize_price_row(row) for row in rows],
                "coverage": {
                    "requested_start": start_date,
                    "requested_end": end_date,
                    "exact_date": exact_date,
                    "returned_rows": len(rows),
                    "latest_trade_date": self._date_to_text(rows[-1].get("trade_date")) if rows else "",
                    "profile_available": bool(identity),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def query_profile(self, *, subject: str) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")

        db = self.db_factory()
        try:
            identity = self._resolve_fund_identity(db=db, subject=raw_subject)
            fund_code = self._trim(identity.get("fund_code")) or raw_subject
            if not fund_code:
                raise ValueError(f"cannot resolve fund subject: {subject}")
            index_flags = self._query_index_flags(db=db, fund_code=fund_code)
            return {
                "source": ["kcrp_fund_baseinfo", "kcrp_dwd_fund_index"],
                "subject": raw_subject,
                "code": fund_code,
                "name": self._trim(identity.get("fund_name")),
                "profile": self._normalize_profile(identity),
                "latest_index_flags": index_flags,
                "coverage": {
                    "profile_available": bool(identity),
                    "index_flags_available": bool(index_flags),
                    "latest_index_trade_date": self._date_to_text(index_flags.get("trade_date")) if index_flags else "",
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _resolve_fund_identity(self, *, db: Any, subject: str) -> Mapping[str, Any]:
        sql = """
            SELECT fund_code, fund_name, fund_fullname, market,
                   fund_operation, fund_invest_type, fund_nature,
                   establishment_date, maturity_date, fund_invest_style,
                   invest_scope, invest_object, delist_date, list_date,
                   etf_track_index_code, is_index_fund, is_intraday_trading
            FROM kcrp_fund_baseinfo
            WHERE fund_code = %s
               OR LEFT(fund_code, 6) = %s
               OR fund_name = %s
               OR fund_fullname = %s
               OR fund_name LIKE %s
               OR fund_fullname LIKE %s
            ORDER BY
              CASE
                WHEN fund_code = %s THEN 0
                WHEN LEFT(fund_code, 6) = %s THEN 1
                WHEN fund_name = %s THEN 2
                WHEN fund_fullname = %s THEN 3
                ELSE 9
              END,
              list_date DESC
            LIMIT 1
        """
        like_subject = f"%{subject}%"
        params = (
            subject,
            subject,
            subject,
            subject,
            like_subject,
            like_subject,
            subject,
            subject,
            subject,
            subject,
        )
        return self._fetchone(db, sql, params)

    def _query_price_rows(
        self,
        *,
        db: Any,
        fund_code: str,
        start: str,
        end: str,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        params: List[Any] = [fund_code]
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
            SELECT trade_date, fund_code, nav_unit, preclose, open, high, low, close,
                   avg_price, differ, differ_range, turn_ratio, volume, amount,
                   amplitude, discount, unit_total
            FROM kcrp_fund_price
            WHERE fund_code = %s
              {date_clause}
            ORDER BY trade_date DESC
            LIMIT %s
        """
        return self._fetchall(db, sql, tuple(params))

    def _query_index_flags(self, *, db: Any, fund_code: str) -> Dict[str, Any]:
        sql = """
            SELECT trade_date, fund_code, fund_name, f_market, f_sort, f_operation,
                   f_invest_type, f_nature, f_invest_style, f_listed_nature_day,
                   f_listed_trade_day, f_is_maturity_date, f_is_delist_date,
                   f_is_exchange_guarantee, f_is_exchange_rz_underlying,
                   f_is_exchange_rq_underlying, f_is_index_fund
            FROM kcrp_dwd_fund_index
            WHERE fund_code = %s
            ORDER BY trade_date DESC
            LIMIT 1
        """
        row = self._fetchone(db, sql, (fund_code,))
        if not row:
            return {}
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "name": self._trim(row.get("fund_name")),
            "market": self._trim(row.get("f_market")),
            "sort": self._trim(row.get("f_sort")),
            "operation": self._trim(row.get("f_operation")),
            "invest_type": self._trim(row.get("f_invest_type")),
            "nature": self._trim(row.get("f_nature")),
            "invest_style": self._trim(row.get("f_invest_style")),
            "listed_nature_day": self._number(row.get("f_listed_nature_day")),
            "listed_trade_day": self._number(row.get("f_listed_trade_day")),
            "is_maturity_date": self._bool(row.get("f_is_maturity_date")),
            "is_delist_date": self._bool(row.get("f_is_delist_date")),
            "is_exchange_guarantee": self._bool(row.get("f_is_exchange_guarantee")),
            "is_exchange_rz_underlying": self._bool(row.get("f_is_exchange_rz_underlying")),
            "is_exchange_rq_underlying": self._bool(row.get("f_is_exchange_rq_underlying")),
            "is_index_fund": self._bool(row.get("f_is_index_fund")),
        }

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

    def _normalize_profile(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        if not row:
            return {}
        return {
            "code": self._trim(row.get("fund_code")),
            "name": self._trim(row.get("fund_name")),
            "fund_fullname": self._trim(row.get("fund_fullname")),
            "market": self._trim(row.get("market")),
            "fund_operation": self._trim(row.get("fund_operation")),
            "fund_invest_type": self._trim(row.get("fund_invest_type")),
            "fund_nature": self._trim(row.get("fund_nature")),
            "fund_invest_style": self._trim(row.get("fund_invest_style")),
            "establishment_date": self._date_to_text(row.get("establishment_date")),
            "maturity_date": self._date_to_text(row.get("maturity_date")),
            "list_date": self._date_to_text(row.get("list_date")),
            "delist_date": self._date_to_text(row.get("delist_date")),
            "invest_scope": self._trim(row.get("invest_scope")),
            "invest_object": self._trim(row.get("invest_object")),
            "etf_track_index_code": self._trim(row.get("etf_track_index_code")),
            "is_index_fund": self._bool(row.get("is_index_fund")),
            "is_intraday_trading": self._bool(row.get("is_intraday_trading")),
        }

    def _normalize_price_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "code": self._trim(row.get("fund_code")),
            "nav_unit": self._number(row.get("nav_unit")),
            "preclose": self._number(row.get("preclose")),
            "open": self._number(row.get("open")),
            "high": self._number(row.get("high")),
            "low": self._number(row.get("low")),
            "close": self._number(row.get("close")),
            "avg_price": self._number(row.get("avg_price")),
            "differ": self._number(row.get("differ")),
            "differ_range": self._number(row.get("differ_range")),
            "turn_ratio": self._number(row.get("turn_ratio")),
            "volume": self._number(row.get("volume")),
            "amount": self._number(row.get("amount")),
            "amplitude": self._number(row.get("amplitude")),
            "discount": self._number(row.get("discount")),
            "unit_total": self._number(row.get("unit_total")),
        }
