from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.utils.mysql_utils import MySQLUtils


@dataclass(frozen=True)
class HotEventSource:
    dataview: str
    table: str
    fields: Mapping[str, str]
    default_columns: List[str]
    default_order: str


OP_SQL = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|==|=|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|==|=|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


BASE_FIELDS = {
    "event_id": "event_id",
    "event_code": "event_id",
    "event": "event",
    "event_name": "event",
    "first_trigger_date": "first_trigger_date",
    "latest_trigger_date": "latest_trigger_date",
    "latest_active_id": "latest_active_id",
    "is_active": "is_active",
    "loop_times": "loop_times",
    "max_exist_days": "max_exist_days",
    "slience_days": "slience_days",
    "company_num": "company_num",
    "current_company_num": "current_company_num",
    "core_event_desc": "core_event_desc",
    "latest_event_desc": "latest_event_desc",
    "merge_rule_version": "merge_rule_version",
    "update_time": "update_time",
}

STATE_FIELDS = {
    "event_id": "event_id",
    "event_code": "event_id",
    "active_id": "active_id",
    "event": "event",
    "event_name": "event",
    "trade_date": "trade_date",
    "tradedate": "trade_date",
    "state_time": "state_time",
    "minute": "minute",
    "heat_score": "heat_score",
    "hotness": "heat_score",
    "heat_level": "heat_level",
    "change_label": "change_label",
    "company_num": "company_num",
    "company_num_change": "company_num_change",
    "avg_lift": "avg_lift",
    "avg_lift_change": "avg_lift_change",
    "limited_count": "limited_count",
    "board_strength": "board_strength",
    "source": "source",
    "source_concept": "source_concept",
    "source_point_no": "source_point_no",
    "event_desc": "event_desc",
    "update_time": "update_time",
}

MEMBER_FIELDS = {
    "event_id": "event_id",
    "event_code": "event_id",
    "active_id": "active_id",
    "event": "event",
    "event_name": "event",
    "trade_date": "trade_date",
    "tradedate": "trade_date",
    "stock_code": "stock_code",
    "stock_name": "stock_name",
    "company_event": "company_event",
    "source": "source",
    "source_concept": "source_concept",
    "source_point_no": "source_point_no",
    "relation_type": "relation_type",
    "is_leader": "is_leader",
    "update_time": "update_time",
}

SOURCES = {
    "base_info": HotEventSource(
        dataview="base_info",
        table="hot_event_base_info",
        fields=BASE_FIELDS,
        default_columns=[
            "event_id",
            "event",
            "latest_trigger_date",
            "is_active",
            "loop_times",
            "max_exist_days",
            "company_num",
            "current_company_num",
            "latest_event_desc",
        ],
        default_order="latest_trigger_date DESC, company_num DESC",
    ),
    "state": HotEventSource(
        dataview="state",
        table="hot_event_state",
        fields=STATE_FIELDS,
        default_columns=[
            "event_id",
            "active_id",
            "event",
            "trade_date",
            "state_time",
            "heat_score",
            "company_num",
            "avg_lift",
            "board_strength",
        ],
        default_order="trade_date DESC, state_time DESC, heat_score DESC",
    ),
    "member": HotEventSource(
        dataview="member",
        table="hot_event_member",
        fields=MEMBER_FIELDS,
        default_columns=[
            "event_id",
            "active_id",
            "event",
            "trade_date",
            "stock_code",
            "stock_name",
            "relation_type",
            "company_event",
        ],
        default_order="trade_date DESC, event ASC, stock_code ASC",
    ),
}


