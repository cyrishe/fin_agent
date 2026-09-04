from __future__ import annotations

import re
from datetime import date
from typing import Mapping

from src.experiments.staged_data_protocol.phase2.agg_protocol import AGG_METHODS, metric_column, parse_agg_spec
from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
from src.experiments.staged_data_protocol.phase2.call_structure import (
    runtime_field_contract,
    validate_call_structure,
)
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle, ValidationResult


REF_RE = re.compile(r"\b(r\d+)\.([A-Za-z_]\w*)\b")
PREVIOUS_METRIC_RE = re.compile(r"^(r\d+)\.([A-Za-z_]\w*)$")
KD_METHOD_TOKEN_RE = re.compile(r"^[A-Za-z_]\w*$")
FILTER_REF_PREFIX_RE = re.compile(
    r"\b[A-Za-z_]\w*\s+in\s*$",
    flags=re.IGNORECASE,
)
EXACT_TRADE_DATE_RE = re.compile(
    r"\b(?:tradedate|trade_date|date)\s*(?:==|=)\s*['\"]?(\d{4}-\d{2}-\d{2})['\"]?",
    flags=re.IGNORECASE,
)
OUTPUT_ALIAS_RE = re.compile(
    r"^(?P<expression>.+?)\s+as\s+(?P<alias>[A-Za-z_]\w*)$",
    flags=re.IGNORECASE | re.DOTALL,
)


def validate_call(call: ApiCall, previous_results: Mapping[str, ResultHandle]) -> ValidationResult:
    errors: list[str] = []
    resolved = resolve_api(call.api)
    if not resolved:
        return ValidationResult(ok=False, errors=[f"API_ERROR: unsupported api={call.api}"])
    view = resolved["view"]
    if resolved["type"] == "kd":
        kd = view.get("kd") or {}
        field_name = resolved.get("field")
        method = resolved.get("method")
        if method not in set(kd.get(field_name) or []):
            if not _allows_kd_fallback(resolved):
                available = [f"kd_{field}_{item}" for field, methods in kd.items() for item in methods]
                errors.append(f"API_ERROR: {call.api} unsupported. available={available}")
        k = call.args.get("k")
        if not isinstance(k, int) or k <= 0:
            errors.append("ARG_ERROR: kd api requires positive integer k")
    if resolved["type"] == "agg":
        _validate_agg_args(call, previous_results, view, errors, dataview=str(resolved.get("dataview") or ""))
    if resolved["type"] == "dynamic_cal":
        task = str(call.args.get("task") or "").strip()
        if not task:
            errors.append("ARG_ERROR: dynamic_cal requires task")
        k = call.args.get("k")
        if k is not None and (not isinstance(k, int) or k <= 0):
            errors.append("ARG_ERROR: dynamic_cal k must be a positive integer when provided")
        _validate_dynamic_fields(call, view, errors)
    if resolved.get("subject") == "stock" and resolved.get("dataview") == "quote":
        _validate_stock_quote_mode(call, errors)
    errors.extend(validate_call_structure(call, previous_results))
    _validate_common_args(call, errors)
    _validate_refs(call, previous_results, errors)
    _validate_outputs(call, resolved, errors)
    return ValidationResult(ok=not errors, errors=errors)


def _allows_kd_fallback(resolved: Mapping[str, object]) -> bool:
    if resolved.get("dataview") not in {"margin", "moneyflow", "pricevalue", "quote"}:
        return False
    view = resolved.get("view")
    if not isinstance(view, Mapping):
        return False
    field_name = str(resolved.get("field") or "")
    method = str(resolved.get("method") or "")
    fields = set((view.get("fields") or {}).keys())
    if field_name not in fields or not KD_METHOD_TOKEN_RE.fullmatch(method):
        return False
    if resolved.get("dataview") in {"moneyflow", "pricevalue", "quote"}:
        return method in AGG_METHODS
    return True


def _validate_common_args(call: ApiCall, errors: list[str]) -> None:
    if "limit" in call.args and not isinstance(call.args.get("limit"), int):
        errors.append(f"ARG_ERROR: limit must be an integer, got={call.args.get('limit')}")
    elif "limit" in call.args and (
        isinstance(call.args.get("limit"), bool)
        or int(call.args.get("limit")) == 0
        or int(call.args.get("limit")) < -1
    ):
        errors.append("ARG_ERROR: limit must be -1 or a positive integer")
    for key, value in call.args.items():
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        if re.search(r"\bselect\b.+\bfrom\s+r\d+\b", lowered):
            errors.append(f"REF_ERROR: {key} must use rN.column refs, not SQL subquery refs")


