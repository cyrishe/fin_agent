from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Mapping

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_structure import structure_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


class FinanceDataToolRuntimeService:
    """Execute one finance data protocol request through the staged runtime."""

    PROTOCOL = "finance_data_tool.v1"
    MAX_BINDING_KEYS = 16
    MAX_BINDING_ITEMS = 10_000
    MAX_BINDING_BYTES = 1_000_000
    BINDING_REF_RE = re.compile(r"^\$([A-Za-z_]\w*)$")

    def execute_request(
        self,
        *,
        request: str,
        bindings: Mapping[str, Any] | None = None,
        previous_results: Mapping[str, ResultHandle] | None = None,
    ) -> Dict[str, Any]:
        return self._execute_request(
            request=request,
            bindings=bindings,
            previous_results=previous_results,
        )

    def _execute_request(
        self,
        *,
        request: str,
        bindings: Mapping[str, Any] | None = None,
        previous_results: Mapping[str, ResultHandle] | None = None,
    ) -> Dict[str, Any]:
        request_text = str(request or "").strip()
        if not request_text:
            raise ValueError("request is required")

        static_started = time.monotonic()
        handles = dict(previous_results or {})
        call = self._bind_call(parse_api_call(request_text), bindings)
        validation = validate_call(call, handles)
        static_duration_ms = round((time.monotonic() - static_started) * 1_000, 3)
        payload: Dict[str, Any] = {
            "protocol": self.PROTOCOL,
            "request": call.raw,
            "structured_call": structure_call(call),
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
            "timings": {
                "static_validation_ms": static_duration_ms,
                "api_execution_ms": 0.0,
            },
        }
        if not validation.ok:
            payload["ok"] = False
            payload["result"] = None
            return payload

        api_started = time.monotonic()
        try:
            result = execute_api_call(call, handles)
        except Exception as exc:
            payload["timings"]["api_execution_ms"] = round(
                (time.monotonic() - api_started) * 1_000,
                3,
            )
            payload["ok"] = False
            payload["execution"] = {
                "ok": False,
                "status": "provider_exception",
                "reason": str(exc),
            }
            payload["result"] = None
            return payload
        payload["timings"]["api_execution_ms"] = round(
            (time.monotonic() - api_started) * 1_000,
            3,
        )
        payload["result"] = self._result_payload(result)
        payload["execution"] = self._execution_payload(result)
        payload["ok"] = bool(payload["execution"]["ok"])
        return payload

    @classmethod
    def _bind_call(cls, call: Any, bindings: Mapping[str, Any] | None) -> Any:
        """Resolve exact ``$name`` arguments without expanding the DSL string."""

        from src.experiments.staged_data_protocol.phase2.models import ApiCall

        normalized = cls._normalize_bindings(bindings)
        referenced: set[str] = set()
        args: Dict[str, Any] = {}
        for key, value in call.args.items():
            match = cls.BINDING_REF_RE.fullmatch(value) if isinstance(value, str) else None
            if not match:
                args[key] = value
                continue
            binding_name = match.group(1)
            if binding_name not in normalized:
                raise ValueError(f"missing finance query binding: {binding_name}")
            referenced.add(binding_name)
            args[key] = normalized[binding_name]
        unused = sorted(set(normalized) - referenced)
        if unused:
            raise ValueError(f"unused finance query bindings: {', '.join(unused)}")
        return ApiCall(
            result_id=call.result_id,
            api=call.api,
            args=args,
            outputs=list(call.outputs),
            raw=call.raw,
        )

    @classmethod
    def _normalize_bindings(
        cls,
        bindings: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        if bindings is None:
            return {}
        if not isinstance(bindings, Mapping):
            raise ValueError("finance query bindings must be an object")
        if len(bindings) > cls.MAX_BINDING_KEYS:
            raise ValueError(
                f"finance query bindings exceed key limit ({cls.MAX_BINDING_KEYS})"
            )
        normalized: Dict[str, Any] = {}
        total_items = 0
        for raw_name, raw_value in bindings.items():
            name = str(raw_name or "").strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                raise ValueError(f"invalid finance query binding name: {name or '-'}")
            if isinstance(raw_value, (list, tuple)):
                values = [cls._binding_scalar(item, name=name) for item in raw_value]
                total_items += len(values)
                normalized[name] = values
            else:
                total_items += 1
                normalized[name] = cls._binding_scalar(raw_value, name=name)
        if total_items > cls.MAX_BINDING_ITEMS:
            raise ValueError(
                f"finance query bindings exceed item limit ({cls.MAX_BINDING_ITEMS})"
            )
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > cls.MAX_BINDING_BYTES:
            raise ValueError(
                f"finance query bindings exceed byte limit ({cls.MAX_BINDING_BYTES})"
            )
        return normalized

    @staticmethod
    def _binding_scalar(value: Any, *, name: str) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError(
            f"finance query binding {name} must contain JSON scalar values"
        )

    @staticmethod
    def _result_payload(result: ResultHandle) -> Dict[str, Any]:
        data = result.data
        if isinstance(data, Mapping):
            data = {
                key: value
                for key, value in data.items()
                if key != "sql_shape"
            }
        return {
            "name": result.name,
            "api": result.api,
            "columns": result.columns,
            "data": data,
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
