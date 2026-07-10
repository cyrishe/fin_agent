from __future__ import annotations

from typing import Any, Dict

from src.services.kingdomai_stock_announcement_service import KingdomaiStockAnnouncementService


def _as_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "y", "yes", "是"}:
        return True
    if text in {"0", "false", "n", "no", "否", ""}:
        return False
    return bool(value)


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = KingdomaiStockAnnouncementService().query(
            subject=str(
                params.get("stock")
                or params.get("subject")
                or params.get("stock_code")
                or params.get("stk_code")
                or params.get("stock_name")
                or params.get("company")
                or ""
            ).strip(),
            start_date=str(params.get("start_date") or "").strip(),
            end_date=str(params.get("end_date") or params.get("ann_date") or "").strip(),
            keyword=str(params.get("keyword") or "").strip(),
            limit=int(params.get("limit", 20) or 20),
            include_text=_as_bool(params.get("include_text", False)),
            max_text_chars=int(params.get("max_text_chars", 500) or 500),
        )
        return {
            "tool": "stock_announcement_query",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_announcement_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
