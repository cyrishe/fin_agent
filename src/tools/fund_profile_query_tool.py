from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_fund_daily_market_service import KingdomaiFundDailyMarketService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiFundDailyMarketService().query_profile(
            subject=str(params.get("fund") or params.get("subject") or params.get("fund_code") or params.get("fund_name") or "").strip(),
        )
        return {
            "tool": "fund_profile_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "fund_profile_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
