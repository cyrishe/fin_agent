from __future__ import annotations

from typing import Any, Dict

from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = FinanceDataToolRuntimeService().execute_request(
            request=str(params.get("request") or "").strip(),
        )
        return {
            "tool": "finance_data_query",
            "ok": bool(payload.get("validation", {}).get("ok")),
            "data": payload,
            "error": "" if payload.get("validation", {}).get("ok") else "; ".join(payload.get("validation", {}).get("errors") or []),
        }
    except Exception as exc:
        return {
            "tool": "finance_data_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
