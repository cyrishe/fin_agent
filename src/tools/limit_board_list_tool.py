from __future__ import annotations

from typing import Any, Dict, Optional

from src.tools.realtime_market_ranking_tool import RealtimeMarketRankingTool
from src.utils.mysql_utils import StockInfoDbUtils


class LimitBoardListTool(RealtimeMarketRankingTool):
    def __init__(self, db: Optional[StockInfoDbUtils] = None) -> None:
        super().__init__(db=db)

    def _normalize_limit_type(self, value: Any) -> str:
        raw = self._trim(value) or "涨停"
        if raw in {"跌停", "limit_down", "down"}:
            return "跌停"
        return "涨停"

    def run(self, params: Any) -> Dict[str, Any]:
        args = params if isinstance(params, dict) else {}
        market = self._normalize_market(args.get("market"))
        limit_type = self._normalize_limit_type(args.get("limit_type"))
        top_k = self._normalize_top_k(args.get("top_k"), default=50)

        slot = self._resolve_latest_slot()
        rows = self._load_rows(
            trade_date=slot["trade_date"],
            minute_index=int(slot["minute_index"]),
        )
        filtered = [row for row in rows if self._market_matches(self._trim(row.get("stk_code")), market)]
        if limit_type == "跌停":
            filtered = [row for row in filtered if self._is_limit_down(row)]
        else:
            filtered = [row for row in filtered if self._is_limit_up(row)]

        # Keep the tool semantic as a list selector instead of a ranking tool.
        filtered.sort(key=lambda row: self._trim(row.get("stk_code")))

        items = [self._row_to_output(row) for row in filtered[:top_k]]
        return {
            "tool": "涨跌停列表查询",
            "ok": True,
            "data": items,
            "error": "",
            "meta": {
                "market": market,
                "limit_type": limit_type,
                "top_k": top_k,
                "trade_date": str(slot.get("trade_date") or ""),
                "minute_index": int(slot.get("minute_index") or 0),
                "snapshot_time": str(slot.get("snapshot_time") or ""),
                "returned_count": len(items),
            },
        }


def run(params: Any) -> Dict[str, Any]:
    tool = LimitBoardListTool()
    try:
        return tool.run(params)
    finally:
        try:
            tool.db.close_db()
        except Exception:
            pass
