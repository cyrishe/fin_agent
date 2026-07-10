from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_plate_rank_service import KingdomaiPlateRankService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiPlateRankService().query(
            trade_date=str(params.get("trade_date") or "").strip(),
            top_k=int(params.get("top_k", 10) or 10),
            sort_by=str(params.get("sort_by") or "rise_fall_rate").strip(),
            query=str(
                params.get("plate")
                or params.get("query")
                or params.get("plate_name")
                or params.get("plate_code")
                or ""
            ).strip(),
            include_members=bool(params.get("include_members", True)),
            member_limit=int(params.get("member_limit", 3) or 3),
        )
        return {
            "tool": "plate_rank_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "plate_rank_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
