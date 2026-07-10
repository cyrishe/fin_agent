from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.services.quant_factor_screening_service import QuantFactorScreeningError, QuantFactorScreeningService


def run(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    runtime = payload.pop("_runtime", {}) if isinstance(payload.get("_runtime"), dict) else {}
    data_root = runtime.get("data_root") or "data"
    service = QuantFactorScreeningService(data_root=Path(data_root))
    try:
        data = service.run(payload)
    except QuantFactorScreeningError as exc:
        return {
            "tool": QuantFactorScreeningService.TOOL_NAME,
            "ok": False,
            "data": exc.data,
            "error": exc.message,
            "meta": {"failure_kind": exc.failure_kind},
        }
    except Exception as exc:
        return {
            "tool": QuantFactorScreeningService.TOOL_NAME,
            "ok": False,
            "data": {},
            "error": str(exc),
            "meta": {"failure_kind": "screening_failed"},
        }
    return {"tool": QuantFactorScreeningService.TOOL_NAME, "ok": True, "data": data, "error": "", "meta": {"screening_mode": "code_runtime"}}
