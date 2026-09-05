from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from src.experiments.staged_data_protocol.phase2.catalog import (
    catalog_source,
    normalize_dataview_for_subject,
    resolve_api,
)
from src.experiments.staged_data_protocol.phase2.intraday_quote_provider import FIELD_SQL
from src.experiments.staged_data_protocol.phase2.quote_provider import QUOTE_SOURCES
from src.experiments.staged_data_protocol.phase2.models import ApiCall, ResultHandle


FILTER_ATOM_RE = re.compile(
    r"^(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>not\s+in|in|like|==|!=|>=|<=|=|>|<)\s*"
    r"(?P<value>.+)$",
    flags=re.IGNORECASE | re.DOTALL,
)
RESULT_REF_RE = re.compile(r"^(r\d+)\.([A-Za-z_]\w*)$")
ARG_NAME_RE = re.compile(r"^([A-Za-z_]\w*)")
OUTPUT_ALIAS_RE = re.compile(r"\s+as\s+([A-Za-z_]\w*)\s*$", flags=re.IGNORECASE)
OUTPUT_FIELD_RE = re.compile(r"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)$")
AGG_RE = re.compile(
    r"^(?P<method>[A-Za-z_]\w*)\((?P<metric>[^)]+)\)$",
    flags=re.DOTALL,
)
NOOP_FILTER_RE = re.compile(r"^1\s*=\s*1$")

KD_COMMON_FILTER_FIELDS = {"code", "name", "value", "k", "end_date", "tradedate"}
KD_COMMON_OUTPUT_FIELDS = KD_COMMON_FILTER_FIELDS | {"window_count"}
KD_MARGIN_FILTER_FIELDS = KD_COMMON_FILTER_FIELDS | {
    "start_value",
    "current_value",
    "start_date",
}
KD_MARGIN_OUTPUT_FIELDS = KD_MARGIN_FILTER_FIELDS | {"window_count"}
KD_INTRADAY_FILTER_RESULT_FIELDS = {
    "value",
    "current_value",
    "change_pct",
    "change_ratio",
    "window_count",
}
KD_INTRADAY_OUTPUT_RESULT_FIELDS = KD_INTRADAY_FILTER_RESULT_FIELDS | {
    "end_date",
    "k",
}

# Existing, tested runtime inputs that pre-date or complement the model-facing
# catalog.  They remain accepted so adding structural validation cannot break
# already valid API strings.  This is a compatibility contract, not semantic
# inference.
IDENTITY_ARGUMENTS = {"code", "name", "codes", "names"}
DATE_ARGUMENTS = {
    "date",
    "tradedate",
    "trade_date",
    "start",
    "end",
    "start_date",
    "end_date",
    "as_of",
    "asof",
}
MARKET_ARGUMENTS = {
    "market",
    "market_code",
}

STOCK_CORPORATE_DATAVIEWS = {
    "shareholder",
    "pledge",
    "corporate_action",
    "performance_notice",
    "business_segment",
}

COMPATIBLE_FIELD_ALIASES = {
    "plate": {
        "plate_code": "code",
        "plate_name": "name",
    },
}


class CallStructureError(ValueError):
    pass


def _catalog() -> dict[str, Any]:
    return dict(catalog_source())


def _stock_quote_fields(*, intraday: bool) -> set[str]:
    declared = set(
        catalog_source()["subjects"]["stock"]["quote"]["fields"]
    )
    provider_fields = set(FIELD_SQL) if intraday else set(QUOTE_SOURCES["stock"].fields)
    return provider_fields & declared


def structure_call(call: ApiCall) -> dict[str, Any]:
    """Compile the existing model-authored DSL into a JSON-safe call shape.

    This function is deliberately post-generation.  It does not rewrite the
    request string or infer whether the call matches the user's business goal.
    """

    matched = _match_catalog_function(call.api)
    filter_raw = str(call.args.get("filter") or "").strip()
    order_raw = str(call.args.get("order") or "").strip()
    arguments = {
        str(key): _json_value(value)
        for key, value in call.args.items()
        if key not in {"filter", "order"}
    }
    filter_expression = None
    filter_error = ""
    try:
        filter_expression = parse_filter_expression(filter_raw)
        if matched:
            filter_expression = _canonicalize_filter_fields(
                filter_expression,
                subject=str(matched.get("subject") or ""),
                available_fields=set(matched.get("fields") or set()),
            )
    except CallStructureError as exc:
        filter_error = str(exc)
    order_by: list[dict[str, str]] = []
    order_error = ""
    try:
        order_by = parse_order(order_raw)
    except CallStructureError as exc:
        order_error = str(exc)
    payload = {
        "result_id": call.result_id,
        "api": call.api,
        "api_class": str(matched.get("api_class") or "") if matched else "",
        "subject": str(matched.get("subject") or "") if matched else "",
        "dataview": str(matched.get("dataview") or "") if matched else "",
        "arguments": arguments,
        "filter": {
            "raw": filter_raw,
            "expression": filter_expression,
        },
        "order_by": order_by,
        "outputs": [_structure_output(item) for item in call.outputs],
    }
    if filter_error:
        payload["filter"]["parse_error"] = filter_error
    if order_error:
        payload["order_error"] = order_error
    return payload


