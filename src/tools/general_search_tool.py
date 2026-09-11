from __future__ import annotations

from typing import Any, Dict, Iterable

from src.services.search_gateway_service import SearchGatewayService


TOOL_NAME = "general_search"


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    result = SearchGatewayService().search(
        query=str(params.get("query") or params.get("q") or "").strip(),
        limit=params.get("limit", 10),
        start_time=str(params.get("start_time") or params.get("start_date") or "").strip(),
        end_time=str(params.get("end_time") or params.get("end_date") or "").strip(),
        category_scope=_list(params.get("category_scope")),
        source_scope=_list(params.get("source_scope")),
    )
    ok = result.get("status") == "ok"
    return {
        "tool": TOOL_NAME,
        "ok": ok,
        "provider": str(result.get("provider") or ""),
        "coverage": str(result.get("coverage") or ""),
        "query": str(result.get("query") or ""),
        "data": list(result.get("items") or []),
        "total": int(result.get("total") or 0),
        "error": "" if ok else str(result.get("reason") or "search failed"),
    }
