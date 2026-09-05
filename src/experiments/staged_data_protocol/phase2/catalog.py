from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Dict, Mapping


CATALOG_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "tools"
    / "finance_data"
    / "catalog"
    / "api_view_catalog.json"
)

# These aliases are compatibility inputs accepted by the execution protocol.
# Canonical dataviews, fields, operations, methods, and examples all come from
# api_view_catalog.json; they are not re-declared here.
DATAVIEW_ALIASES = {
    "basic_info": "base_info",
    "qoute": "quote",
    "tech factors": "tech_factors",
    "margin_trade": "margin",
    "holder": "shareholder",
    "holders": "shareholder",
    "shareholders": "shareholder",
    "pledge_ratio": "pledge",
    "profitnotice": "performance_notice",
    "profit_notice": "performance_notice",
    "performance_forecast": "performance_notice",
    "capital_action": "corporate_action",
    "equity_event": "corporate_action",
    "segment": "business_segment",
    "hotness": "state",
    "intraday": "quote",
    "intraday_quote": "quote",
    "intraday_kline": "quote",
    "minute_quote": "quote",
    "minute_qoute": "quote",
    "minute_kline": "quote",
    "realtime_minute_quote": "quote",
    "realtime_minute_qoute": "quote",
}

STOCK_DATAVIEW_ALIASES = {
    "history_quote": "quote",
    "realtime_minute_qoute": "quote",
    "realtime_minute_quote": "quote",
    "intraday_quote": "quote",
}

REALTIME_STOCK_DATAVIEW_NAMES = {
    "realtime_minute_qoute",
    "realtime_minute_quote",
    "intraday",
    "intraday_quote",
    "intraday_kline",
    "minute_quote",
    "minute_qoute",
    "minute_kline",
}

HISTORY_STOCK_DATAVIEW_NAMES = {"history_quote"}
OPERATION_TYPES = frozenset({"query", "aggregate", "window", "compute"})
RUNTIME_TYPES = {
    "query": "base",
    "aggregate": "agg",
    "window": "kd",
    "compute": "dynamic_cal",
}


class FinanceCatalogContractError(ValueError):
    pass


_SOURCE_LOCK = RLock()
_SOURCE_STATE: tuple[int, int, int] | None = None
_SOURCE_PAYLOAD: Mapping[str, Any] | None = None