def validate_call_structure(
    call: ApiCall,
    previous_results: Mapping[str, ResultHandle] | None = None,
) -> list[str]:
    """Validate only mechanical API/dataview contract consistency."""

    previous_results = previous_results or {}
    errors: list[str] = []
    matched = _match_catalog_function(call.api)
    if not matched:
        # The existing runtime validator remains authoritative for unsupported
        # APIs and aliases.  Do not duplicate a less informed error here.
        return errors

    api_class = matched["api_class_cfg"]
    required, optional = _argument_contract(api_class)
    allowed_args = required | optional | _compatible_arguments(matched)
    if matched.get("api_class") == "constituent_aggregate":
        # The runtime supports both agg=avg(metric) and the older equivalent
        # metric=metric, agg=avg form.
        allowed_args.add("metric")
    for name in call.args:
        if name not in allowed_args:
            errors.append(
                f"ARG_ERROR: argument={name} not declared for api={call.api}; "
                f"available={sorted(allowed_args)}"
            )
    for name in sorted(required):
        if call.args.get(name) in (None, ""):
            errors.append(f"ARG_ERROR: api={call.api} requires argument={name}")

    fields = set(matched["fields"])
    computed_fields = _computed_fields(matched.get("view_cfg"))
    computed_fields.update(_computed_fields(matched.get("runtime_view_cfg")))
    runtime_filter_fields = runtime_field_contract(call, usage="filter")
    filter_fields = (
        set(runtime_filter_fields)
        if runtime_filter_fields is not None
        else fields | computed_fields
    )
    filter_fields.update(_metric_fields(call, previous_results))
    filter_raw = str(call.args.get("filter") or "").strip()
    try:
        expression = parse_filter_expression(filter_raw)
    except CallStructureError as exc:
        errors.append(f"FILTER_ERROR: {exc}")
    else:
        predicates = list(_predicates(expression))
        for predicate in predicates:
            field = str(predicate.get("field") or "")
            operator = str(predicate.get("operator") or "").lower()
            value = predicate.get("value")
            canonical_field = _canonical_field(
                field,
                subject=str(matched.get("subject") or ""),
                available_fields=filter_fields,
            )
            if canonical_field and canonical_field not in filter_fields:
                errors.append(
                    f"FILTER_ERROR: field={field} not in api={call.api}; "
                    f"available={sorted(filter_fields)}"
                )
            if operator == "not in":
                errors.append(
                    "FILTER_ERROR: operator=not in is not supported by finance Providers"
                )
            elif operator == "like" and not _supports_like(call, matched):
                errors.append(
                    f"FILTER_ERROR: operator=like is not supported by api={call.api}"
                )
            if operator in {"in", "not in"} and _looks_like_sql_subquery(value):
                errors.append(
                    "FILTER_ERROR: filter must use a prior rN.column result "
                    "binding, not a SQL subquery"
                )
            if operator in {"in", "not in"} and isinstance(value, list) and not value:
                errors.append(
                    f"FILTER_ERROR: field={field} uses an empty {operator} list"
                )
    output_fields = _output_names(call.outputs)
    runtime_order_fields = runtime_field_contract(call, usage="order")
    if runtime_order_fields is not None:
        order_fields = set(runtime_order_fields)
    elif str(matched.get("resolved_type") or "") == "agg":
        order_fields = set(_field_list(call.args.get("group_by"))) | output_fields
    else:
        order_fields = fields | computed_fields | output_fields
    order_raw = str(call.args.get("order") or "").strip()
    try:
        order_items = parse_order(order_raw)
    except CallStructureError as exc:
        errors.append(f"ORDER_ERROR: {exc}")
    else:
        for item in order_items:
            field = str(item.get("field") or "")
            if field and field not in order_fields:
                errors.append(
                    f"ORDER_ERROR: field={field} not in api={call.api}; "
                    f"available={sorted(order_fields)}"
                )
    return _unique(errors)


