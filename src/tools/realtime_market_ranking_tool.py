from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


SNAPSHOT_TABLE = "aiia_stock_realtime_minute_snapshot"


class RealtimeMarketRankingTool:
    _ST_RE = re.compile(r"^(?:\*?ST|S\*ST)", re.IGNORECASE)

    SORT_ALIASES = {
        "chg_ratio": "chg_ratio",
        "change_pct": "chg_ratio",
        "涨幅": "chg_ratio",
        "涨跌幅": "chg_ratio",
        "交易量": "volume",
        "成交量": "volume",
        "volume": "volume",
        "成交额": "amount",
        "交易额": "amount",
        "amount": "amount",
        "换手": "unsupported_turnover",
        "换手率": "unsupported_turnover",
        "turnover": "unsupported_turnover",
    }

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
    def _normalize_top_k(value: Any, default: int = 20) -> int:
        try:
            normalized = int(value or default)
        except Exception:
            normalized = default
        return max(1, min(normalized, 100))

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

    def _normalize_sort_by(self, value: Any) -> str:
        raw = self._trim(value) or "涨幅"
        return self.SORT_ALIASES.get(raw, self.SORT_ALIASES.get(raw.lower(), "chg_ratio"))

    def _normalize_order(self, value: Any, *, sort_by: str) -> str:
        raw = self._trim(value).lower()
        if raw in {"asc", "ascending", "升序", "从小到大"}:
            return "asc"
        if raw in {"desc", "descending", "降序", "从大到小"}:
            return "desc"
        # 跌幅榜的自然表达仍可通过 order=asc 表示；这里默认所有字段降序。
        return "desc"

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

    def _load_rows(self, *, trade_date: dt.date, minute_index: int) -> List[Dict[str, Any]]:
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
                  open_price,
                  high_price,
                  low_price,
                  preclose_price,
                  chg_value,
                  chg_ratio,
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

    def _market_matches(self, code: str, market: str) -> bool:
        code6 = self._trim(code).split(".", 1)[0]
        if market == "all":
            return True
        if market == "sh":
            return code6.startswith(("60", "68", "69", "90"))
        if market == "sz":
            return code6.startswith(("00", "001", "002", "003", "30"))
        if market == "bj":
            return code6.startswith(("43", "82", "83", "87", "88", "89", "92"))
        return True

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

    def _is_limit_up(self, row: Dict[str, Any]) -> bool:
        chg_ratio = self._safe_float(row.get("chg_ratio"))
        if chg_ratio is None:
            return False
        return chg_ratio >= self._limit_pct(self._trim(row.get("stk_code")), self._trim(row.get("stk_name"))) - 0.2

    def _is_limit_down(self, row: Dict[str, Any]) -> bool:
        chg_ratio = self._safe_float(row.get("chg_ratio"))
        if chg_ratio is None:
            return False
        return chg_ratio <= -self._limit_pct(self._trim(row.get("stk_code")), self._trim(row.get("stk_name"))) + 0.2

    def _row_to_output(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "stock_code": self._trim(row.get("stk_code")),
            "stock_name": self._trim(row.get("stk_name")),
            "snapshot_time": str(row.get("snapshot_time") or ""),
            "latest_price": self._safe_float(row.get("latest_price")),
            "open_price": self._safe_float(row.get("open_price")),
            "high_price": self._safe_float(row.get("high_price")),
            "low_price": self._safe_float(row.get("low_price")),
            "preclose_price": self._safe_float(row.get("preclose_price")),
            "chg_value": self._safe_float(row.get("chg_value")),
            "chg_ratio": self._safe_float(row.get("chg_ratio")),
            "amount": self._safe_float(row.get("amount")),
            "volume": self._safe_float(row.get("volume")),
        }

    def run(self, params: Any) -> Dict[str, Any]:
        args = params if isinstance(params, dict) else {}
        market = self._normalize_market(args.get("market"))
        sort_key = self._normalize_sort_by(args.get("sort_by"))
        order = self._normalize_order(args.get("order"), sort_by=sort_key)
        top_k = self._normalize_top_k(args.get("top_k"), default=20)
        if sort_key == "unsupported_turnover":
            return {
                "tool": "实时行情排名查询",
                "ok": False,
                "data": [],
                "error": "当前分钟快照表不含换手率字段，请改用涨幅、跌幅、成交额或成交量。",
            }

        slot = self._resolve_latest_slot()
        rows = self._load_rows(
            trade_date=slot["trade_date"],
            minute_index=int(slot["minute_index"]),
        )
        filtered = [row for row in rows if self._market_matches(self._trim(row.get("stk_code")), market)]

        reverse = order == "desc"
        def sort_value(row: Dict[str, Any], field: str) -> float:
            value = self._safe_float(row.get(field))
            if value is None:
                return -1.0e30 if reverse else 1.0e30
            return value
        if sort_key == "chg_ratio":
            filtered.sort(key=lambda row: sort_value(row, "chg_ratio"), reverse=reverse)
        elif sort_key == "volume":
            filtered.sort(key=lambda row: sort_value(row, "volume"), reverse=reverse)
        elif sort_key == "amount":
            filtered.sort(key=lambda row: sort_value(row, "amount"), reverse=reverse)

        items = [self._row_to_output(row) for row in filtered[:top_k]]
        return {
            "tool": "实时行情排名查询",
            "ok": True,
            "data": items,
            "error": "",
            "meta": {
                "market": market,
                "sort_by": sort_key,
                "order": order,
                "top_k": top_k,
                "trade_date": str(slot.get("trade_date") or ""),
                "minute_index": int(slot.get("minute_index") or 0),
                "snapshot_time": str(slot.get("snapshot_time") or ""),
                "returned_count": len(items),
            },
        }


def run(params: Any) -> Dict[str, Any]:
    tool = RealtimeMarketRankingTool()
    try:
        return tool.run(params)
    finally:
        try:
            tool.db.close_db()
        except Exception:
            pass
