from __future__ import annotations

from typing import Any, Dict, Mapping

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import (
    TradeDateResolutionError,
    TradeDateResolver,
)


class FinanceDataToolRuntimeService:
    """Execute one finance data protocol request through the staged runtime."""

    PROTOCOL = "finance_data_tool.v1"

    def __init__(self, *, trade_date_resolver: TradeDateResolver | None = None) -> None:
        self.trade_date_resolver = trade_date_resolver

    def execute_request(
        self,
        *,
        request: str,
        previous_results: Mapping[str, ResultHandle] | None = None,
    ) -> Dict[str, Any]:
        request_text = str(request or "").strip()
        if not request_text:
            raise ValueError("request is required")

        handles = dict(previous_results or {})
        call = parse_api_call(request_text)
        validation = validate_call(call, handles)
        payload: Dict[str, Any] = {
            "protocol": self.PROTOCOL,
            "request": call.raw,
            "call": {
                "result_id": call.result_id,
                "api": call.api,
                "args": call.args,
                "outputs": call.outputs,
            },
            "validation": {
                "ok": validation.ok,
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
        }
        if not validation.ok:
            payload["ok"] = False
            payload["result"] = None
            return payload

        date_resolution = None
        if self.trade_date_resolver is not None:
            try:
                date_resolution = self.trade_date_resolver.resolve(call)
                call = date_resolution.call
                payload["call"] = {
                    "result_id": call.result_id,
                    "api": call.api,
                    "args": call.args,
                    "outputs": call.outputs,
                }
                if date_resolution.warnings:
                    payload["date_resolution"] = {
                        "warnings": date_resolution.warnings,
                    }
            except TradeDateResolutionError as exc:
                payload["ok"] = False
                payload["execution"] = {
                    "ok": False,
                    "status": "provider_error",
                    "reason": f"trade date resolution failed: {exc}",
                }
                payload["result"] = None
                return payload

        try:
            if self.trade_date_resolver is None:
                result = execute_api_call(call, handles)
            else:
                result = execute_api_call(
                    call,
                    handles,
                )
        except Exception as exc:
            payload["ok"] = False
            payload["execution"] = {
                "ok": False,
                "status": "provider_exception",
                "reason": str(exc),
            }
            payload["result"] = None
            return payload
        payload["result"] = self._result_payload(result)
        payload["execution"] = self._execution_payload(result)
        if date_resolution is not None and date_resolution.warnings:
            payload["execution"]["warnings"] = date_resolution.warnings
        payload["ok"] = bool(payload["execution"]["ok"])
        return payload

    @staticmethod
    def _result_payload(result: ResultHandle) -> Dict[str, Any]:
        return {
            "name": result.name,
            "api": result.api,
            "columns": result.columns,
            "data": result.data,
            "step_id": result.step_id,
            "task": result.task,
        }

    @staticmethod
    def _execution_payload(result: ResultHandle) -> Dict[str, Any]:
        data = result.data if isinstance(result.data, Mapping) else {}
        status = str(data.get("status") or "").strip().lower()
        # Older or injected runtimes may not expose provider status. Preserve
        # their successful contract while making every explicit non-ok provider
        # status observable to callers.
        ok = not status or status == "ok"
        payload: Dict[str, Any] = {
            "ok": ok,
            "status": status or "ok",
        }
        reason = str(data.get("reason") or "").strip()
        if reason:
            payload["reason"] = reason
        return payload
