from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional, Sequence

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


SNAPSHOT_TABLE = "aiia_stock_realtime_minute_snapshot"


class MarketSnapshotBase:
    _ST_RE = re.compile(r"^(?:\*?ST|S\*ST)", re.IGNORECASE)

    def __init__(self, db: Optional[StockInfoDbUtils] = None) -> None:
        self.db = db or StockInfoDbUtils()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_days(value: Any, default: int = 5) -> int:
        try:
            normalized = int(value or default)
        except Exception:
            normalized = default
        return max(1, min(normalized, 30))

    def _normalize_market(self, value: Any) -> str:
        raw = (self._trim(value) or "all").lower()
        if raw in {"all", "全"}:
            return "all"
        if raw in {"sh", "上", "沪", "沪市", "上海"}:
            return "sh"
        if raw in {"sz", "深", "深市", "深证", "深圳"}:
            return "sz"
        if raw in {"bj", "北", "北市", "北交所", "北京"}:
            return "bj"
        return "all"

    def _is_stock_code(self, code: str) -> bool:
        code6 = self._trim(code).split(".", 1)[0]
        return code6.startswith(("60", "68", "69", "90", "00", "001", "002", "003", "30", "43", "82", "83", "87", "88", "89", "92"))

    def _market_matches(self, code: str, market: str) -> bool:
        code6 = self._trim(code).split(".", 1)[0]
        if not self._is_stock_code(code6):
            return False
        if market == "all":
            return True
        if market == "sh":
            return code6.startswith(("60", "68", "69", "90"))
        if market == "sz":
            return code6.startswith(("00", "001", "002", "003", "30"))
        if market == "bj":
            return code6.startswith(("43", "82", "83", "87", "88", "89", "92"))
        return False

    def _is_st(self, name: str) -> bool:
        return bool(self._ST_RE.match(self._trim(name)))

    def _limit_pct(self, code: str, name: str) -> float:
        code6 = self._trim(code).split(".", 1)[0]
        if self._is_st(name):
            return 5.0
        if code6.startswith(("300", "301", "688", "689")):
            return 20.0
        if code6.startswith(("43", "82", "83", "87", "88", "89", "92")):
            return 30.0
        return 10.0

    def _resolve_latest_slot(self) -> Dict[str, Any]:
        with self.db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                f"""
                SELECT trade_date, minute_index, snapshot_time
                FROM {SNAPSHOT_TABLE}
                ORDER BY trade_date DESC, minute_index DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
        if not row:
            raise ValueError("没有找到可用的分钟快照数据")
        return dict(row)

    def _load_snapshot_rows(self, *, trade_date: dt.date, minute_index: int) -> List[Dict[str, Any]]:
        with self.db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                f"""
                SELECT
                  trade_date,
                  minute_index,
                  snapshot_time,
                  stk_code,
                  stk_name,
                  latest_price,
                  preclose_price,
                  amount,
                  volume
                FROM {SNAPSHOT_TABLE}
                WHERE trade_date = %s
                  AND minute_index = %s
                """,
                (trade_date, minute_index),
            )
            rows = cursor.fetchall()
        return list(rows or [])

    def _load_recent_trade_dates(self, *, days: int) -> List[dt.date]:
        with self.db.conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT trade_date
                FROM {SNAPSHOT_TABLE}
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (days,),
            )
            rows = cursor.fetchall()
        return [row[0] for row in rows or []]

    def _load_daily_total_amount(self, *, trade_date: dt.date, market: str) -> float:
        with self.db.conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT MAX(minute_index)
                FROM {SNAPSHOT_TABLE}
                WHERE trade_date = %s
                """,
                (trade_date,),
            )
            row = cursor.fetchone()
            max_minute = int(row[0] or 0) if row else 0
        if max_minute <= 0:
            return 0.0
        rows = self._load_snapshot_rows(trade_date=trade_date, minute_index=max_minute)
        return float(
            sum(
                self._safe_float(item.get("amount")) or 0.0
                for item in rows
                if self._market_matches(self._trim(item.get("stk_code")), market)
            )
        )

    def _load_same_minute_total_amount(self, *, trade_date: dt.date, minute_index: int, market: str) -> float:
        with self.db.conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT MAX(minute_index)
                FROM {SNAPSHOT_TABLE}
                WHERE trade_date = %s
                  AND minute_index <= %s
                """,
                (trade_date, minute_index),
            )
            row = cursor.fetchone()
            resolved_minute = int(row[0] or 0) if row else 0
        if resolved_minute <= 0:
            return 0.0
        rows = self._load_snapshot_rows(trade_date=trade_date, minute_index=resolved_minute)
        return float(
            sum(
                self._safe_float(item.get("amount")) or 0.0
                for item in rows
                if self._market_matches(self._trim(item.get("stk_code")), market)
            )
        )


class MarketRealtimeBreadthTool(MarketSnapshotBase):
    def run(self, params: Any) -> Dict[str, Any]:
        args = params if isinstance(params, dict) else {}
        market = self._normalize_market(args.get("market"))
        slot = self._resolve_latest_slot()
        rows = self._load_snapshot_rows(
            trade_date=slot["trade_date"],
            minute_index=int(slot["minute_index"]),
        )
        filtered = [row for row in rows if self._market_matches(self._trim(row.get("stk_code")), market)]

        rising_count = 0
        falling_count = 0
        flat_count = 0
        limit_up_count = 0
        limit_down_count = 0

        for row in filtered:
            latest_price = self._safe_float(row.get("latest_price"))
            preclose_price = self._safe_float(row.get("preclose_price"))
            if latest_price is None or preclose_price is None:
                continue
            if latest_price > preclose_price:
                rising_count += 1
            elif latest_price < preclose_price:
                falling_count += 1
            else:
                flat_count += 1

            limit_pct = self._limit_pct(self._trim(row.get("stk_code")), self._trim(row.get("stk_name")))
            if preclose_price > 0:
                chg_ratio = ((latest_price - preclose_price) / preclose_price) * 100.0
                if chg_ratio >= limit_pct - 0.2:
                    limit_up_count += 1
                elif chg_ratio <= -limit_pct + 0.2:
                    limit_down_count += 1

        return {
            "tool": "market_realtime_breadth",
            "ok": True,
            "data": {
                "market": market,
                "trade_date": str(slot.get("trade_date") or ""),
                "snapshot_time": str(slot.get("snapshot_time") or ""),
                "rising_count": rising_count,
                "falling_count": falling_count,
                "flat_count": flat_count,
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
            },
            "error": "",
        }


class MarketHistoryAmountTool(MarketSnapshotBase):
    def run(self, params: Any) -> Dict[str, Any]:
        args = params if isinstance(params, dict) else {}
        market = self._normalize_market(args.get("market"))
        days = self._normalize_days(args.get("days"), default=5)
        trade_dates = self._load_recent_trade_dates(days=days)
        history = [
            {
                "date": str(trade_date),
                "total_amount": self._load_daily_total_amount(trade_date=trade_date, market=market),
            }
            for trade_date in trade_dates
        ]
        return {
            "tool": "market_history_amount",
            "ok": True,
            "data": {
                "market": market,
                "days": days,
                "history": history,
            },
            "error": "",
        }


class MarketMinuteAmountSeriesTool(MarketSnapshotBase):
    def run(self, params: Any) -> Dict[str, Any]:
        args = params if isinstance(params, dict) else {}
        market = self._normalize_market(args.get("market"))
        days = self._normalize_days(args.get("days"), default=5)
        slot = self._resolve_latest_slot()
        trade_dates = self._load_recent_trade_dates(days=days)
        series = [
            {
                "date": str(trade_date),
                "total_amount": self._load_same_minute_total_amount(
                    trade_date=trade_date,
                    minute_index=int(slot["minute_index"]),
                    market=market,
                ),
            }
            for trade_date in trade_dates
        ]
        return {
            "tool": "market_minute_amount_series",
            "ok": True,
            "data": {
                "market": market,
                "trade_date": str(slot.get("trade_date") or ""),
                "minute_index": int(slot.get("minute_index") or 0),
                "snapshot_time": str(slot.get("snapshot_time") or ""),
                "series": series,
            },
            "error": "",
        }


def run_market_realtime_breadth(params: Any) -> Dict[str, Any]:
    tool = MarketRealtimeBreadthTool()
    try:
        return tool.run(params)
    finally:
        try:
            tool.db.close_db()
        except Exception:
            pass


def run_market_history_amount(params: Any) -> Dict[str, Any]:
    tool = MarketHistoryAmountTool()
    try:
        return tool.run(params)
    finally:
        try:
            tool.db.close_db()
        except Exception:
            pass


def run_market_minute_amount_series(params: Any) -> Dict[str, Any]:
    tool = MarketMinuteAmountSeriesTool()
    try:
        return tool.run(params)
    finally:
        try:
            tool.db.close_db()
        except Exception:
            pass
