from __future__ import annotations

from typing import Any, Dict

from src.services.custom_tool_service import CustomToolRuntimeService


def run(args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(args or {})
    tool_name = str(payload.get("tool_name") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    return CustomToolRuntimeService().run(tool_name, arguments)