def _validate_stock_quote_mode(call: ApiCall, errors: list[str]) -> None:
    mode = _stock_quote_effective_mode(call.args)
    if mode is None:
        errors.append("ARG_ERROR: stock.quote mode must be one of 0, 1, 2")
        return
    if mode == 0:
        exact_date = _stock_quote_exact_date(call.args)
        if exact_date is not None and exact_date >= date.today():
            errors.append(
                "ARG_ERROR: stock.quote mode=0 only returns day K through the "
                "previous trading day; use mode=2 for today's "
                "latest quote or mode=1 for today's minute K"
            )
        if "period" in call.args:
            errors.append("ARG_ERROR: stock.quote period is only valid with mode=1 minute K")
        if "count" in call.args:
            count = call.args.get("count")
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count <= 0
                or count > 5000
            ):
                errors.append(
                    "ARG_ERROR: stock.quote day K count must be an integer "
                    "between 1 and 5000 per code"
                )
    elif mode == 1:
        period = _parse_minute_period(call.args.get("period", 1))
        if period not in {1, 3, 5, 10, 15, 30, 60}:
            errors.append(
                "ARG_ERROR: stock.quote minute K period must be one of "
                "1, 3, 5, 10, 15, 30, 60"
            )
        count = call.args.get("count", 100)
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0 or count > 1000:
            errors.append("ARG_ERROR: stock.quote minute K count must be an integer between 1 and 1000")
    elif "period" in call.args or "count" in call.args:
        errors.append(
            "ARG_ERROR: stock.quote period/count are not valid with mode=2 latest quote"
        )
    resolved = resolve_api(call.api)
    source_fields = runtime_field_contract(call, usage="source")
    if (
        resolved
        and resolved.get("type") == "kd"
        and source_fields is not None
        and str(resolved.get("field") or "") not in source_fields
    ):
        errors.append(
            f"API_ERROR: field={resolved.get('field')} is not available in "
            f"stock.quote mode={mode}; available={sorted(source_fields)}"
        )


def _parse_minute_period(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value if value not in (None, "") else 1).strip().lower()
    if text.endswith("m"):
        text = text[:-1]
    try:
        return int(text)
    except ValueError:
        return None


def _stock_quote_effective_mode(args: Mapping[str, object]) -> int | None:
    has_public_mode = "mode" in args
    raw = args.get("mode", args.get("realtime", 0))
    if raw in (None, ""):
        return 0
    if isinstance(raw, bool):
        if has_public_mode:
            return None
        return 2 if raw else 0
    if isinstance(raw, int):
        return raw if raw in {0, 1, 2} else None
    text = str(raw).strip().lower()
    if text in {"0", "false", "no", "history", "historical"}:
        return 0
    if text in {"1", "true", "yes", "minute", "intraday", "minute_kline"}:
        return 1
    if text in {"2", "current", "latest", "realtime", "snapshot"}:
        return 2
    return None


def _stock_quote_exact_date(args: Mapping[str, object]) -> date | None:
    for key in ("tradedate", "trade_date", "date"):
        parsed = _parse_iso_date(args.get(key))
        if parsed is not None:
            return parsed
    filter_text = str(args.get("filter") or "")
    match = EXACT_TRADE_DATE_RE.search(filter_text)
    if not match:
        return None
    return _parse_iso_date(match.group(1))


def _parse_iso_date(value: object) -> date | None:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _validate_refs(call: ApiCall, previous_results: Mapping[str, ResultHandle], errors: list[str]) -> None:
    for key, value in call.args.items():
        if not isinstance(value, str):
            continue
        ref_matches = list(REF_RE.finditer(value))
        if key == "filter":
            for match in ref_matches:
                result_name, column = match.groups()
                ref = f"{result_name}.{column}"
                if not FILTER_REF_PREFIX_RE.search(value[: match.start()]):
                    errors.append(
                        "REF_ERROR: filter result references must be bound as "
                        f"`field in {ref}`, not used as a bare filter"
                    )
        for match in ref_matches:
            result_name, column = match.groups()
            handle = previous_results.get(result_name)
            if not handle:
                errors.append(f"REF_ERROR: unknown result={result_name}")
            elif column not in set(handle.columns):
                errors.append(f"COLUMN_ERROR: {result_name} has no column={column}; available={handle.columns}")


