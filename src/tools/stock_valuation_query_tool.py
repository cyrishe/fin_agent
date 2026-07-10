from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_stock_valuation_service import KingdomaiStockValuationService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiStockValuationService().query(
            subject=str(params.get("stock") or params.get("subject") or params.get("name") or params.get("code") or "").strip(),
            start=str(params.get("start") or "").strip(),
            end=str(params.get("end") or "").strip(),
            limit=int(params.get("limit", 50) or 50),
        )
        return {
            "tool": "stock_valuation_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_valuation_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
