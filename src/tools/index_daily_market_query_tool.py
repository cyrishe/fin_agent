from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_index_daily_market_service import KingdomaiIndexDailyMarketService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiIndexDailyMarketService().query(
            subject=str(
                params.get("index")
                or params.get("subject")
                or params.get("index_code")
                or params.get("idx_code")
                or params.get("index_name")
                or ""
            ).strip(),
            start=str(params.get("start") or "").strip(),
            end=str(params.get("end") or "").strip(),
            limit=int(params.get("limit", 50) or 50),
        )
        return {
            "tool": "index_daily_market_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "index_daily_market_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