def _validate_outputs(call: ApiCall, resolved: Mapping[str, object], errors: list[str]) -> None:
    if not call.outputs:
        errors.append("OUTPUT_ERROR: request must declare output fields")
        return
    view = resolved["view"]  # type: ignore[index]
    fields = set((view.get("fields") or {}).keys())  # type: ignore[union-attr]
    if resolved["type"] == "agg":
        _validate_agg_outputs(call, fields, errors)
        return
    if resolved["type"] == "dynamic_cal":
        _validate_dynamic_outputs(call, fields, errors)
        return
    runtime_allowed = runtime_field_contract(call, usage="output")
    allowed = set(runtime_allowed) if runtime_allowed is not None else fields
    for output in call.outputs:
        text = str(output or "").strip()
        alias_match = OUTPUT_ALIAS_RE.fullmatch(text)
        expression = alias_match.group("expression").strip() if alias_match else text
        if "(" in expression or ")" in expression:
            errors.append(
                f"OUTPUT_ERROR: function expression={expression} is only valid "
                "for aggregate APIs"
            )
            continue
        if alias_match and resolved["type"] != "kd":
            errors.append(
                f"OUTPUT_ERROR: alias={alias_match.group('alias')} is not supported "
                f"by api={call.api}"
            )
            continue
        token = _output_base_token(output)
        if token and token not in allowed and not _is_allowed_computed_field(token, view):  # type: ignore[arg-type]
            errors.append(f"OUTPUT_ERROR: field={token} not in api={call.api}; available={sorted(allowed)}")


def _output_base_token(output: str) -> str:
    text = str(output or "").strip()
    alias_match = OUTPUT_ALIAS_RE.fullmatch(text)
    if alias_match:
        text = alias_match.group("expression").strip()
    if "(" in text and ")" in text:
        return ""
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _validate_agg_args(
    call: ApiCall,
    previous_results: Mapping[str, ResultHandle],
    view: Mapping[str, object],
    errors: list[str],
    *,
    dataview: str,
) -> None:
    agg_spec = parse_agg_spec(call.args)
    metric = agg_spec.metric
    agg = agg_spec.method
    if not metric or not agg:
        errors.append("ARG_ERROR: agg api requires agg like avg(stock.quote.pct)")
        return
    previous_metric = PREVIOUS_METRIC_RE.fullmatch(metric)
    if previous_metric:
        result_name, column = previous_metric.groups()
        handle = previous_results.get(result_name)
        if not handle:
            errors.append(f"REF_ERROR: unknown result={result_name}")
        else:
            if column not in set(handle.columns):
                errors.append(f"COLUMN_ERROR: {result_name} has no column={column}; available={handle.columns}")
            if not ({"code", "stock_code"} & set(handle.columns)):
                errors.append(f"ARG_ERROR: metric result={result_name} must include code or stock_code for constitution.agg")
        if agg not in AGG_METHODS:
            errors.append(f"API_ERROR: agg={agg} unsupported for metric={metric}")
    else:
        if dataview == "quote" and metric_column(metric) in set((view.get("fields") or {}).keys()):
            source_fields = runtime_field_contract(call, usage="source")
            if source_fields is not None and metric_column(metric) not in source_fields:
                errors.append(
                    f"API_ERROR: metric={metric} is not available for the "
                    f"selected stock.quote mode; available={sorted(source_fields)}"
                )
        elif not _is_supported_source_metric(metric):
            errors.append(f"API_ERROR: metric={metric} unsupported")
        if agg not in AGG_METHODS:
            errors.append(f"API_ERROR: agg={agg} unsupported for metric={metric}")
        aggregate_fields = view.get("aggregate_fields")
        if isinstance(aggregate_fields, Mapping):
            field_name = metric_column(metric)
            methods = aggregate_fields.get(field_name)
            if not isinstance(methods, list) or agg not in set(methods):
                errors.append(
                    f"API_ERROR: agg={agg} unsupported for metric={metric}; "
                    f"available={aggregate_fields}"
                )
    if dataview == "constitution":
        _validate_group_by(call, view, errors)
    elif str(call.args.get("group_by") or "").strip():
        _validate_optional_group_by(call, view, errors, dataview=dataview)


def _is_supported_source_metric(metric: str) -> bool:
    parts = str(metric or "").strip().split(".")
    if len(parts) != 3:
        return False
    subject, dataview, field_name = parts
    resolved = resolve_api(f"{subject}.{dataview}")
    if not resolved or resolved.get("type") != "base":
        return False
    view = resolved.get("view")
    fields = set((view.get("fields") or {}).keys()) if isinstance(view, Mapping) else set()
    return field_name in fields


def _validate_group_by(call: ApiCall, view: Mapping[str, object], errors: list[str]) -> None:
    group_by = str(call.args.get("group_by") or "").strip()
    if not group_by:
        errors.append("ARG_ERROR: constitution.agg requires group_by")
        return
    fields = set((view.get("fields") or {}).keys()) if isinstance(view, Mapping) else set()
    for field_name in _field_list(group_by):
        if field_name not in fields:
            errors.append(f"ARG_ERROR: group_by field={field_name} not in constitution fields; available={sorted(fields)}")


