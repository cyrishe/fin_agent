from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_plate_member_service import KingdomaiPlateMemberService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiPlateMemberService().query(
            subject=str(params.get("plate") or params.get("subject") or params.get("plate_code") or params.get("plate_name") or "").strip(),
            subject_type="plate",
            trade_date=str(params.get("as_of") or "").strip(),
            limit=int(params.get("limit", 50) or 50),
            sort_by=str(params.get("sort_by") or "stock_rise_fall_rate").strip(),
        )
        return {
            "tool": "plate_members_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "plate_members_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