def runtime_field_contract(call: ApiCall, *, usage: str) -> set[str] | None:
    """Return the fields the selected execution path actually consumes.

    ``None`` means that the normal dataview contract applies.  A concrete set
    is used only where one public API routes to materially different result
    shapes (stock quote modes and K-day result rows).
    """

    if usage not in {"filter", "order", "output", "source"}:
        raise ValueError(f"unsupported field-contract usage={usage}")
    matched = _match_catalog_function(call.api)
    if not matched:
        return None
    subject = str(matched.get("subject") or "")
    dataview = str(
        matched.get("runtime_dataview") or matched.get("dataview") or ""
    )
    resolved_type = str(matched.get("resolved_type") or "")

    if subject == "stock" and dataview == "quote":
        quote_fields = _stock_quote_fields(
            intraday=_effective_quote_mode(call.args, matched) > 0,
        )
        if usage == "source" or resolved_type == "base":
            return set(quote_fields)
        if resolved_type == "agg":
            return set(quote_fields) if usage == "filter" else None
        if resolved_type == "dynamic_cal":
            return set(quote_fields) if usage == "filter" else None
        if resolved_type == "kd" and _effective_quote_mode(call.args, matched) > 0:
            result_fields = (
                KD_INTRADAY_FILTER_RESULT_FIELDS
                if usage == "filter"
                else KD_INTRADAY_OUTPUT_RESULT_FIELDS
            )
            return set(quote_fields) | result_fields

    if resolved_type != "kd":
        return None
    if dataview == "margin":
        if usage in {"order", "output"}:
            return set(KD_MARGIN_OUTPUT_FIELDS)
        return set(KD_MARGIN_FILTER_FIELDS)
    if dataview == "pricevalue":
        metric = str(matched.get("field") or "")
        common = KD_COMMON_FILTER_FIELDS | {"current_value", metric}
        if usage == "output":
            return common | {"window_count"}
        if usage == "order":
            return KD_COMMON_FILTER_FIELDS | {"current_value"}
        return common
    if dataview in {"quote", "moneyflow"}:
        if usage == "output":
            return set(KD_COMMON_OUTPUT_FIELDS)
        return set(KD_COMMON_FILTER_FIELDS)
    return None


