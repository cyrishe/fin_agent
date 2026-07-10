from __future__ import annotations

from typing import Any, Dict, List, Mapping


STOCK_QUOTE_METHODS: List[Dict[str, Any]] = [
    {
        "method_id": "stock.quote.close",
        "field": "close",
        "operation": "field",
        "description": "Return close price for the selected stock quote rows.",
        "arguments_schema": {
            "security_ref": "optional string, such as S1.result or an explicit stock code list",
            "trade_date": "optional string, such as today or latest",
        },
        "returns": ["code", "name", "trade_date", "close"],
    },
    {
        "method_id": "stock.quote.pct",
        "field": "pct",
        "operation": "field",
        "description": "Return daily percentage change for the selected stock quote rows.",
        "arguments_schema": {
            "security_ref": "optional string, such as S1.result or an explicit stock code list",
            "trade_date": "optional string, such as today or latest",
        },
        "returns": ["code", "name", "trade_date", "pct"],
    },
    {
        "method_id": "stock.quote.pct.kd_pct_sum",
        "field": "pct",
        "operation": "window_metric",
        "description": "Return the fixed k-trading-day percentage change metric. The implementation is fixed by the API layer.",
        "arguments_schema": {
            "k": "required integer, trading-day window size",
            "security_ref": "optional string, such as S1.result or an explicit stock code list",
            "end_date": "optional string, defaults to latest trading day",
        },
        "returns": ["code", "name", "end_date", "k", "kd_pct_sum"],
    },
    {
        "method_id": "stock.quote.close.kd_close_high",
        "field": "close",
        "operation": "window_metric",
        "description": "Return the highest close price over the fixed k-trading-day window.",
        "arguments_schema": {
            "k": "required integer, trading-day window size",
            "security_ref": "optional string, such as S1.result or an explicit stock code list",
            "end_date": "optional string, defaults to latest trading day",
        },
        "returns": ["code", "name", "end_date", "k", "kd_close_high"],
    },
]

_METHODS_BY_ID = {item["method_id"]: item for item in STOCK_QUOTE_METHODS}


def validate_stock_quote_method_call(call: Mapping[str, Any]) -> List[str]:
    method_id = str(call.get("method_id") or "").strip()
    if method_id not in _METHODS_BY_ID:
        return [f"unknown stock quote method: {method_id}"]
    method = _METHODS_BY_ID[method_id]
    arguments = call.get("arguments")
    if not isinstance(arguments, Mapping):
        return [f"{method_id}: arguments must be an object"]
    errors: List[str] = []
    if method.get("operation") == "window_metric":
        k = arguments.get("k")
        if not isinstance(k, int) or k <= 0:
            errors.append(f"{method_id}: k must be a positive integer")
    return errors


def mock_stock_quote_method_call(call: Mapping[str, Any]) -> Dict[str, Any]:
    errors = validate_stock_quote_method_call(call)
    method_id = str(call.get("method_id") or "").strip()
    method = _METHODS_BY_ID.get(method_id, {})
    return {
        "method_id": method_id,
        "status": "mock_executed" if not errors else "mock_blocked",
        "arguments": dict(call.get("arguments") or {}),
        "schema": list(method.get("returns") or []),
        "errors": errors,
    }