def _freeze_source(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_source(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_source(item) for item in value)
    return deepcopy(value)


def _thaw_source(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_source(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_source(item) for item in value]
    return deepcopy(value)


def _source_catalog() -> Mapping[str, Any]:
    global _SOURCE_PAYLOAD, _SOURCE_STATE
    with _SOURCE_LOCK:
        stat = CATALOG_PATH.stat()
        state = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if _SOURCE_PAYLOAD is not None and state == _SOURCE_STATE:
            return _SOURCE_PAYLOAD
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        validate_catalog_source(payload)
        _SOURCE_PAYLOAD = _freeze_source(payload)
        _SOURCE_STATE = state
        return _SOURCE_PAYLOAD


def catalog_source() -> Mapping[str, Any]:
    """Return the recursively immutable authoritative source snapshot."""

    return _source_catalog()


def load_catalog_source() -> dict[str, Any]:
    """Return the one authoritative finance API catalog as a safe copy."""

    return _thaw_source(_source_catalog())


def validate_catalog_source(payload: Mapping[str, Any]) -> None:
    """Validate facts required by both model discovery and execution.

    This deliberately checks only the stable cross-module contract. Business
    wording remains SOFT and can evolve without adding runtime validators.
    """

    api_classes = payload.get("api_class_patterns")
    subjects = payload.get("subjects")
    if not isinstance(api_classes, Mapping) or not isinstance(subjects, Mapping):
        raise FinanceCatalogContractError(
            "finance catalog requires api_class_patterns and subjects objects"
        )

    for class_name, class_cfg in api_classes.items():
        if not isinstance(class_cfg, Mapping):
            raise FinanceCatalogContractError(f"api_class={class_name} must be an object")
        if not str(class_cfg.get("call_pattern") or "").strip():
            raise FinanceCatalogContractError(f"api_class={class_name} requires call_pattern")

    patterns: set[str] = set()
    for subject, subject_cfg in subjects.items():
        if not isinstance(subject_cfg, Mapping):
            raise FinanceCatalogContractError(f"subject={subject} must be an object")
        for dataview, view_cfg in subject_cfg.items():
            if str(dataview).startswith("_"):
                continue
            if not isinstance(view_cfg, Mapping):
                raise FinanceCatalogContractError(
                    f"dataview={subject}.{dataview} must be an object"
                )
            if not str(view_cfg.get("desc") or "").strip():
                raise FinanceCatalogContractError(
                    f"dataview={subject}.{dataview} requires desc"
                )
            if not isinstance(view_cfg.get("fields"), Mapping):
                raise FinanceCatalogContractError(
                    f"dataview={subject}.{dataview} requires fields"
                )
            functions = view_cfg.get("api")
            if not isinstance(functions, list) or not functions:
                raise FinanceCatalogContractError(
                    f"dataview={subject}.{dataview} requires at least one api operation"
                )
            operations: set[str] = set()
            for function in functions:
                if not isinstance(function, Mapping):
                    raise FinanceCatalogContractError(
                        f"dataview={subject}.{dataview} contains an invalid api operation"
                    )
                api_name = str(function.get("api_name") or "").strip()
                class_name = str(function.get("api_class") or "").strip()
                if not api_name or api_name in patterns:
                    raise FinanceCatalogContractError(
                        f"api_name={api_name or '(missing)'} must be present and unique"
                    )
                patterns.add(api_name)
                api_parts = api_name.split(".")
                if (
                    len(api_parts) < 2
                    or api_parts[0] != str(subject)
                    or api_parts[1] != str(dataview)
                ):
                    raise FinanceCatalogContractError(
                        f"api={api_name} does not belong to dataview="
                        f"{subject}.{dataview}"
                    )
                operation = operation_for_api_pattern(api_name)
                if operation in operations:
                    raise FinanceCatalogContractError(
                        f"dataview={subject}.{dataview} contains more than one "
                        f"operation={operation}"
                    )
                operations.add(operation)
                class_cfg = api_classes.get(class_name)
                if not isinstance(class_cfg, Mapping):
                    raise FinanceCatalogContractError(
                        f"api={api_name} references unknown api_class={class_name}"
                    )
                declared_examples = function.get("examples")
                if isinstance(declared_examples, list) and len(operation_examples(function, class_cfg)) != len(
                    [item for item in declared_examples if str(item).strip()]
                ):
                    raise FinanceCatalogContractError(
                        f"api={api_name} contains an example for another operation"
                    )


def operation_examples(
    function: Mapping[str, Any],
    api_class: Mapping[str, Any],
) -> list[str]:
    """Resolve only examples that execute the selected concrete API pattern."""

    api_name = str(function.get("api_name") or "").strip()
    declared = function.get("examples")
    candidates = (
        declared
        if isinstance(declared, (list, tuple))
        else api_class.get("examples")
    )
    return [
        str(example).strip()
        for example in (candidates or [])
        if str(example).strip()
        and _api_pattern_matches(api_name, _example_api(str(example)))
    ]


def _example_api(example: str) -> str:
    request = str(example or "").split("\nnote:", 1)[0]
    match = re.search(r"=\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(", request)
    return match.group(1) if match else ""


def _api_pattern_matches(pattern: str, api: str) -> bool:
    if not pattern or not api:
        return False
    return _api_pattern_expression(pattern, api) or _api_pattern_expression(
        _normalize_api_dataview(pattern),
        _normalize_api_dataview(api),
    )


def _api_pattern_expression(pattern: str, api: str) -> bool:
    expression = re.escape(pattern)
    expression = expression.replace(re.escape("<field>"), r"[A-Za-z_]\w*")
    expression = expression.replace(re.escape("<method>"), r"[A-Za-z_]\w*")
    return bool(re.fullmatch(expression, api))


def operation_for_api_pattern(api_name: str) -> str:
    """Derive the stable model-facing operation selector from an API pattern."""

    parts = str(api_name or "").strip().split(".")
    method = parts[2] if len(parts) >= 3 else ""
    if method == "dynamic_cal":
        return "compute"
    if method == "agg":
        return "aggregate"
    if method.startswith("kd_"):
        return "window"
    return "query"


def _normalize_api_dataview(api: str) -> str:
    parts = str(api or "").split(".")
    if len(parts) >= 2:
        parts[1] = normalize_dataview_for_subject(parts[0], parts[1])
    return ".".join(parts)


def normalize_dataview(dataview: str) -> str:
    value = str(dataview or "").strip()
    return DATAVIEW_ALIASES.get(value, value)


def normalize_dataview_for_subject(subject: str, dataview: str) -> str:
    value = normalize_dataview(dataview)
    if str(subject or "").strip() == "stock":
        return STOCK_DATAVIEW_ALIASES.get(value, value)
    return value


def default_mode_for_stock_dataview(dataview: str) -> int:
    value = str(dataview or "").strip()
    normalized = DATAVIEW_ALIASES.get(value, value)
    if value in HISTORY_STOCK_DATAVIEW_NAMES or normalized in HISTORY_STOCK_DATAVIEW_NAMES:
        return 0
    if value in REALTIME_STOCK_DATAVIEW_NAMES:
        return 1
    return 0


def _source_dataview(
    subject: str,
    dataview: str,
    *,
    source_catalog: Mapping[str, Any] | None = None,
) -> tuple[str, Mapping[str, Any]] | None:
    source = source_catalog or _source_catalog()
    subject_cfg = (source.get("subjects") or {}).get(subject)
    if not isinstance(subject_cfg, Mapping):
        return None
    normalized = normalize_dataview_for_subject(subject, dataview)
    candidates = [normalized]
    if normalized == "base_info":
        candidates.append("basic_info")
    for candidate in candidates:
        view = subject_cfg.get(candidate)
        if isinstance(view, Mapping):
            return normalized, view
    return None


def _runtime_fields(fields: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(fields, Mapping):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for name, cfg in fields.items():
        if isinstance(cfg, Mapping):
            row = _thaw_source(cfg)
            aliases = row.get("aliases")
            row["aliases"] = (
                list(aliases) if isinstance(aliases, (list, tuple)) else []
            )
        else:
            row = {
                "aliases": list(cfg) if isinstance(cfg, (list, tuple)) else []
            }
        rows[str(name)] = row
    return rows


def _runtime_kd(
    view: Mapping[str, Any],
    api_classes: Mapping[str, Any],
) -> Any:
    kd = view.get("kd")
    if not isinstance(kd, (list, tuple)):
        return _thaw_source(kd or {})
    methods: list[str] = []
    for function in view.get("api") or []:
        if not isinstance(function, Mapping):
            continue
        class_cfg = api_classes.get(str(function.get("api_class") or ""))
        api_name = str(function.get("api_name") or "")
        if isinstance(class_cfg, Mapping) and operation_for_api_pattern(api_name) == "window":
            if "<method>" in api_name:
                methods = [str(item) for item in class_cfg.get("methods") or []]
            else:
                method_match = re.search(r"\.kd_<field>_([A-Za-z_]\w*)$", api_name)
                methods = [method_match.group(1)] if method_match else []
            break
    return {str(field): list(methods) for field in kd}


def _runtime_dataview(
    source_view: Mapping[str, Any],
    api_classes: Mapping[str, Any],
) -> Dict[str, Any]:
    view = _thaw_source(source_view)
    view["fields"] = _runtime_fields(source_view.get("fields"))
    view["kd"] = _runtime_kd(source_view, api_classes)
    return view


def get_dataview(subject: str, dataview: str) -> Dict[str, Any] | None:
    subject_name = str(subject or "").strip()
    source_catalog = _source_catalog()
    resolved = _source_dataview(
        subject_name,
        dataview,
        source_catalog=source_catalog,
    )
    if resolved is None:
        return None
    _, source_view = resolved
    api_classes = source_catalog.get("api_class_patterns") or {}
    return _runtime_dataview(source_view, api_classes)


def has_api(api: str) -> bool:
    return resolve_api(api) is not None


def resolve_api(api: str) -> Dict[str, Any] | None:
    parts = str(api or "").strip().split(".")
    if len(parts) < 2:
        return None
    subject, raw_dataview = parts[0], parts[1]
    source_catalog = _source_catalog()
    source = _source_dataview(
        subject,
        raw_dataview,
        source_catalog=source_catalog,
    )
    if source is None:
        return None
    dataview, source_view = source
    api_classes = source_catalog.get("api_class_patterns") or {}
    matches: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    for function in source_view.get("api") or []:
        if not isinstance(function, Mapping):
            continue
        pattern = str(function.get("api_name") or "").strip()
        if not _api_pattern_matches(pattern, api):
            continue
        class_cfg = api_classes.get(str(function.get("api_class") or ""))
        if isinstance(class_cfg, Mapping):
            specificity = len(pattern.replace("<field>", "").replace("<method>", ""))
            matches.append((specificity, function, class_cfg))
    if not matches:
        return None
    _, function, class_cfg = max(matches, key=lambda item: item[0])
    operation = operation_for_api_pattern(str(function.get("api_name") or ""))
    resolved: Dict[str, Any] = {
        "type": RUNTIME_TYPES[operation],
        "operation": operation,
        "subject": subject,
        "dataview": dataview,
        "view": _runtime_dataview(source_view, api_classes),
        "function": _thaw_source(function),
        "api_class": str(function.get("api_class") or ""),
        "default_mode": (
            default_mode_for_stock_dataview(raw_dataview)
            if subject == "stock" and dataview == "quote"
            else 0
        ),
    }
    if operation == "window":
        metric = parts[2][3:] if len(parts) >= 3 and parts[2].startswith("kd_") else ""
        if metric.endswith("_pct_change"):
            field_name, method = metric[: -len("_pct_change")], "pct_change"
        else:
            field_name, method = metric.rsplit("_", 1) if "_" in metric else ("", "")
        resolved.update({"field": field_name, "method": method})
    return resolved


def _compile_runtime_catalog() -> dict[str, Any]:
    source_catalog = _source_catalog()
    api_classes = source_catalog.get("api_class_patterns") or {}
    subjects: dict[str, Any] = {}
    for subject, subject_cfg in (source_catalog.get("subjects") or {}).items():
        if not isinstance(subject_cfg, Mapping):
            continue
        dataviews: dict[str, Any] = {}
        for dataview, view in subject_cfg.items():
            if str(dataview).startswith("_") or not isinstance(view, Mapping):
                continue
            runtime_name = normalize_dataview_for_subject(str(subject), str(dataview))
            dataviews[runtime_name] = _runtime_dataview(view, api_classes)
        subjects[str(subject)] = {"dataviews": dataviews}
    return {
        "version": str(source_catalog.get("version") or ""),
        "subjects": subjects,
    }


def catalog_summary() -> Dict[str, Any]:
    return _compile_runtime_catalog()