def parse_filter_expression(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if (
        not raw
        or raw.lower() in {"all", "none", "true", "*"}
        or NOOP_FILTER_RE.fullmatch(raw)
    ):
        return None
    return _parse_filter_node(raw)


def parse_order(text: str) -> list[dict[str, str]]:
    raw = str(text or "").strip()
    if not raw or raw.lower() in {"none", "default"}:
        return []
    rows: list[dict[str, str]] = []
    for segment in _split_top_level_delimiter(raw, ","):
        parts = segment.strip().split()
        if not parts or not re.fullmatch(r"[A-Za-z_]\w*", parts[0]):
            raise CallStructureError(f"invalid order expression={segment.strip() or raw}")
        if len(parts) > 2:
            raise CallStructureError(f"invalid order expression={segment.strip()}")
        direction = parts[1].lower() if len(parts) == 2 else "desc"
        if direction not in {"asc", "desc"}:
            raise CallStructureError(
                f"order direction={direction} must be asc or desc"
            )
        rows.append({"field": parts[0], "direction": direction})
    return rows


def _parse_filter_node(text: str) -> dict[str, Any]:
    raw = _strip_balanced_outer_parentheses(text.strip())
    for connective in ("or", "and"):
        parts = _split_top_level_word(raw, connective)
        if len(parts) > 1:
            return {connective: [_parse_filter_node(item) for item in parts]}
    match = FILTER_ATOM_RE.fullmatch(raw)
    if not match:
        raise CallStructureError(f"cannot parse filter expression={raw}")
    field = match.group("field")
    operator = re.sub(r"\s+", " ", match.group("op").lower())
    value_text = match.group("value").strip()
    if not value_text:
        raise CallStructureError(f"filter field={field} has no value")
    return {
        "field": field,
        "operator": operator,
        "value": _parse_filter_value(value_text),
    }


def _parse_filter_value(text: str) -> Any:
    raw = text.strip()
    ref = RESULT_REF_RE.fullmatch(raw)
    if ref:
        return {"result": ref.group(1), "field": ref.group(2)}
    if (
        len(raw) >= 2
        and raw[0] == raw[-1]
        and raw[0] in {"'", '"'}
    ):
        return raw[1:-1]
    if (
        len(raw) >= 2
        and (raw[0], raw[-1]) in {("[", "]"), ("(", ")")}
    ):
        body = raw[1:-1].strip()
        if not body:
            return []
        return [
            _parse_filter_value(item)
            for item in _split_top_level_delimiter(body, ",")
        ]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", raw):
        return float(raw)
    return raw


def _split_top_level_word(text: str, word: str) -> list[str]:
    lowered = text.lower()
    boundaries: list[int] = []
    quote = ""
    round_depth = 0
    square_depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
            if round_depth < 0:
                raise CallStructureError(f"unbalanced filter parentheses={text}")
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
            if square_depth < 0:
                raise CallStructureError(f"unbalanced filter brackets={text}")
        elif round_depth == 0 and square_depth == 0:
            end = index + len(word)
            if (
                lowered[index:end] == word
                and (index == 0 or not _word_char(text[index - 1]))
                and (end == len(text) or not _word_char(text[end]))
            ):
                boundaries.append(index)
                index = end
                continue
        index += 1
    if quote or round_depth or square_depth:
        raise CallStructureError(f"unbalanced filter expression={text}")
    if not boundaries:
        return [text.strip()]
    parts: list[str] = []
    start = 0
    for boundary in boundaries:
        part = text[start:boundary].strip()
        if not part:
            raise CallStructureError(f"empty operand around {word} in filter={text}")
        parts.append(part)
        start = boundary + len(word)
    tail = text[start:].strip()
    if not tail:
        raise CallStructureError(f"empty operand around {word} in filter={text}")
    parts.append(tail)
    return parts


def _split_top_level_delimiter(text: str, delimiter: str) -> list[str]:
    rows: list[str] = []
    quote = ""
    round_depth = 0
    square_depth = 0
    start = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif char == delimiter and round_depth == 0 and square_depth == 0:
            rows.append(text[start:index].strip())
            start = index + 1
        if round_depth < 0 or square_depth < 0:
            raise CallStructureError(f"unbalanced expression={text}")
    if quote or round_depth or square_depth:
        raise CallStructureError(f"unbalanced expression={text}")
    rows.append(text[start:].strip())
    if any(not item for item in rows):
        raise CallStructureError(f"empty item in expression={text}")
    return rows


def _strip_balanced_outer_parentheses(text: str) -> str:
    raw = text
    while raw.startswith("(") and raw.endswith(")") and _outer_pair_wraps(raw):
        raw = raw[1:-1].strip()
    return raw


def _outer_pair_wraps(text: str) -> bool:
    quote = ""
    depth = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return False
            if depth < 0:
                raise CallStructureError(f"unbalanced filter parentheses={text}")
    if quote or depth:
        raise CallStructureError(f"unbalanced filter expression={text}")
    return True


def _match_catalog_function(api: str) -> dict[str, Any] | None:
    catalog = _catalog()
    api_classes = catalog.get("api_class_patterns") or {}
    subjects = catalog.get("subjects") or {}
    candidates: list[dict[str, Any]] = []
    for subject, subject_cfg in subjects.items():
        if not isinstance(subject_cfg, Mapping):
            continue
        for dataview, view_cfg in subject_cfg.items():
            if str(dataview).startswith("_") or not isinstance(view_cfg, Mapping):
                continue
            for function in view_cfg.get("api") or []:
                if not isinstance(function, Mapping):
                    continue
                pattern = str(function.get("api_name") or "").strip()
                if not pattern or not _api_pattern_matches(pattern, api):
                    continue
                class_name = str(function.get("api_class") or "").strip()
                class_cfg = api_classes.get(class_name)
                if not isinstance(class_cfg, Mapping):
                    class_cfg = {}
                candidates.append(
                    {
                        "subject": str(subject),
                        "dataview": str(dataview),
                        "fields": set((view_cfg.get("fields") or {}).keys()),
                        "view_cfg": view_cfg,
                        "function": function,
                        "api_class": class_name,
                        "api_class_cfg": class_cfg,
                        "specificity": len(pattern.replace("<field>", "").replace("<method>", "")),
                    }
                )
    if not candidates:
        return None
    matched = max(candidates, key=lambda item: int(item["specificity"]))
    resolved = resolve_api(api)
    if isinstance(resolved, Mapping):
        runtime_view = resolved.get("view")
        runtime_fields = (
            set((runtime_view.get("fields") or {}).keys())
            if isinstance(runtime_view, Mapping)
            else set()
        )
        matched["fields"] = set(matched.get("fields") or set()) | runtime_fields
        matched["runtime_view_cfg"] = runtime_view if isinstance(runtime_view, Mapping) else {}
        matched["runtime_dataview"] = str(resolved.get("dataview") or "")
        matched["resolved_type"] = str(resolved.get("type") or "")
        matched["field"] = str(resolved.get("field") or "")
        matched["method"] = str(resolved.get("method") or "")
        matched["default_mode"] = resolved.get("default_mode", 0)
    return matched


def _api_pattern_matches(pattern: str, api: str) -> bool:
    return _api_pattern_expression(pattern, api) or _api_pattern_expression(
        _normalize_api_dataview(pattern),
        _normalize_api_dataview(api),
    )


def _api_pattern_expression(pattern: str, api: str) -> bool:
    expression = re.escape(pattern)
    expression = expression.replace(re.escape("<field>"), r"[A-Za-z_]\w*")
    expression = expression.replace(re.escape("<method>"), r"[A-Za-z_]\w*")
    return bool(re.fullmatch(expression, api))


def _normalize_api_dataview(api: str) -> str:
    parts = str(api or "").split(".")
    if len(parts) >= 2:
        parts[1] = normalize_dataview_for_subject(parts[0], parts[1])
    return ".".join(parts)


def _argument_contract(api_class: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    args = api_class.get("args") if isinstance(api_class.get("args"), Mapping) else {}
    required = {_argument_name(item) for item in args.get("required") or []}
    optional = {_argument_name(item) for item in args.get("optional") or []}
    return {item for item in required if item}, {item for item in optional if item}


def _argument_name(value: Any) -> str:
    match = ARG_NAME_RE.match(str(value or "").strip())
    return match.group(1) if match else ""


def _compatible_arguments(matched: Mapping[str, Any]) -> set[str]:
    """Return only compatibility inputs consumed by the selected Provider."""

    subject = str(matched.get("subject") or "")
    dataview = str(
        matched.get("runtime_dataview") or matched.get("dataview") or ""
    )
    resolved_type = str(matched.get("resolved_type") or "")
    if dataview in {"quote", "moneyflow", "margin", "pricevalue", "constitution"}:
        rows = IDENTITY_ARGUMENTS | DATE_ARGUMENTS | MARKET_ARGUMENTS
        if subject == "stock" and dataview == "quote":
            rows |= {"mode", "realtime"}
        return rows
    if dataview == "financial_3_table":
        return IDENTITY_ARGUMENTS | {
            "date",
            "start",
            "end",
            "start_date",
            "end_date",
        }
    if subject == "stock" and dataview in STOCK_CORPORATE_DATAVIEWS:
        return IDENTITY_ARGUMENTS | {
            "date",
            "start",
            "end",
            "start_date",
            "end_date",
        }
    if subject == "hot_event" and resolved_type == "base":
        return {"name"} | DATE_ARGUMENTS
    return set()


def _computed_fields(view: Any) -> set[str]:
    if not isinstance(view, Mapping):
        return set()
    computed = view.get("computed")
    if not isinstance(computed, Mapping):
        return set()
    return {
        f"{base}_{suffix}"
        for base in computed.get("base_fields") or []
        for suffix in computed.get("suffixes") or []
        if str(base).strip() and str(suffix).strip()
    }


def _supports_like(call: ApiCall, matched: Mapping[str, Any]) -> bool:
    subject = str(matched.get("subject") or "")
    dataview = str(
        matched.get("runtime_dataview") or matched.get("dataview") or ""
    )
    resolved_type = str(matched.get("resolved_type") or "")
    if dataview == "base_info" and subject in {"stock", "index", "plate", "fund", "bond"}:
        return True
    if subject == "stock" and (
        dataview in {"report", "report_metric"}
        or dataview in STOCK_CORPORATE_DATAVIEWS
    ):
        return True
    if (
        subject == "stock"
        and dataview == "quote"
        and resolved_type in {"base", "agg", "kd"}
    ):
        return _effective_quote_mode(call.args, matched) > 0
    return False


def _effective_quote_mode(
    args: Mapping[str, Any],
    matched: Mapping[str, Any],
) -> int:
    field = str(matched.get("field") or "")
    if field.startswith("minute_") and "mode" not in args and "realtime" not in args:
        return 1
    if (
        str(matched.get("resolved_type") or "") == "kd"
        and "mode" not in args
        and "realtime" not in args
    ):
        return 0
    raw = args.get("mode", args.get("realtime", matched.get("default_mode", 0)))
    if isinstance(raw, bool):
        return 2 if raw else 0
    if isinstance(raw, (int, float)):
        return max(0, min(int(raw), 2))
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "minute", "intraday", "minute_kline"}:
        return 1
    if text in {"2", "current", "latest", "realtime", "snapshot"}:
        return 2
    return 0


def _field_list(raw_fields: Any) -> list[str]:
    if isinstance(raw_fields, (list, tuple, set)):
        return [str(item).strip() for item in raw_fields if str(item).strip()]
    return [
        item.strip()
        for item in str(raw_fields or "").split(",")
        if item.strip()
    ]


def _looks_like_sql_subquery(value: Any) -> bool:
    candidates = value if isinstance(value, list) else [value]
    return any(
        isinstance(item, str)
        and re.search(r"^\s*select\b.+\bfrom\b", item, flags=re.IGNORECASE | re.DOTALL)
        for item in candidates
    )


def _metric_fields(
    call: ApiCall,
    previous_results: Mapping[str, ResultHandle],
) -> set[str]:
    raw = str(call.args.get("agg") or "").strip()
    match = AGG_RE.fullmatch(raw)
    if not match:
        return set()
    metric = match.group("metric").strip()
    ref = RESULT_REF_RE.fullmatch(metric)
    if ref:
        handle = previous_results.get(ref.group(1))
        return set(handle.columns) if handle else set()
    parts = metric.split(".")
    if len(parts) != 3:
        return set()
    subject, dataview, _field = parts
    catalog = _catalog()
    subject_cfg = (catalog.get("subjects") or {}).get(subject)
    if not isinstance(subject_cfg, Mapping):
        return set()
    view_cfg = subject_cfg.get(dataview)
    if not isinstance(view_cfg, Mapping):
        return set()
    fields = view_cfg.get("fields")
    return set(fields.keys()) if isinstance(fields, Mapping) else set()


def _structure_output(output: str) -> dict[str, str]:
    raw = str(output or "").strip()
    alias_match = OUTPUT_ALIAS_RE.search(raw)
    alias = alias_match.group(1) if alias_match else ""
    expression = raw[: alias_match.start()].strip() if alias_match else raw
    field_match = OUTPUT_FIELD_RE.fullmatch(expression)
    return {
        "expression": expression,
        "field": field_match.group(1) if field_match else "",
        "alias": alias,
    }


def _output_names(outputs: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for output in outputs:
        item = _structure_output(output)
        if item["field"]:
            names.add(item["field"])
        if item["alias"]:
            names.add(item["alias"])
        raw = str(output or "").strip()
        if not item["field"] and not item["alias"] and re.fullmatch(r"[A-Za-z_]\w*", raw):
            names.add(raw)
    return names


def _predicates(expression: Mapping[str, Any] | None) -> Iterable[Mapping[str, Any]]:
    if not expression:
        return []
    if "field" in expression:
        return [expression]
    rows: list[Mapping[str, Any]] = []
    for key in ("and", "or"):
        values = expression.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, Mapping):
                rows.extend(_predicates(item))
    return rows


def _canonicalize_filter_fields(
    expression: Mapping[str, Any] | None,
    *,
    subject: str,
    available_fields: set[str],
) -> dict[str, Any] | None:
    if not expression:
        return None
    if "field" in expression:
        row = dict(expression)
        row["field"] = _canonical_field(
            str(row.get("field") or ""),
            subject=subject,
            available_fields=available_fields,
        )
        return row
    result: dict[str, Any] = {}
    for key in ("and", "or"):
        values = expression.get(key)
        if isinstance(values, list):
            result[key] = [
                _canonicalize_filter_fields(
                    item,
                    subject=subject,
                    available_fields=available_fields,
                )
                for item in values
                if isinstance(item, Mapping)
            ]
    return result


def _canonical_field(
    field: str,
    *,
    subject: str,
    available_fields: set[str],
) -> str:
    if field in available_fields:
        return field
    aliases = COMPATIBLE_FIELD_ALIASES.get(subject, {})
    candidate = aliases.get(field, field)
    return candidate if candidate in available_fields else field


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def _word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
