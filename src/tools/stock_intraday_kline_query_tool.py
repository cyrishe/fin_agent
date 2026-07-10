from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_stock_kline_service import KingdomaiStockKlineService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiStockKlineService().query_intraday(
            subject=str(params.get("stock") or params.get("subject") or params.get("stock_code") or params.get("code") or params.get("name") or "").strip(),
            minute_count=int(params.get("minute_count", 240) or 240),
        )
        return {
            "tool": "stock_intraday_kline_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_intraday_kline_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
