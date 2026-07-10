from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


DbFactory = Callable[[], Any]


class KingdomaiStockKlineService:
    """Read-only stock kline provider backed by kingdomai market tables."""

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
    def _time_to_text(value: Any) -> str:
        if value in (None, ""):
            return ""
        if hasattr(value, "strftime"):
            return value.strftime("%H:%M:%S")
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

    def query_daily(
        self,
        *,
        subject: str,
        start: str = "",
        end: str = "",
        limit: int = 120,
    ) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        start_date, end_date, exact_date = self._normalize_date_range(start=start, end=end)
        row_limit = self._bounded_int(limit, default=120, min_value=1, max_value=1000)

        db = self.db_factory()
        try:
            identity = self._resolve_stock_identity(db=db, subject=raw_subject)
            stock_code = self._trim(identity.get("stock_code")) or self._normalize_stock_code(raw_subject)
            if not stock_code:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            rows = self._query_daily_rows(
                db=db,
                stock_code=stock_code,
                start=start_date,
                end=end_date,
                limit=row_limit,
            )
            rows = list(reversed(rows))
            normalized_rows = [self._normalize_daily_row(row) for row in rows]
            return {
                "source": ["kcrp_stock_price", "kcrp_stock_baseinfo"],
                "subject": raw_subject,
                "stock_code": stock_code,
                "code6": self._code6(stock_code),
                "stock_name": self._trim(identity.get("stock_name")),
                "date_range": self._date_range(normalized_rows, date_field="trade_date"),
                "row_count": len(normalized_rows),
                "rows": normalized_rows,
                "coverage": {
                    "requested_start": start_date,
                    "requested_end": end_date,
                    "exact_date": exact_date,
                    "returned_rows": len(normalized_rows),
                    "latest_trade_date": normalized_rows[-1]["trade_date"] if normalized_rows else "",
                    "identity_available": bool(identity),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def query_intraday(
        self,
        *,
        subject: str,
        trade_date: str = "",
        minute_count: int = 240,
    ) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")
        requested_minutes = self._bounded_int(minute_count, default=240, min_value=1, max_value=1000)

        db = self.db_factory()
        try:
            identity = self._resolve_stock_identity(db=db, subject=raw_subject)
            stock_code = self._trim(identity.get("stock_code")) or self._normalize_stock_code(raw_subject)
            code6 = self._code6(stock_code or raw_subject)
            if not code6:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            rows = self._query_intraday_rows(
                db=db,
                code6=code6,
                trade_date=self._trim(trade_date),
                limit=requested_minutes,
            )
            rows = list(reversed(rows))
            normalized_rows = [self._normalize_intraday_row(row) for row in rows]
            return {
                "source": ["aiia_stock_realtime_minute_snapshot", "kcrp_stock_baseinfo"],
                "subject": raw_subject,
                "stock_code": stock_code,
                "code6": code6,
                "stock_name": self._trim(identity.get("stock_name")) or self._trim(rows[-1].get("stk_name") if rows else ""),
                "trade_date": normalized_rows[-1]["trade_date"] if normalized_rows else "",
                "row_count": len(normalized_rows),
                "rows": normalized_rows,
                "coverage": {
                    "requested_minutes": requested_minutes,
                    "returned_rows": len(normalized_rows),
                    "latest_trade_date": normalized_rows[-1]["trade_date"] if normalized_rows else "",
                    "latest_minute_index": normalized_rows[-1]["minute_index"] if normalized_rows else 0,
                    "identity_available": bool(identity),
                },
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def query_realtime(self, *, subject: str) -> Dict[str, Any]:
        raw_subject = self._trim(subject)
        if not raw_subject:
            raise ValueError("subject is required")

        db = self.db_factory()
        try:
            identity = self._resolve_stock_identity(db=db, subject=raw_subject)
            stock_code = self._trim(identity.get("stock_code")) or self._normalize_stock_code(raw_subject)
            code6 = self._code6(stock_code or raw_subject)
            if not code6:
                raise ValueError(f"cannot resolve stock subject: {subject}")
            rows = self._query_intraday_rows(db=db, code6=code6, trade_date="", limit=1)
            row = rows[0] if rows else {}
            normalized = self._normalize_intraday_row(row) if row else {}
            stock_name = self._trim(identity.get("stock_name")) or self._trim(row.get("stk_name"))
            source = self._trim(row.get("source")) or "aiia_stock_realtime_minute_snapshot"
            return {
                "source": "aiia_stock_realtime_minute_snapshot",
                "source_detail": source,
                "code": code6,
                "code6": code6,
                "stk_code": stock_code,
                "name": stock_name,
                "stock_name": stock_name,
                "trade_date": normalized.get("trade_date", ""),
                "minute_index": normalized.get("minute_index", 0),
                "minute_time": normalized.get("minute_time", ""),
                "snapshot_time": normalized.get("snapshot_time", ""),
                "snapshot_slot": normalized.get("snapshot_slot", ""),
                "quote": {
                    "open": normalized.get("open_price", 0.0),
                    "current": normalized.get("latest_price", 0.0),
                    "high": normalized.get("high_price", 0.0),
                    "low": normalized.get("low_price", 0.0),
                    "preclose": normalized.get("preclose_price", 0.0),
                    "change": normalized.get("chg_value", 0.0),
                    "change_pct": normalized.get("chg_ratio", 0.0),
                    "volume": normalized.get("volume", 0.0),
                    "amount": normalized.get("amount", 0.0),
                },
                "coverage": {
                    "returned_rows": 1 if row else 0,
                    "latest_trade_date": normalized.get("trade_date", ""),
                    "latest_minute_index": normalized.get("minute_index", 0),
                    "identity_available": bool(identity),
                    "is_fallback": normalized.get("is_fallback", False),
                },
                "render_blocks": [],
            }
        finally:
            close_db = getattr(db, "close_db", None)
            if callable(close_db):
                close_db()

    def _normalize_stock_code(self, subject: str) -> str:
        value = self._trim(subject).upper()
        if not value:
            return ""
        if value.endswith((".SH", ".SZ", ".BJ")):
            return value
        if value.isdigit() and len(value) == 6:
            if value.startswith(("60", "68", "69", "90")):
                return f"{value}.SH"
            if value.startswith(("43", "82", "83", "87", "88", "92")):
                return f"{value}.BJ"
            return f"{value}.SZ"
        return ""

    def _code6(self, stock_code: str) -> str:
        value = self._trim(stock_code).upper()
        if "." in value:
            value = value.split(".", 1)[0]
        digits = "".join(ch for ch in value if ch.isdigit())
        return digits if len(digits) == 6 else ""

    def _resolve_stock_identity(self, *, db: Any, subject: str) -> Mapping[str, Any]:
        normalized_code = self._normalize_stock_code(subject)
        code6 = self._code6(subject)
        sql = """
            SELECT stk_code, stk_name, market, board, list_date, delist_date
            FROM kcrp_stock_baseinfo
            WHERE stk_code = %s
               OR stk_code = %s
               OR stk_code LIKE %s
               OR stk_name = %s
               OR stk_name LIKE %s
            ORDER BY
              CASE
                WHEN stk_code = %s THEN 0
                WHEN stk_code = %s THEN 1
                WHEN stk_name = %s THEN 2
                ELSE 9
              END,
              list_date DESC
            LIMIT 1
        """
        like_code6 = f"{code6}.%" if code6 else ""
        like_subject = f"%{subject}%"
        row = self._fetchone(
            db,
            sql,
            (
                subject,
                normalized_code,
                like_code6,
                subject,
                like_subject,
                subject,
                normalized_code,
                subject,
            ),
        )
        if not row:
            return {}
        return {
            "stock_code": row.get("stk_code"),
            "stock_name": row.get("stk_name"),
            "market": row.get("market"),
            "board": row.get("board"),
            "list_date": row.get("list_date"),
            "delist_date": row.get("delist_date"),
        }

    def _query_daily_rows(
        self,
        *,
        db: Any,
        stock_code: str,
        start: str,
        end: str,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        params: List[Any] = [stock_code]
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
            SELECT trade_date, stk_code, preclose, open, high, low, close, avg_price,
                   differ, rise_fall_rate, turn_ratio, volume, amount, amplitude,
                   adjpreclose, adjopen, adjhigh, adjlow, adjclose, is_limit_price
            FROM kcrp_stock_price
            WHERE stk_code = %s
              {date_clause}
            ORDER BY trade_date DESC
            LIMIT %s
        """
        return self._fetchall(db, sql, tuple(params))

    def _query_intraday_rows(
        self,
        *,
        db: Any,
        code6: str,
        trade_date: str,
        limit: int,
    ) -> List[Mapping[str, Any]]:
        params: List[Any] = [code6]
        date_clause = """
              AND trade_date = (
                SELECT MAX(trade_date)
                FROM aiia_stock_realtime_minute_snapshot
                WHERE stk_code = %s
              )
        """
        params.append(code6)
        if trade_date:
            date_clause = "AND trade_date = %s"
            params = [code6, trade_date]
        params.append(limit)
        sql = f"""
            SELECT trade_date, minute_index, snapshot_time, snapshot_slot, stk_code, stk_name,
                   latest_price, open_price, high_price, low_price, preclose_price,
                   chg_value, chg_ratio, amount, volume, source, is_fallback
            FROM aiia_stock_realtime_minute_snapshot
            WHERE stk_code = %s
              {date_clause}
            ORDER BY trade_date DESC, minute_index DESC
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

    def _date_range(self, rows: List[Mapping[str, Any]], *, date_field: str) -> Dict[str, str]:
        dates = [self._trim(row.get(date_field)) for row in rows if self._trim(row.get(date_field))]
        if not dates:
            return {"start": "", "end": ""}
        return {"start": min(dates), "end": max(dates)}

    def _minute_time(self, minute_index: Any) -> str:
        try:
            value = int(minute_index)
        except Exception:
            return ""
        hour, minute = divmod(value, 60)
        return f"{hour:02d}:{minute:02d}"

    def _normalize_daily_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "stock_code": self._trim(row.get("stk_code")),
            "preclose": self._number(row.get("preclose")),
            "open": self._number(row.get("open")),
            "high": self._number(row.get("high")),
            "low": self._number(row.get("low")),
            "close": self._number(row.get("close")),
            "avg_price": self._number(row.get("avg_price")),
            "differ": self._number(row.get("differ")),
            "rise_fall_rate": self._number(row.get("rise_fall_rate")),
            "turn_ratio": self._number(row.get("turn_ratio")),
            "volume": self._number(row.get("volume")),
            "amount": self._number(row.get("amount")),
            "amplitude": self._number(row.get("amplitude")),
            "adjpreclose": self._number(row.get("adjpreclose")),
            "adjopen": self._number(row.get("adjopen")),
            "adjhigh": self._number(row.get("adjhigh")),
            "adjlow": self._number(row.get("adjlow")),
            "adjclose": self._number(row.get("adjclose")),
            "is_limit_price": self._bool(row.get("is_limit_price")),
        }

    def _normalize_intraday_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        minute_index = self._bounded_int(row.get("minute_index"), default=0, min_value=0, max_value=24 * 60)
        return {
            "trade_date": self._date_to_text(row.get("trade_date")),
            "minute_index": minute_index,
            "minute_time": self._minute_time(minute_index),
            "snapshot_time": self._time_to_text(row.get("snapshot_time")),
            "snapshot_slot": self._trim(row.get("snapshot_slot")),
            "code6": self._trim(row.get("stk_code")),
            "stock_name": self._trim(row.get("stk_name")),
            "latest_price": self._number(row.get("latest_price")),
            "open_price": self._number(row.get("open_price")),
            "high_price": self._number(row.get("high_price")),
            "low_price": self._number(row.get("low_price")),
            "preclose_price": self._number(row.get("preclose_price")),
            "chg_value": self._number(row.get("chg_value")),
            "chg_ratio": self._number(row.get("chg_ratio")),
            "amount": self._number(row.get("amount")),
            "volume": self._number(row.get("volume")),
            "source": self._trim(row.get("source")),
            "is_fallback": self._bool(row.get("is_fallback")),
        }
