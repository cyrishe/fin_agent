from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_stock_kline_service import KingdomaiStockKlineService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiStockKlineService().query_realtime(
            subject=str(
                params.get("stock")
                or params.get("name")
                or params.get("subject")
                or params.get("stock_code")
                or params.get("code")
                or params.get("query")
                or ""
            ).strip(),
        )
        return {
            "tool": "stock_realtime_quote",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_realtime_quote",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
