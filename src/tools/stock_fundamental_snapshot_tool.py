from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_stock_fundamental_service import KingdomaiStockFundamentalService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiStockFundamentalService().snapshot(
            subject=str(params.get("stock") or params.get("subject") or params.get("name") or params.get("code") or "").strip(),
        )
        return {
            "tool": "stock_fundamental_snapshot",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_fundamental_snapshot",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
