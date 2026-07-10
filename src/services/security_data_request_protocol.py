from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping


PROTOCOL_PATH = Path(__file__).resolve().parents[1] / "protocols" / "security_data_request_protocol.json"
REFERENCE_VALUE_PATTERN = re.compile(r"^(r\d+)\.([A-Za-z_][A-Za-z0-9_]*)$")


def _load_protocol_definition(path: Path = PROTOCOL_PATH) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"security data request protocol file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"security data request protocol JSON is invalid: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("security data request protocol must be a JSON object")
    return data


def _required_list(data: Mapping[str, Any], key: str) -> List[str]:
    value = data.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"security data request protocol.{key} must be a list")
    rows = [str(item or "").strip() for item in value]
    return [item for item in rows if item]


def _required_mapping(data: Mapping[str, Any], key: str) -> Dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"security data request protocol.{key} must be an object")
    return dict(value)


def _string_list_mapping(data: Mapping[str, Any], key: str) -> Dict[str, List[str]]:
    value = _required_mapping(data, key)
    rows: Dict[str, List[str]] = {}
    for raw_name, raw_items in value.items():
        name = str(raw_name or "").strip().lower()
        if not name:
            continue
        if not isinstance(raw_items, list):
            raise RuntimeError(f"security data request protocol.{key}.{name} must be a list")
        rows[name] = [str(item or "").strip() for item in raw_items if str(item or "").strip()]
    return rows


def _validate_loaded_protocol() -> None:
    subjects = set(SUBJECTS)
    data_views = set(DATA_VIEWS)
    for subject in subjects:
        if subject not in DEFAULT_DATA_VIEWS_BY_SUBJECT:
            raise RuntimeError(f"security data request protocol missing subject_data_views.{subject}")
    for subject, views in DEFAULT_DATA_VIEWS_BY_SUBJECT.items():
        if subject not in subjects:
            raise RuntimeError(f"security data request protocol has unknown subject: {subject}")
        for data_view in views:
            if data_view not in data_views:
                raise RuntimeError(f"security data request protocol has unknown data_view: {subject}.{data_view}")


_PROTOCOL = _load_protocol_definition()
_REQUEST_CONTRACT = _required_mapping(_PROTOCOL, "request_contract")

PROTOCOL_SCHEMA_VERSION = str(_PROTOCOL.get("schema_version") or "").strip()
SUBJECTS = tuple(_required_list(_PROTOCOL, "subjects"))
DATA_VIEWS = tuple(_required_list(_PROTOCOL, "data_views"))
OPS = tuple(_required_list(_PROTOCOL, "ops"))
SUPPORT_STATUSES = tuple(_required_list(_PROTOCOL, "support_statuses"))
CONDITION_OPERATORS = tuple(_required_list(_PROTOCOL, "condition_operators"))
METRIC_METHODS = tuple(_required_list(_PROTOCOL, "metric_methods"))
SORT_DIRECTIONS = tuple(_required_list(_PROTOCOL, "sort_directions"))
REQUEST_REQUIRED_FIELDS = tuple(_required_list(_REQUEST_CONTRACT, "required_fields"))
REQUEST_LIST_FIELDS = tuple(_required_list(_REQUEST_CONTRACT, "list_fields"))
CONDITION_REQUIRED_FIELDS = tuple(_required_list(_REQUEST_CONTRACT, "condition_required_fields"))
METRIC_REQUIRED_FIELDS = tuple(_required_list(_REQUEST_CONTRACT, "metric_required_fields"))
SORT_REQUIRED_FIELDS = tuple(_required_list(_REQUEST_CONTRACT, "sort_required_fields"))
REFERENCE_VALUE_FORMAT = str(_REQUEST_CONTRACT.get("reference_value_format") or "").strip()
DEFAULT_DATA_VIEWS_BY_SUBJECT = _string_list_mapping(_PROTOCOL, "subject_data_views")
DATA_VIEW_FIELDS: Dict[str, List[str]] = {}

_validate_loaded_protocol()


@dataclass(frozen=True)
class SecurityDataRequest:
    request_id: str
    subject: str
    data_view: str
    op: str
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    fields: List[str] = field(default_factory=list)
    group_by: List[str] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    sort: List[Dict[str, Any]] = field(default_factory=list)
    limit: int | None = None
    depends_on: List[str] = field(default_factory=list)
    support_status: str = ""


def available_data_views(subject: str) -> List[str]:
    return list(DEFAULT_DATA_VIEWS_BY_SUBJECT.get(str(subject or "").strip().lower(), []))


def available_fields(subject: str, data_view: str) -> List[Dict[str, str]]:
    return []