def _validate_optional_group_by(call: ApiCall, view: Mapping[str, object], errors: list[str], *, dataview: str) -> None:
    runtime_fields = runtime_field_contract(call, usage="source")
    if runtime_fields is not None:
        fields = set(runtime_fields)
    elif isinstance(view, Mapping):
        fields = set((view.get("fields") or {}).keys())
    else:
        fields = set()
    for field_name in _field_list(call.args.get("group_by")):
        if field_name not in fields:
            errors.append(f"ARG_ERROR: group_by field={field_name} not in {dataview} fields; available={sorted(fields)}")


def _validate_agg_outputs(call: ApiCall, fields: set[str], errors: list[str]) -> None:
    spec = parse_agg_spec(call.args)
    metric_name = metric_column(spec.metric)
    group_fields = set(_field_list(call.args.get("group_by")))
    emitted_group_fields: set[str] = set()
    aggregate_outputs = 0
    for output in call.outputs:
        text = str(output or "").strip()
        aggregate = re.fullmatch(
            r"([A-Za-z_]\w*)\(([^)]+)\)(?:\s+as\s+([A-Za-z_]\w*))?",
            text,
            flags=re.IGNORECASE,
        )
        if aggregate:
            aggregate_outputs += 1
            output_agg, output_metric, alias = aggregate.groups()
            if output_agg.lower() != spec.method.lower():
                errors.append(f"OUTPUT_ERROR: aggregate output uses {output_agg}, expected {spec.method}")
            if metric_name and metric_column(output_metric.strip()) != metric_name:
                errors.append(f"OUTPUT_ERROR: aggregate output metric={output_metric.strip()} expected {metric_name}")
            if not alias:
                errors.append("OUTPUT_ERROR: aggregate output must use a clear alias")
            continue
        token = _output_base_token(text)
        if token in group_fields:
            emitted_group_fields.add(token)
            continue
        if token and re.fullmatch(r"[A-Za-z_]\w*", token):
            aggregate_outputs += 1
            continue
        errors.append(
            f"OUTPUT_ERROR: aggregate output={text or '<empty>'} must be a "
            "group field, one alias, or an aggregate expression"
        )
    missing_groups = sorted(group_fields - emitted_group_fields)
    if missing_groups:
        errors.append(
            f"OUTPUT_ERROR: agg output is missing group_by fields={missing_groups}"
        )
    if aggregate_outputs != 1:
        errors.append(
            "OUTPUT_ERROR: agg output must declare exactly one aggregate result; "
            f"found={aggregate_outputs}"
        )


def _validate_dynamic_fields(call: ApiCall, view: Mapping[str, object], errors: list[str]) -> None:
    raw_fields = call.args.get("fields")
    if raw_fields in (None, ""):
        return
    available = set((view.get("fields") or {}).keys()) if isinstance(view, Mapping) else set()
    for field_name in _field_list(raw_fields):
        if field_name not in available:
            errors.append(f"ARG_ERROR: dynamic_cal fields contains unknown field={field_name}")


def _field_list(raw_fields: object) -> list[str]:
    if isinstance(raw_fields, (list, tuple, set)):
        return [str(item).strip() for item in raw_fields if str(item).strip()]
    return [item.strip() for item in str(raw_fields or "").split(",") if item.strip()]


def _validate_dynamic_outputs(call: ApiCall, base_fields: set[str], errors: list[str]) -> None:
    allowed_runtime = base_fields | {"value", "k", "end_date", "tradedate", "window_count"}
    for output in call.outputs:
        text = str(output or "").strip()
        if "(" in text or ")" in text:
            errors.append(
                f"OUTPUT_ERROR: function expression={text} is only valid for "
                "aggregate APIs"
            )
            continue
        alias_match = OUTPUT_ALIAS_RE.fullmatch(text)
        if alias_match:
            errors.append(
                f"OUTPUT_ERROR: alias={alias_match.group('alias')} is not "
                f"supported by api={call.api}"
            )
            continue
        token = _output_base_token(output)
        if not token:
            continue
        if token in allowed_runtime:
            continue
        if not re.match(r"^[A-Za-z_]\w*$", token):
            errors.append(f"OUTPUT_ERROR: dynamic_cal output field={token} must be a valid identifier")


def _is_allowed_computed_field(token: str, view: Mapping[str, object]) -> bool:
    computed = view.get("computed") if isinstance(view, Mapping) else None
    if not isinstance(computed, Mapping):
        return False
    suffixes = set(computed.get("suffixes") or [])
    base_fields = set(computed.get("base_fields") or [])
    if "_" not in token:
        return False
    base, suffix = token.rsplit("_", 1)
    return suffix in suffixes and base in base_fields