def execute_hot_event_api(*, dataview: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    normalized_view = "state" if dataview == "hotness" else dataview
    source = SOURCES.get(normalized_view)
    if not source:
        return _standard_result(
            status="unsupported",
            source=SOURCES["base_info"],
            args=args,
            columns=[],
            rows=[],
            reason=f"unsupported hot_event dataview: {dataview}",
        )
    columns = _requested_columns(source, outputs)
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            reason="filter contains result references that must be materialized by the execution engine",
        )

    limit = _bounded_limit(args.get("limit"))
    where_sql, params = _build_where(source=source, args=args)
    order_sql = _build_order(source=source, args=args)
    sql = _build_sql(source=source, columns=columns, where_sql=where_sql, order_sql=order_sql)
    params.append(limit)

    db = MySQLUtils()
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        rows = [_normalize_row(row, columns) for row in raw_rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            sql_shape={"where": where_sql, "order": order_sql, "limit": limit},
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(status="provider_error", source=source, args=args, columns=columns, rows=[], reason=str(exc))
    finally:
        db.close_db()


def _requested_columns(source: HotEventSource, outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        token = _output_token(output)
        if token in source.fields and token not in columns:
            columns.append(token)
    return columns or list(source.default_columns)


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _has_unresolved_ref(args: Mapping[str, Any]) -> bool:
    return any(isinstance(value, str) and re.search(r"\br\d+\.", value) for value in args.values())


def _bounded_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 100
    if parsed <= 0:
        parsed = 100
    return max(1, min(parsed, 500))


def _build_where(*, source: HotEventSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []

    exact_date = str(args.get("date") or args.get("trade_date") or args.get("tradedate") or "").strip()
    start = str(args.get("start") or args.get("start_date") or "").strip()
    end = str(args.get("end") or args.get("end_date") or "").strip()
    date_field = source.fields.get("trade_date") or source.fields.get("latest_trigger_date")
    if exact_date and date_field:
        clauses.append(f"{date_field} = %s")
        params.append(exact_date)
    elif start and end and date_field:
        clauses.append(f"{date_field} BETWEEN %s AND %s")
        params.extend(sorted([start, end]))

    filter_sql, filter_params = _build_filter_clauses(source=source, args=args, allowed=set(source.fields))
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return (" AND ".join(clauses) if clauses else "1=1"), params


def _build_filter_clauses(*, source: HotEventSource, args: Mapping[str, Any], allowed: set[str]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for connector, field_name, op, value in _explicit_filters(args):
        if field_name not in allowed:
            continue
        expression = source.fields.get(field_name)
        if not expression:
            continue
        prefix = connector if clauses else ""
        if op == "in":
            values = _list_value(value)
            if not values:
                continue
            placeholders = ", ".join(["%s"] * len(values))
            clauses.append(f"{prefix} {expression} IN ({placeholders})".strip())
            params.extend(values)
            continue
        if op == "like":
            clauses.append(f"{prefix} {expression} LIKE %s".strip())
            params.append(f"%{value}%")
            continue
        if op not in OP_SQL:
            continue
        clauses.append(f"{prefix} {expression} {OP_SQL[op]} %s".strip())
        params.append(value)
    if not clauses:
        return "", []
    return f"({' '.join(clauses)})", params


def _explicit_filters(args: Mapping[str, Any]) -> List[tuple[str, str, str, Any]]:
    rows: List[tuple[str, str, str, Any]] = []
    direct_aliases = {
        "event_id": "event_id",
        "event_code": "event_id",
        "active_id": "active_id",
        "stock_code": "stock_code",
        "stock_name": "stock_name",
        "relation_type": "relation_type",
        "is_active": "is_active",
    }
    for arg_name, field_name in direct_aliases.items():
        value = args.get(arg_name)
        if value not in (None, ""):
            rows.append(("AND", field_name, "=", value))
    for arg_name, field_name in {"event": "event", "event_name": "event", "name": "event"}.items():
        value = args.get(arg_name)
        if value not in (None, ""):
            rows.append(("AND", field_name, "like", value))
    for arg_name, field_name in {"event_ids": "event_id", "stock_codes": "stock_code"}.items():
        value = args.get(arg_name)
        if value not in (None, ""):
            rows.append(("AND", field_name, "in", value))
    filter_text = str(args.get("filter") or "").strip()
    for match in FILTER_RE.finditer(filter_text):
        rows.append(
            (
                str(match.group("connector") or "AND").upper(),
                str(match.group("field") or "").strip(),
                str(match.group("op") or "").strip().lower(),
                _clean_value(str(match.group("value") or "")),
            )
        )
    return rows


def _clean_value(value: str) -> Any:
    text = str(value or "").strip().strip("\"'")
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _list_value(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    text = str(value or "").strip()
    if not text:
        return []
    if (text.startswith("[") and text.endswith("]")) or (text.startswith("(") and text.endswith(")")):
        text = text[1:-1]
    return [_clean_value(item) for item in text.split(",") if str(item).strip()]


def _build_order(*, source: HotEventSource, args: Mapping[str, Any]) -> str:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return source.default_order
    order_parts: List[str] = []
    for item in text.split(","):
        parts = re.split(r"\s+", item.strip(), maxsplit=1)
        if not parts or not parts[0]:
            continue
        field_name = parts[0].strip()
        expression = source.fields.get(field_name)
        if not expression:
            continue
        direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
        direction_sql = "ASC" if direction.startswith("asc") else "DESC"
        order_parts.append(f"{expression} {direction_sql}")
    return ", ".join(order_parts) if order_parts else source.default_order


def _build_sql(*, source: HotEventSource, columns: List[str], where_sql: str, order_sql: str) -> str:
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    return f"""
        SELECT DISTINCT {select_sql}
        FROM {source.table}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: _normalize_value(row.get(column)) for column in columns}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _standard_result(
    *,
    status: str,
    source: HotEventSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    reason: str = "",
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": f"hot_event.{source.dataview}",
        "subject": "hot_event",
        "source": [source.table],
        "arguments": dict(args),
        "columns": columns,
        "available_fields": list(source.fields),
        "rows": rows,
        "row_count": len(rows),
    }
    if reason:
        result["reason"] = reason
    if sql_shape:
        result["sql_shape"] = dict(sql_shape)
    return result
