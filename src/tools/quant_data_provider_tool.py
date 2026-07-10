from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.services.quant_data_provider_service import QuantDataProviderError, QuantDataProviderService


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload or {})
    runtime = payload.pop("_runtime", {}) if isinstance(payload.get("_runtime"), dict) else {}
    data_root = runtime.get("data_root") or "data"
    service = QuantDataProviderService(data_root=Path(data_root))
    try:
        data = service.prepare(payload)
    except QuantDataProviderError as exc:
        return {
            "tool": QuantDataProviderService.TOOL_NAME,
            "ok": False,
            "data": {},
            "error": exc.message,
            "meta": {"failure_kind": exc.failure_kind},
        }
    except Exception as exc:
        return {
            "tool": QuantDataProviderService.TOOL_NAME,
            "ok": False,
            "data": {},
            "error": str(exc),
            "meta": {"failure_kind": "query_failed"},
        }
    return {
        "tool": QuantDataProviderService.TOOL_NAME,
        "ok": True,
        "data": data,
        "error": "",
        "meta": {"physical_format": "jsonl"},
    }
