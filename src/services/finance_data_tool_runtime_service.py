from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from typing import Any, Dict, Mapping

from src.experiments.staged_data_protocol.phase2.api_runner import execute_api_call
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_structure import structure_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.dynamic_cal_provider import (
    HISTORICAL_DEFAULT_STOCK_FIELDS,
    HISTORICAL_RAW_ONLY_ARG,
    HISTORICAL_RAW_STOCK_FIELDS,
)
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.trade_date_resolver import (
    TradeDateResolutionError,
    TradeDateResolver,
)


class FinanceDataToolRuntimeService:
    """Execute one finance data protocol request through the staged runtime."""

    PROTOCOL = "finance_data_tool.v1"
    HISTORICAL_REPLAY_APIS = frozenset({"stock.quote.dynamic_cal"})
    HISTORICAL_UNSAFE_STOCK_FIELDS = frozenset(
        {
            "name",
            "adjpreclose",
            "adjopen",
            "adjhigh",
            "adjlow",
            "adjclose",
            "minute_index",
            "minute_time",
            "snapshot_time",
            "snapshot_slot",
            "minute_amount",
            "minute_volumn",
            "source",
            "is_fallback",
        }
    )
    MAX_BINDING_KEYS = 16
    MAX_BINDING_ITEMS = 10_000
    MAX_BINDING_BYTES = 1_000_000
    BINDING_REF_RE = re.compile(r"^\$([A-Za-z_]\w*)$")

    def __init__(self, *, trade_date_resolver: TradeDateResolver | None = None) -> None:
        self.trade_date_resolver = trade_date_resolver

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

    def execute_historical_request(
        self,
        *,
        request: str,
        effective_as_of: dt.date | str,
        allowed_symbols: list[str] | tuple[str, ...],
        bindings: Mapping[str, Any] | None = None,
        previous_results: Mapping[str, ResultHandle] | None = None,
    ) -> Dict[str, Any]:
        """Execute one narrowly supported query under a trusted replay cutoff.

        The cutoff and security scope are supplied by the server-side replay
        host.  They deliberately do not become public Custom Tool inputs.
        """

        cutoff = self._date(effective_as_of)
        symbols = self._symbols(allowed_symbols)
        return self._execute_request(
            request=request,
            bindings=bindings,
            previous_results=previous_results,
            historical_cutoff=cutoff,
            historical_symbols=symbols,
        )

    def _execute_request(
        self,
        *,
        request: str,
        bindings: Mapping[str, Any] | None = None,
        previous_results: Mapping[str, ResultHandle] | None = None,
        historical_cutoff: dt.date | None = None,
        historical_symbols: tuple[str, ...] = (),
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

        date_resolution = None
        historical_scope: Dict[str, Any] | None = None
        if historical_cutoff is not None:
            if call.api not in self.HISTORICAL_REPLAY_APIS:
                return self._historical_denial(
                    payload,
                    status="historical_query_unsupported",
                    reason=(
                        f"finance API {call.api} does not declare a trusted "
                        "historical replay cutoff"
                    ),
                    cutoff=historical_cutoff,
                    symbols=historical_symbols,
                )
            if self._explicit_intraday_mode(call.args):
                return self._historical_denial(
                    payload,
                    status="historical_realtime_denied",
                    reason="historical replay cannot use realtime market data",
                    cutoff=historical_cutoff,
                    symbols=historical_symbols,
                )
            unsafe_fields = self._historical_unsafe_fields(call)
            if unsafe_fields:
                return self._historical_denial(
                    payload,
                    status="historical_field_unsupported",
                    reason=(
                        "historical replay cannot use fields without a trusted "
                        f"raw trade-date snapshot: {', '.join(unsafe_fields)}"
                    ),
                    cutoff=historical_cutoff,
                    symbols=historical_symbols,
                )
            call, historical_scope = self._historical_call(
                call,
                cutoff=historical_cutoff,
                symbols=historical_symbols,
            )
            payload["call"] = {
                "result_id": call.result_id,
                "api": call.api,
                "args": call.args,
                "outputs": call.outputs,
            }
            payload["historical_scope"] = historical_scope
        elif self.trade_date_resolver is not None:
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

        api_started = time.monotonic()
        try:
            if self.trade_date_resolver is None:
                result = execute_api_call(call, handles)
            else:
                result = execute_api_call(
                    call,
                    handles,
                )
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
        if historical_cutoff is not None:
            future_dates = self._future_result_dates(
                payload["result"],
                cutoff=historical_cutoff,
            )
            if future_dates:
                payload["ok"] = False
                payload["execution"] = {
                    "ok": False,
                    "status": "historical_result_after_cutoff",
                    "reason": "finance result contains a date after the replay cutoff",
                    "future_dates": future_dates,
                }
                payload["result"] = None
                return payload
        if date_resolution is not None and date_resolution.warnings:
            payload["execution"]["warnings"] = date_resolution.warnings
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

    @classmethod
    def _historical_call(
        cls,
        call: Any,
        *,
        cutoff: dt.date,
        symbols: tuple[str, ...],
    ) -> tuple[Any, Dict[str, Any]]:
        from src.experiments.staged_data_protocol.phase2.models import ApiCall

        args = dict(call.args)
        requested_codes = cls._requested_codes(args.get("codes"))
        scoped_codes = list(symbols)
        if requested_codes:
            requested = set(requested_codes)
            scoped_codes = [item for item in symbols if item in requested]
        # The provider's calendar window is exclusive on `as_of`; D + 1 day
        # therefore means raw market rows are bounded by D, including D itself.
        args["as_of"] = (cutoff + dt.timedelta(days=1)).isoformat()
        args.pop("realtime", None)
        args["mode"] = 0
        args["codes"] = scoped_codes or ["__historical_scope_empty__"]
        requested_fields = cls._field_names(args.get("fields"))
        args["fields"] = ", ".join(
            requested_fields or HISTORICAL_DEFAULT_STOCK_FIELDS
        )
        args[HISTORICAL_RAW_ONLY_ARG] = True
        scope_payload = {
            "effective_as_of": cutoff.isoformat(),
            "provider_as_of_exclusive": args["as_of"],
            "symbol_count": len(scoped_codes),
            "symbols_fingerprint": cls._fingerprint(scoped_codes),
            "policy": "raw trade dates <= effective_as_of",
            "field_policy": "raw trade-date-bound quote columns only",
        }
        return (
            ApiCall(
                result_id=call.result_id,
                api=call.api,
                args=args,
                outputs=list(call.outputs),
                raw=call.raw,
            ),
            scope_payload,
        )

    @classmethod
    def _historical_denial(
        cls,
        payload: Dict[str, Any],
        *,
        status: str,
        reason: str,
        cutoff: dt.date,
        symbols: tuple[str, ...],
    ) -> Dict[str, Any]:
        payload["ok"] = False
        payload["execution"] = {
            "ok": False,
            "status": status,
            "reason": reason,
        }
        payload["historical_scope"] = {
            "effective_as_of": cutoff.isoformat(),
            "symbol_count": len(symbols),
            "symbols_fingerprint": cls._fingerprint(symbols),
        }
        payload["result"] = None
        return payload

    @staticmethod
    def _date(value: dt.date | str) -> dt.date:
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("effective_as_of must be an ISO date") from exc

    @staticmethod
    def _code(value: Any) -> str:
        text = str(value or "").strip().upper()
        if "." in text:
            text = text.split(".", 1)[0]
        digits = "".join(item for item in text if item.isdigit())
        return digits.zfill(6) if digits else text

    @classmethod
    def _symbols(cls, values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        symbols: list[str] = []
        seen: set[str] = set()
        for raw in values:
            symbol = cls._code(raw)
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
        if not symbols:
            raise ValueError("historical replay requires at least one allowed symbol")
        return tuple(symbols)

    @classmethod
    def _requested_codes(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        else:
            text = str(value or "").strip().strip("[]()")
            raw_values = re.split(r"\s*,\s*", text) if text else []
        return tuple(
            code
            for raw in raw_values
            if (code := cls._code(str(raw).strip().strip("'\"")))
        )

    @staticmethod
    def _field_names(value: Any) -> tuple[str, ...]:
        if isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            raw_values = str(value or "").split(",")
        return tuple(
            str(item or "").strip()
            for item in raw_values
            if str(item or "").strip()
        )

    @classmethod
    def _historical_unsafe_fields(cls, call: Any) -> list[str]:
        args = call.args if isinstance(call.args, Mapping) else {}
        unsafe = {
            field
            for field in cls._field_names(args.get("fields"))
            if field not in HISTORICAL_RAW_STOCK_FIELDS
        }
        if args.get("name") not in (None, "") or args.get("names") not in (
            None,
            "",
        ):
            unsafe.add("name")
        filter_text = str(args.get("filter") or "")
        filter_fields = re.findall(
            r"\b([A-Za-z_]\w*)\s*(?:in\b|==|!=|>=|<=|=|>|<)",
            filter_text,
            flags=re.IGNORECASE,
        )
        if any(field.lower() == "name" for field in filter_fields):
            unsafe.add("name")
        order_field = str(args.get("order") or "").strip().split(" ", 1)[0]
        if order_field in cls.HISTORICAL_UNSAFE_STOCK_FIELDS:
            unsafe.add(order_field)
        for output in call.outputs or []:
            token = re.split(
                r"\s+as\s+",
                str(output or "").strip(),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            if token in cls.HISTORICAL_UNSAFE_STOCK_FIELDS:
                unsafe.add(token)
        return sorted(unsafe)

    @staticmethod
    def _explicit_realtime(value: Any) -> bool:
        if value in (None, ""):
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return int(value) in {1, 2}
        return str(value).strip().lower() not in {
            "0",
            "false",
            "no",
            "history",
            "historical",
        }

    @classmethod
    def _explicit_intraday_mode(cls, args: Mapping[str, Any]) -> bool:
        if args.get("mode") not in (None, ""):
            return cls._explicit_realtime(args.get("mode"))
        return cls._explicit_realtime(args.get("realtime"))

    @staticmethod
    def _future_result_dates(
        result: Mapping[str, Any],
        *,
        cutoff: dt.date,
    ) -> list[str]:
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        future: set[str] = set()
        date_fields = {"tradedate", "trade_date", "end_date", "as_of_date"}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for field in date_fields.intersection(row):
                raw = str(row.get(field) or "").strip()
                try:
                    parsed = dt.date.fromisoformat(raw)
                except ValueError:
                    continue
                if parsed > cutoff:
                    future.add(raw)
        return sorted(future)

    @staticmethod
    def _fingerprint(value: Any) -> str:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
