from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


@dataclass(frozen=True)
class BaseInfoSource:
    subject: str
    table_sql: str
    source_tables: tuple[str, ...]
    fields: Mapping[str, str]


BASE_INFO_SOURCES: Dict[str, BaseInfoSource] = {
    "stock": BaseInfoSource(
        subject="stock",
        table_sql="""
            kcrp_stock_baseinfo b
            LEFT JOIN kcrp_industry_member im ON im.stk_code = b.stk_code
        """,
        source_tables=("kcrp_stock_baseinfo", "kcrp_industry_member"),
        fields={
            "code": "b.stk_code",
            "name": "b.stk_name",
            "intro": "NULL",
            "industry": "COALESCE(im.level3_industry_name, im.level2_industry_name, im.level1_industry_name)",
            "listed_date": "b.list_date",
        },
    ),
    "index": BaseInfoSource(
        subject="index",
        table_sql="kcrp_index_base b",
        source_tables=("kcrp_index_base",),
        fields={
            "code": "b.idx_code",
            "name": "b.index_short_name",
            "publisher": "NULL",
            "index_type": "NULL",
        },
    ),
    "plate": BaseInfoSource(
        subject="plate",
        table_sql="kcrp_yp_plate b",
        source_tables=("kcrp_yp_plate",),
        fields={
            "plate_code": "b.plate_code",
            "plate_name": "b.plate_name",
        },
    ),
    "fund": BaseInfoSource(
        subject="fund",
        table_sql="kcrp_fund_baseinfo b",
        source_tables=("kcrp_fund_baseinfo",),
        fields={
            "code": "b.fund_code",
            "name": "b.fund_name",
            "full_name": "b.fund_fullname",
        },
    ),
    "bond": BaseInfoSource(
        subject="bond",
        table_sql="kcrp_bond_baseinfo b",
        source_tables=("kcrp_bond_baseinfo",),
        fields={
            "code": "b.bond_code",
            "name": "b.bond_name",
            "issuer": "b.bond_issuer",
        },
    ),
}


OP_SQL = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|=|==|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|=|==|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_base_info_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = BASE_INFO_SOURCES.get(subject)
    if not source:
        return {"status": "unsupported", "subject": subject, "reason": "base_info provider is not configured for this subject", "rows": []}

    columns = _requested_columns(source=source, outputs=outputs)
    if not columns:
        columns = list(source.fields)[:2]
    ignored_filters = _ignored_filters(source=source, args=args)
    where_sql, params = _build_where(source=source, args=args)
    order_sql = _build_order(source=source, args=args)
    limit = _bounded_limit(args.get("limit"))
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    sql = f"""
        SELECT {select_sql}
        FROM {source.table_sql}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """
    params.append(limit)

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        rows = [_normalize_row(row, columns) for row in raw_rows]
        return {
            "status": "ok",
            "api": f"{subject}.basic_info",
            "subject": subject,
            "provider": "kingdomai_base_info",
            "source_tables": list(source.source_tables),
            "arguments": dict(args),
            "columns": columns,
            "rows": rows,
            "ignored_filters": ignored_filters,
            "sql_shape": {"where": where_sql, "order": order_sql, "limit": limit},
        }
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return {
            "status": "provider_error",
            "api": f"{subject}.basic_info",
            "subject": subject,
            "provider": "kingdomai_base_info",
            "source_tables": list(source.source_tables),
            "arguments": dict(args),
            "columns": columns,
            "rows": [],
            "ignored_filters": ignored_filters,
            "reason": str(exc),
        }
    finally:
        db.close_db()


def _requested_columns(*, source: BaseInfoSource, outputs: List[str]) -> List[str]:
    columns: list[str] = []
    for output in outputs:
        field = _output_field(output)
        if field in source.fields and field not in columns:
            columns.append(field)
    return columns


def _output_field(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if "(" in text and ")" in text:
        return ""
    return text


def _build_where(*, source: BaseInfoSource, args: Mapping[str, Any]) -> tuple[str, list[Any]]:
    filter_sql, params = _build_filter_clauses(source=source, args=args)
    return filter_sql or "1=1", params


def _build_filter_clauses(*, source: BaseInfoSource, args: Mapping[str, Any]) -> tuple[str, list[Any]]:
    raw_filter = str(args.get("filter") or "").strip()
    if not raw_filter:
        return "", []
    clauses: list[str] = []
    params: list[Any] = []
    for match in FILTER_RE.finditer(raw_filter):
        field = match.group("field")
        op = match.group("op").lower()
        if field not in source.fields:
            continue
        connector = (match.group("connector") or "").strip().upper()
        if connector and clauses:
            clauses.append(connector)
        elif clauses:
            clauses.append("AND")
        value = _clean_value(match.group("value"))
        if op == "in":
            values = _parse_in_values(value)
            if not values:
                clauses.pop()
                continue
            clauses.append(f"{source.fields[field]} IN ({', '.join(['%s'] * len(values))})")
            params.extend(values)
        else:
            clauses.append(f"{source.fields[field]} {OP_SQL[op]} %s")
            params.append(value)
    return " ".join(clauses), params


def _ignored_filters(*, source: BaseInfoSource, args: Mapping[str, Any]) -> List[str]:
    raw_filter = str(args.get("filter") or "").strip()
    if not raw_filter:
        return []
    ignored: list[str] = []
    for match in FILTER_RE.finditer(raw_filter):
        field = match.group("field")
        if field not in source.fields and field not in ignored:
            ignored.append(field)
    return ignored


def _build_order(*, source: BaseInfoSource, args: Mapping[str, Any]) -> str:
    raw_order = str(args.get("order") or "").strip()
    if raw_order:
        parts = raw_order.split()
        field = parts[0]
        direction = parts[1].upper() if len(parts) > 1 and parts[1].lower() in {"asc", "desc"} else "ASC"
        if field in source.fields:
            return f"{source.fields[field]} {direction}"
    first_field = next(iter(source.fields.values()))
    return f"{first_field} ASC"


def _bounded_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 50
    return max(1, min(limit, 500))


def _clean_value(value: str) -> Any:
    text = str(value or "").strip()
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        return text[1:-1]
    return text


def _parse_in_values(value: Any) -> list[Any]:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return [_clean_value(part.strip()) for part in text.split(",") if part.strip()]


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    result: dict[str, Any] = {}
    for column in columns:
        value = row.get(column)
        if isinstance(value, (datetime, date)):
            result[column] = value.isoformat()
        elif isinstance(value, Decimal):
            result[column] = float(value)
        else:
            result[column] = value
    return result
