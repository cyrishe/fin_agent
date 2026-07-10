from __future__ import annotations

from typing import Any, Dict, Mapping

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


class FinanceDataToolRuntimeService:
    """Execute one finance data protocol request through the staged runtime."""

    PROTOCOL = "finance_data_tool.v1"

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
            payload["result"] = None
            return payload

        result = execute_api_call(call, handles)
        payload["result"] = self._result_payload(result)
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
