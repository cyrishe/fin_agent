from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_plate_member_service import KingdomaiPlateMemberService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiPlateMemberService().query(
            subject=str(params.get("stock") or params.get("subject") or params.get("stock_code") or params.get("code") or params.get("stock_name") or "").strip(),
            subject_type="stock",
            trade_date=str(params.get("as_of") or "").strip(),
            limit=int(params.get("limit", 50) or 50),
            sort_by=str(params.get("sort_by") or "plate_rise_fall_rate").strip(),
        )
        return {
            "tool": "stock_plate_membership_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_plate_membership_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