def parse_reference_value(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        return "", ""
    match = REFERENCE_VALUE_PATTERN.match(value.strip())
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def is_reference_value(value: Any) -> bool:
    ref_id, ref_field = parse_reference_value(value)
    return bool(ref_id and ref_field)


def protocol_summary_for_subject(subject: str) -> Dict[str, Any]:
    normalized = str(subject or "").strip().lower()
    data_views = available_data_views(normalized)
    return {
        "subject": normalized,
        "data_views": data_views,
        "field_schema": {data_view: available_fields(normalized, data_view) for data_view in data_views},
        "ops": list(OPS),
    }


def validate_data_request_shape(request: Mapping[str, Any], *, expected_subject: str = "") -> List[str]:
    errors: List[str] = []
    subject = str(request.get("subject") or "").strip().lower()
    data_view = str(request.get("data_view") or "").strip().lower()
    op = str(request.get("op") or "").strip().lower()
    expected = str(expected_subject or "").strip().lower()
    depends_on = request.get("depends_on") if isinstance(request.get("depends_on"), list) else []
    support_status = str(request.get("support_status") or "").strip().lower()
    if expected and subject != expected:
        errors.append("subject_mismatch")
    if subject not in SUBJECTS:
        errors.append("invalid_subject")
    if data_view not in DATA_VIEWS:
        errors.append("invalid_data_view")
    elif subject in SUBJECTS and data_view not in available_data_views(subject):
        errors.append("unsupported_data_view_for_subject")
    if op not in OPS:
        errors.append("invalid_op")
    for list_field in REQUEST_LIST_FIELDS:
        if list_field in request and not isinstance(request.get(list_field), list):
            errors.append(f"{list_field}_not_list")
    if support_status and support_status not in SUPPORT_STATUSES:
        errors.append("invalid_support_status")
    conditions = request.get("conditions") if isinstance(request.get("conditions"), list) else []
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if "value_ref" in condition:
            errors.append("unsupported_value_ref")
        has_value = "value" in condition and condition.get("value") not in (None, "")
        value = condition.get("value")
        ref_request_id, _ref_field = parse_reference_value(value)
        if ref_request_id:
            if ref_request_id not in [str(item or "").strip() for item in depends_on]:
                errors.append("value_reference_missing_dependency")
    if "limit" in request and request.get("limit") not in (None, ""):
        try:
            int(request.get("limit"))
        except Exception:
            errors.append("limit_not_integer")
    return errors


def normalize_compiler_payload(payload: Any, *, expected_subject: str = "") -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    analyze = str(data.get("analyze") or data.get("reason") or "").strip()
    requests = data.get("data_requests") if isinstance(data.get("data_requests"), list) else []
    if not requests and isinstance(data.get("data_process"), list):
        requests = data.get("data_process")
    normalized_requests: List[Dict[str, Any]] = []
    request_errors: List[Dict[str, Any]] = []
    for item in requests:
        if not isinstance(item, Mapping):
            continue
        request_id = str(item.get("request_id") or f"r{len(normalized_requests) + 1}").strip()
        row: Dict[str, Any] = {
            "request_id": request_id,
            "subject": str(item.get("subject") or expected_subject or "").strip().lower(),
            "data_view": str(item.get("data_view") or "").strip().lower(),
            "op": str(item.get("op") or "").strip().lower(),
        }
        for list_field in ("conditions", "fields", "group_by", "metrics", "sort"):
            if isinstance(item.get(list_field), list) and item.get(list_field):
                row[list_field] = item.get(list_field)
        if "limit" in item and item.get("limit") not in (None, ""):
            row["limit"] = item.get("limit")
        depends_on = [
            str(dep or "").strip()
            for dep in (item.get("depends_on") if isinstance(item.get("depends_on"), list) else [])
            if str(dep or "").strip()
        ]
        if depends_on:
            row["depends_on"] = depends_on
        support_status = str(item.get("support_status") or "").strip().lower()
        if support_status:
            row["support_status"] = support_status
        errors = validate_data_request_shape(row, expected_subject=expected_subject)
        if errors:
            request_errors.append({"request": row, "errors": errors})
        normalized_requests.append(row)
    transforms = data.get("transforms") if isinstance(data.get("transforms"), list) else []
    return {
        "subject": str(data.get("subject") or expected_subject or "").strip().lower(),
        "data_requests": normalized_requests,
        "transforms": [item for item in transforms if isinstance(item, Mapping)],
        "missing_capabilities": [
            str(item).strip()
            for item in (data.get("missing_capabilities") if isinstance(data.get("missing_capabilities"), list) else [])
            if str(item).strip()
        ],
        "analyze": analyze,
        "request_errors": request_errors,
    }
