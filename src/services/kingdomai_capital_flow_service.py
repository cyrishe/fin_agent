from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiCapitalFlowService:
    """Read-only capital-flow provider backed by kingdomai tables."""

    SUBJECT_TYPES = {"stock", "industry", "concept", "sector", "index", "market", "other"}
    GRANULARITIES = {"daily", "minute"}

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
    def _datetime_to_text(value: Any) -> str:
        if value in (None, ""):
            return ""
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M:%S")
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
        subject_type: str = "stock",
        subject: str = "",
        start: str = "",
        end: str = "",
        days: int = 20,
        granularity: str = "daily",
        limit: int = 50,
    ) -> Dict[str, Any]:
        normalized_subject_type = self._trim(subject_type).lower() or "stock"
        if normalized_subject_type not in self.SUBJECT_TYPES:
            raise ValueError(f"unsupported subject_type: {subject_type}")
        normalized_granularity = self._trim(granularity).lower() or "daily"
        if normalized_granularity not in self.GRANULARITIES:
            raise ValueError(f"unsupported granularity: {granularity}")

        window_days = self._bounded_int(days, default=20, min_value=1, max_value=250)
        row_limit = self._bounded_int(limit, default=50, min_value=1, max_value=500)
        start_date, end_date, exact_date = self._normalize_date_range(start=start, end=end)
        raw_subject = self._trim(subject)
        if normalized_subject_type != "market" and not raw_subject:
            raise ValueError("subject is required unless subject_type is market")

        db = self.db_factory()
        try:
            if normalized_subject_type == "stock":
                return self._query_stock_daily(
                    db=db,
                    subject=raw_subject,
                    start=start_date,
                    end=end_date,
                    exact_date=exact_date,
                    days=window_days,
                    limit=row_limit,
                )
            return self._query_unified_snapshot(
                db=db,
                subject_type=normalized_subject_type,
                subject=raw_subject,
                trade_date=end_date,
                days=window_days,
                granularity=normalized_granularity,
                limit=row_limit,
            )
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _query_stock_daily(
        self,
        *,
        db: Any,
        subject: str,
        start: str,
        end: str,
        exact_date: bool,
        days: int,
        limit: int,
    ) -> Dict[str, Any]:
        identity = db.resolve_stock_identity(subject) if hasattr(db, "resolve_stock_identity") else None
        stk_code = self._trim((identity or {}).get("stk_code")) or subject
        code6 = stk_code[:6]
        stk_name = self._trim((identity or {}).get("stk_name"))
        if not code6:
            raise ValueError(f"cannot resolve stock subject: {subject}")

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
                   huge_buy_value, huge_sell_value, huge_net_buy_value_ratio,
                   large_buy_value, large_sell_value, large_net_buy_value, large_net_buy_value_ratio,
                   medium_buy_value, medium_sell_value,
                   small_buy_value, small_sell_value,
                   main_buy_value, main_sell_value, main_net_buy_value, main_net_buy_value_ratio
            FROM kcrp_stock_moneyflow
            WHERE (stk_code = %s OR LEFT(stk_code, 6) = %s)
              {date_clause}
            ORDER BY trade_date DESC
            LIMIT %s
        """
        rows = self._fetchall(db, sql, params)
        rows = list(reversed(rows if start or end else rows[:days]))
        return {
            "source": "kcrp_stock_moneyflow",
            "subject_type": "stock",
            "subject": subject,
            "subject_code": stk_code,
            "subject_name": stk_name,
            "granularity": "daily",
            "date_range": self._date_range(rows),
            "row_count": len(rows),
            "rows": [self._normalize_stock_moneyflow_row(row) for row in rows],
            "coverage": {
                "requested_start": start,
                "requested_end": end,
                "exact_date": exact_date,
                "requested_days": days,
                "returned_rows": len(rows),
                "latest_trade_date": self._date_to_text(rows[-1].get("trade_date")) if rows else "",
            },
        }

    def _query_unified_snapshot(
        self,
        *,
        db: Any,
        subject_type: str,
        subject: str,
        trade_date: str,
        days: int,
        granularity: str,
        limit: int,
    ) -> Dict[str, Any]:
        params: List[Any] = [subject_type, granularity]
        subject_clause = ""
        if subject_type != "market":
            subject_clause = "AND (subject_code = %s OR subject_name = %s)"
            params.extend([subject, subject])
        elif subject:
            subject_clause = "AND (subject_code = %s OR subject_name = %s OR market = %s)"
            params.extend([subject, subject, subject])

        date_clause = ""
        if trade_date:
            date_clause = "AND trade_date <= %s"
            params.append(trade_date)
        params.append(limit)
        sql = f"""
            SELECT trade_date, snapshot_time, minute_index, capture_phase, series_granularity, source,
                   subject_type, subject_code, subject_name, market, bucket_schema, currency, amount_unit,
                   total_inflow_wan, total_outflow_wan, total_net_inflow_wan, total_net_ratio_pct,
                   main_inflow_wan, main_outflow_wan, main_net_inflow_wan, main_net_ratio_pct,
                   retail_inflow_wan, retail_outflow_wan, retail_net_inflow_wan, retail_net_ratio_pct,
                   super_large_inflow_wan, super_large_outflow_wan, super_large_net_inflow_wan,
                   large_inflow_wan, large_outflow_wan, large_net_inflow_wan,
                   medium_inflow_wan, medium_outflow_wan, medium_net_inflow_wan,
                   small_inflow_wan, small_outflow_wan, small_net_inflow_wan
            FROM aiia_capital_flow_snapshot
            WHERE subject_type = %s
              AND series_granularity = %s
              {subject_clause}
              {date_clause}
            ORDER BY trade_date DESC, minute_index DESC
            LIMIT %s
        """
        rows = self._fetchall(db, sql, params)
        rows = list(reversed(rows[:days]))
        subject_code = self._trim(rows[-1].get("subject_code")) if rows else subject
        subject_name = self._trim(rows[-1].get("subject_name")) if rows else subject
        return {
            "source": "aiia_capital_flow_snapshot",
            "subject_type": subject_type,
            "subject": subject,
            "subject_code": subject_code,
            "subject_name": subject_name,
            "granularity": granularity,
            "date_range": self._date_range(rows),
            "row_count": len(rows),
            "rows": [self._normalize_unified_row(row) for row in rows],
            "coverage": {
                "requested_days": days,
                "returned_rows": len(rows),
                "latest_trade_date": self._date_to_text(rows[-1].get("trade_date")) if rows else "",
            },
        }

    def _fetchall(self, db: Any, sql: str, params: List[Any]) -> List[Mapping[str, Any]]:
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

    def _normalize_stock_moneyflow_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        trade_date = row.get("trade_date")
        return {
            "trade_date": self._date_to_text(trade_date),
            "snapshot_time": self._datetime_to_text(trade_date),
            "subject_code": self._trim(row.get("stk_code")),
            "subject_name": "",
            "total_net_inflow_wan": self._number(row.get("main_net_buy_value")),
            "total_net_ratio_pct": self._number(row.get("main_net_buy_value_ratio")),
            "main_net_inflow_wan": self._number(row.get("main_net_buy_value")),
            "main_net_ratio_pct": self._number(row.get("main_net_buy_value_ratio")),
            "retail_net_inflow_wan": 0.0,
            "retail_net_ratio_pct": 0.0,
            "super_large_net_inflow_wan": self._number(row.get("huge_buy_value")) - self._number(row.get("huge_sell_value")),
            "large_net_inflow_wan": self._number(row.get("large_net_buy_value")),
            "medium_net_inflow_wan": self._number(row.get("medium_buy_value")) - self._number(row.get("medium_sell_value")),
            "small_net_inflow_wan": self._number(row.get("small_buy_value")) - self._number(row.get("small_sell_value")),
        }

    def _normalize_unified_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "snapshot_time": self._datetime_to_text(row.get("snapshot_time")),
            "subject_code": self._trim(row.get("subject_code")),
            "subject_name": self._trim(row.get("subject_name")),
            "total_net_inflow_wan": self._number(row.get("total_net_inflow_wan")),
            "total_net_ratio_pct": self._number(row.get("total_net_ratio_pct")),
            "main_net_inflow_wan": self._number(row.get("main_net_inflow_wan")),
            "main_net_ratio_pct": self._number(row.get("main_net_ratio_pct")),
            "retail_net_inflow_wan": self._number(row.get("retail_net_inflow_wan")),
            "retail_net_ratio_pct": self._number(row.get("retail_net_ratio_pct")),
            "super_large_net_inflow_wan": self._number(row.get("super_large_net_inflow_wan")),
            "large_net_inflow_wan": self._number(row.get("large_net_inflow_wan")),
            "medium_net_inflow_wan": self._number(row.get("medium_net_inflow_wan")),
            "small_net_inflow_wan": self._number(row.get("small_net_inflow_wan")),
        }
