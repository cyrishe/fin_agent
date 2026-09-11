from __future__ import annotations

from typing import Any, Dict

from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import TradeDateResolver


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = FinanceDataToolRuntimeService(
            trade_date_resolver=TradeDateResolver()
        ).execute_request(
            request=str(params.get("request") or "").strip(),
        )
        validation = (
            payload.get("validation")
            if isinstance(payload.get("validation"), dict)
            else {}
        )
        execution = (
            payload.get("execution")
            if isinstance(payload.get("execution"), dict)
            else {}
        )
        validation_ok = bool(validation.get("ok"))
        execution_ok = not execution or bool(execution.get("ok"))
        errors = [str(item) for item in validation.get("errors") or []]
        if validation_ok and not execution_ok:
            status = str(execution.get("status") or "provider_error")
            reason = str(execution.get("reason") or "").strip()
            errors.append(status + (f": {reason}" if reason else ""))
        return {
            "tool": "finance_data_query",
            "ok": validation_ok and execution_ok,
            "data": payload,
            "error": "; ".join(errors),
        }
    except Exception as exc:
        return {
            "tool": "finance_data_query",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
