from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.experiments.staged_data_protocol.phase2.agg_protocol import (
    AGG_METHODS,
    output_alias,
    parse_agg_spec,
)
from src.experiments.staged_data_protocol.phase2.call_structure import (
    CallStructureError,
    parse_filter_expression,
    parse_order,
)
from src.utils.mysql_utils import StockInfoDbUtils


REPORT_TABLE = "reports"
METRIC_FACT_TABLE = "metric_fact"
METRIC_DEF_TABLE = "metric_def"
DEFAULT_REPORT_LIMIT = 100
DEFAULT_REPORT_HARD_ROW_LIMIT = 100_000

METRIC_CODE_NAMES = {
    "eps": "每股收益",
    "np_parent": "归母净利润",
    "np_parent_growth": "归母净利润增长率",
    "pb": "市净率",
    "pe": "市盈率",
    "revenue": "营业收入",
    "revenue_growth": "营业收入增长率",
    "roe": "净资产收益率",
}
METRIC_CODE_ALIASES = {
    **{code: code for code in METRIC_CODE_NAMES},
    **{name: code for code, name in METRIC_CODE_NAMES.items()},
    "EPS": "eps",
    "PE": "pe",
    "PB": "pb",
    "ROE": "roe",
}


def _numeric_text(expression: str) -> str:
    """Return a numeric value only when the extracted text is wholly numeric."""

    return (
        f"CASE WHEN TRIM({expression}) REGEXP "
        "'^-?[0-9]+([.][0-9]+)?$' "
        f"THEN CAST(TRIM({expression}) AS DECIMAL(24, 8)) ELSE NULL END"
    )


REPORT_FIELDS: Dict[str, str] = {
    "report_id": "r.id",
    "code": "r.security_code",
    "name": "r.security_name",
    "market": "r.market",
    "title": "COALESCE(NULLIF(r.title, ''), r.file_title)",
    "report_date": "r.publish_at",
    "publisher": "r.publisher",
    "institution": "COALESCE(NULLIF(r.research_institution, ''), r.publisher)",
    "analyst": "JSON_UNQUOTE(JSON_EXTRACT(r.report_authors, '$[0]'))",
    "analysts": "r.report_authors",
    "industry": "r.industry_name",
    "rating": "r.stock_rating",
    "rating_change": "r.rating_change",
    "change_reason": "r.change_reason",
    "investment_highlights": "r.investment_highlights",
    "risk_warnings": "r.risk_warnings",
    "target_price_lower": _numeric_text("r.target_price_lower"),
    "target_price_upper": _numeric_text("r.target_price_upper"),
    "target_price_lower_raw": "r.target_price_lower",
    "target_price_upper_raw": "r.target_price_upper",
    "target_price_horizon": "r.target_price_horizon",
    "target_price_horizon_start_date": "r.target_price_horizon_start_date",
    "target_price_horizon_end_date": "r.target_price_horizon_end_date",
    "source_system": "r.source_system",
    "source_doc_id": "r.source_doc_id",
    "file_hash": "r.file_hash",
    "file_name": "r.file_name",
    "created_at": "r.created_at",
    "updated_at": "r.updated_at",
}

METRIC_FIELDS: Dict[str, str] = {
    "report_id": "m.report_id",
    "company_id": "m.company_id",
    "code": "r.security_code",
    "name": "r.security_name",
    "market": "r.market",
    "title": "COALESCE(NULLIF(r.title, ''), r.file_title)",
    "report_date": "r.publish_at",
    "institution": "COALESCE(NULLIF(r.research_institution, ''), r.publisher)",
    "analyst": "JSON_UNQUOTE(JSON_EXTRACT(r.report_authors, '$[0]'))",
    "analysts": "r.report_authors",
    "rating": "r.stock_rating",
    "metric_code": "m.metric_id",
    "metric_name": "COALESCE(NULLIF(d.metric_name, ''), m.metric_name_snapshot)",
    "forecast_year": "m.year",
    "value_type": "m.value_type",
    "metric_value": "m.value_num",
    "metric_value_raw": "m.value_raw",
    "unit_code": "m.unit_code",
    "unit": "COALESCE(NULLIF(d.unit_name, ''), m.unit_name_snapshot)",
    "unit_raw": "m.unit_raw",
    "source_locator": "m.source_locator",
    "reliable": "m.reliable",
    "conflict_detail": "m.conflict_detail",
    "created_at": "m.created_at",
}

REPORT_AGGREGATE_FIELDS: Dict[str, List[str]] = {
    "report_id": ["count"],
    "report_date": ["min", "max", "count"],
    "institution": ["count"],
    "rating": ["count"],
    "target_price_lower": ["sum", "avg", "min", "max", "median", "count"],
    "target_price_upper": ["sum", "avg", "min", "max", "median", "count"],
}

METRIC_AGGREGATE_FIELDS: Dict[str, List[str]] = {
    "report_id": ["count"],
    "report_date": ["min", "max", "count"],
    "forecast_year": ["min", "max", "count"],
    "metric_value": ["sum", "avg", "min", "max", "median", "count"],
}

FIELD_ALIASES = {
    "stock_code": "code",
    "security_code": "code",
    "stock_name": "name",
    "security_name": "name",
    "publish_date": "report_date",
    "publish_time": "report_date",
    "report_period": "report_date",
    "metric": "metric_code",
    "value": "metric_value",
    "target_low": "target_price_lower",
    "target_high": "target_price_upper",
}

OP_SQL = {
    "=": "=",
    "==": "=",
    "!=": "!=",
    ">": ">",
    ">=": ">=",
    "<": "<",
    "<=": "<=",
}

# These words describe an organisation's legal/research form rather than its
# identifying name.  Keep financially meaningful words (证券、国际、银行……)
# intact so fuzzy institution matching cannot silently cross institutions.
INSTITUTION_FORM_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "研究所",
    "研究院",
    "有限公司",
    "公司",
    "集团",
)


@dataclass(frozen=True)
class ReportSource:
    dataview: str
    fields: Mapping[str, str]
    aggregate_fields: Mapping[str, List[str]]
    from_sql: str
    tables: List[str]
    default_columns: List[str]
    default_order: str

    @property
    def api(self) -> str:
        return f"stock.{self.dataview}"


REPORT_SOURCE = ReportSource(
    dataview="report",
    fields=REPORT_FIELDS,
    aggregate_fields=REPORT_AGGREGATE_FIELDS,
    from_sql=f"`{REPORT_TABLE}` r",
    tables=[REPORT_TABLE],
    default_columns=["report_id", "code", "name", "report_date", "institution", "rating"],
    default_order="r.publish_at DESC, r.id DESC",
)

METRIC_SOURCE = ReportSource(
    dataview="report_metric",
    fields=METRIC_FIELDS,
    aggregate_fields=METRIC_AGGREGATE_FIELDS,
    from_sql=(
        f"`{METRIC_FACT_TABLE}` m "
        f"INNER JOIN `{REPORT_TABLE}` r ON r.id = m.report_id "
        f"LEFT JOIN `{METRIC_DEF_TABLE}` d ON d.metric_id = m.metric_id"
    ),
    tables=[METRIC_FACT_TABLE, REPORT_TABLE, METRIC_DEF_TABLE],
    default_columns=[
        "report_id",
        "code",
        "name",
        "report_date",
        "institution",
        "metric_code",
        "forecast_year",
        "value_type",
        "metric_value",
        "unit",
    ],
    default_order="r.publish_at DESC, m.report_id DESC, m.metric_id ASC, m.year ASC",
)

REPORT_SOURCES = {
    REPORT_SOURCE.dataview: REPORT_SOURCE,
    METRIC_SOURCE.dataview: METRIC_SOURCE,
}


def execute_report_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    return _execute_base_query(source=REPORT_SOURCE, args=args, outputs=outputs)


def execute_report_metric_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    return _execute_base_query(source=METRIC_SOURCE, args=args, outputs=outputs)


def execute_report_agg_api(
    *,
    dataview: str,
    args: Mapping[str, Any],
    outputs: List[str],
) -> Dict[str, Any]:
    source = REPORT_SOURCES.get(str(dataview or "").strip())
    if not source:
        return {
            "status": "unsupported",
            "api": f"stock.{dataview}.agg",
            "subject": "stock",
            "dataview": dataview,
            "arguments": dict(args),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "reason": "report aggregate provider supports report and report_metric only",
        }
    return _execute_aggregate_query(source=source, args=args, outputs=outputs)


def _execute_base_query(
    *,
    source: ReportSource,
    args: Mapping[str, Any],
    outputs: List[str],
) -> Dict[str, Any]:
    try:
        columns = _requested_fields(source=source, outputs=outputs)
        where_sql, params = _build_filter(source=source, args=args)
        order_sql = _build_order(source=source, args=args)
        limit_policy = _limit_policy(args.get("limit"))
    except (CallStructureError, ValueError) as exc:
        return _result(
            status="unsupported",
            source=source,
            args=args,
            columns=[],
            rows=[],
            reason=str(exc),
        )
    if limit_policy["rejected"]:
        return _result(
            status="result_too_large",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            reason=str(limit_policy["reason"]),
        )

    select_sql = ", ".join(f"{source.fields[field]} AS `{field}`" for field in columns)
    sql = (
        f"SELECT {select_sql} FROM {source.from_sql} "
        f"WHERE {where_sql} ORDER BY {order_sql} LIMIT %s"
    )
    params.append(int(limit_policy["fetch_limit"]))
    raw_result = _run_query(sql=sql, params=params)
    if raw_result["error"]:
        return _result(
            status="provider_error",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            reason=f"report database query failed: {raw_result['error']}",
        )
    raw_rows = raw_result["rows"]
    if limit_policy["detect_overflow"] and len(raw_rows) > int(limit_policy["hard_limit"]):
        return _overflow_result(source=source, args=args, columns=columns, hard_limit=int(limit_policy["hard_limit"]))
    rows = [_normalize_row(row, columns) for row in raw_rows]
    return _result(
        status="ok",
        source=source,
        args=args,
        columns=columns,
        rows=rows,
        sql_shape={
            "tables": source.tables,
            "where": where_sql,
            "order": order_sql,
            "limit": -1 if limit_policy["explicit_full"] else int(limit_policy["fetch_limit"]),
            "hard_row_limit": int(limit_policy["hard_limit"]),
        },
    )


def _execute_aggregate_query(
    *,
    source: ReportSource,
    args: Mapping[str, Any],
    outputs: List[str],
) -> Dict[str, Any]:
    spec = parse_agg_spec(args)
    metric = FIELD_ALIASES.get(spec.metric_column, spec.metric_column)
    method = spec.method
    allowed_methods = set(source.aggregate_fields.get(metric) or [])
    if method not in AGG_METHODS or method not in allowed_methods:
        available = {
            field: list(methods)
            for field, methods in source.aggregate_fields.items()
        }
        return _result(
            status="unsupported",
            source=source,
            args=args,
            columns=[],
            rows=[],
            reason=(
                f"aggregate {method or '<missing>'}({metric or '<missing>'}) is not supported; "
                f"available={available}"
            ),
            api_suffix=".agg",
        )
    try:
        group_fields = _group_fields(source=source, raw=args.get("group_by"))
        alias = _aggregate_alias(outputs=outputs, default=f"{method}_{metric}", group_fields=group_fields)
        where_sql, params = _build_filter(source=source, args=args)
        order_sql = _build_aggregate_order(
            source=source,
            raw=args.get("order"),
            group_fields=group_fields,
            alias=alias,
        )
        limit_policy = _limit_policy(args.get("limit"))
    except (CallStructureError, ValueError) as exc:
        return _result(
            status="unsupported",
            source=source,
            args=args,
            columns=[],
            rows=[],
            reason=str(exc),
            api_suffix=".agg",
        )
    columns = [*group_fields, alias]
    if limit_policy["rejected"]:
        return _result(
            status="result_too_large",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            reason=str(limit_policy["reason"]),
            api_suffix=".agg",
        )

    if method == "median":
        sql = _median_sql(
            source=source,
            metric=metric,
            group_fields=group_fields,
            alias=alias,
            where_sql=where_sql,
            order_sql=order_sql,
        )
    else:
        select_parts = [f"{source.fields[field]} AS `{field}`" for field in group_fields]
        select_parts.append(f"{method.upper()}({source.fields[metric]}) AS `{alias}`")
        group_sql = (
            " GROUP BY " + ", ".join(source.fields[field] for field in group_fields)
            if group_fields
            else ""
        )
        sql = (
            f"SELECT {', '.join(select_parts)} FROM {source.from_sql} "
            f"WHERE {where_sql}{group_sql} ORDER BY {order_sql} LIMIT %s"
        )
    params.append(int(limit_policy["fetch_limit"]))
    raw_result = _run_query(sql=sql, params=params)
    if raw_result["error"]:
        return _result(
            status="provider_error",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            reason=f"report aggregate query failed: {raw_result['error']}",
            api_suffix=".agg",
        )
    raw_rows = raw_result["rows"]
    if limit_policy["detect_overflow"] and len(raw_rows) > int(limit_policy["hard_limit"]):
        return _overflow_result(
            source=source,
            args=args,
            columns=columns,
            hard_limit=int(limit_policy["hard_limit"]),
            api_suffix=".agg",
        )
    rows = [_normalize_row(row, columns) for row in raw_rows]
    return _result(
        status="ok",
        source=source,
        args=args,
        columns=columns,
        rows=rows,
        api_suffix=".agg",
        sql_shape={
            "tables": source.tables,
            "where": where_sql,
            "group_by": group_fields,
            "aggregate": {"method": method, "metric": metric, "alias": alias},
            "order": order_sql,
            "limit": -1 if limit_policy["explicit_full"] else int(limit_policy["fetch_limit"]),
            "hard_row_limit": int(limit_policy["hard_limit"]),
        },
    )


def _median_sql(
    *,
    source: ReportSource,
    metric: str,
    group_fields: List[str],
    alias: str,
    where_sql: str,
    order_sql: str,
) -> str:
    group_select = [f"{source.fields[field]} AS `{field}`" for field in group_fields]
    filtered_select = ", ".join(
        [*group_select, f"{source.fields[metric]} AS `__metric_value`"]
    )
    partition = ", ".join(f"`{field}`" for field in group_fields)
    over_prefix = f"PARTITION BY {partition} " if partition else ""
    ranked_group_fields = ", ".join(f"`{field}`" for field in group_fields)
    final_select = ", ".join(
        [*[f"`{field}`" for field in group_fields], f"AVG(`__metric_value`) AS `{alias}`"]
    )
    group_sql = f" GROUP BY {ranked_group_fields}" if ranked_group_fields else ""
    return (
        "WITH filtered AS ("
        f"SELECT {filtered_select} FROM {source.from_sql} "
        f"WHERE {where_sql} AND {source.fields[metric]} IS NOT NULL"
        "), ranked AS ("
        f"SELECT *, ROW_NUMBER() OVER ({over_prefix}ORDER BY `__metric_value`) AS `__row_num`, "
        f"COUNT(*) OVER ({over_prefix}) AS `__row_count` FROM filtered"
        ") "
        f"SELECT {final_select} FROM ranked "
        "WHERE `__row_num` IN (((`__row_count` + 1) DIV 2), ((`__row_count` + 2) DIV 2))"
        f"{group_sql} ORDER BY {order_sql} LIMIT %s"
    )


def _connect_report_db() -> StockInfoDbUtils:
    """Use the shared kingdomai connection contract used by other providers."""

    return StockInfoDbUtils(database="kingdomai")


def _run_query(*, sql: str, params: List[Any]) -> Dict[str, Any]:
    try:
        db = _connect_report_db()
    except Exception as exc:  # noqa: BLE001 - provider boundary returns a stable error.
        return {"rows": [], "error": f"connection failed: {exc}"}
    connection = getattr(db, "conn", db)
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            return {"rows": list(cursor.fetchall()), "error": ""}
    except Exception as exc:  # noqa: BLE001 - provider boundary returns a stable error.
        return {"rows": [], "error": str(exc)}
    finally:
        close_db = getattr(db, "close_db", None)
        if callable(close_db):
            close_db()
        else:
            close = getattr(db, "close", None)
            if callable(close):
                close()


def _requested_fields(*, source: ReportSource, outputs: List[str]) -> List[str]:
    if not outputs:
        return list(source.default_columns)
    fields: List[str] = []
    invalid: List[str] = []
    for output in outputs:
        token = _output_token(output)
        token = FIELD_ALIASES.get(token, token)
        if token not in source.fields:
            invalid.append(token or str(output))
        elif token not in fields:
            fields.append(token)
    if invalid:
        raise ValueError(
            f"unsupported {source.dataview} output fields={invalid}; "
            f"available={sorted(source.fields)}"
        )
    return fields


def _build_filter(*, source: ReportSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    raw = str(args.get("filter") or "").strip()
    expression = parse_filter_expression(raw)
    if not expression:
        return "1=1", []
    return _compile_filter(expression=expression, source=source)


def _compile_filter(
    *,
    expression: Mapping[str, Any],
    source: ReportSource,
) -> tuple[str, List[Any]]:
    for connective in ("and", "or"):
        children = expression.get(connective)
        if isinstance(children, list):
            compiled = [
                _compile_filter(expression=child, source=source)
                for child in children
                if isinstance(child, Mapping)
            ]
            if not compiled:
                raise ValueError(f"empty {connective} expression")
            sql = f" {connective.upper()} ".join(f"({item[0]})" for item in compiled)
            params = [value for item in compiled for value in item[1]]
            return sql, params

    raw_field = str(expression.get("field") or "").strip()
    field = FIELD_ALIASES.get(raw_field, raw_field)
    if field not in source.fields:
        raise ValueError(
            f"unsupported {source.dataview} filter field={raw_field}; "
            f"available={sorted(source.fields)}"
        )
    operator = str(expression.get("operator") or "").strip().lower()
    value = expression.get("value")
    field_sql = source.fields[field]
    if field == "report_date" and _is_date_only(value):
        field_sql = f"DATE({field_sql})"

    if operator in {"in", "not in"}:
        if operator == "not in":
            raise ValueError("operator=not in is not supported by report providers")
        values = value if isinstance(value, list) else _list_value(value)
        if not values:
            raise ValueError(f"filter field={field} uses an empty in list")
        if field == "institution":
            clauses: List[str] = []
            params: List[Any] = []
            for item in values:
                clause, clause_params = _institution_identity_filter(
                    field_sql=field_sql,
                    value=item,
                )
                clauses.append(f"({clause})")
                params.extend(clause_params)
            return " OR ".join(clauses), params
        normalized = [_normalize_filter_value(field, item) for item in values]
        return f"{_filter_field_sql(field=field, expression=field_sql)} IN ({', '.join(['%s'] * len(normalized))})", normalized
    if operator == "like":
        if value in (None, ""):
            raise ValueError(f"filter field={field} has an empty like value")
        return f"{field_sql} LIKE %s", [f"%{value}%"]
    if operator not in OP_SQL:
        raise ValueError(f"unsupported filter operator={operator}")
    if value is None:
        if operator in {"=", "=="}:
            return f"{field_sql} IS NULL", []
        if operator == "!=":
            return f"{field_sql} IS NOT NULL", []
        raise ValueError(f"operator={operator} cannot compare field={field} with null")
    if field == "institution" and operator in {"=", "=="}:
        return _institution_identity_filter(field_sql=field_sql, value=value)
    normalized = _normalize_filter_value(field, value)
    return f"{_filter_field_sql(field=field, expression=field_sql)} {OP_SQL[operator]} %s", [normalized]


def _normalize_institution_identity(value: Any) -> str:
    normalized = str(value or "").strip()
    changed = True
    while normalized and changed:
        changed = False
        for suffix in INSTITUTION_FORM_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[: -len(suffix)].strip()
                changed = True
                break
    return normalized


def _institution_identity_sql(expression: str) -> str:
    normalized = f"TRIM({expression})"
    for suffix in INSTITUTION_FORM_SUFFIXES:
        normalized = f"TRIM(TRAILING '{suffix}' FROM {normalized})"
    return normalized


def _institution_identity_filter(
    *,
    field_sql: str,
    value: Any,
) -> tuple[str, List[Any]]:
    exact = str(value or "").strip()
    if not exact:
        raise ValueError("filter field=institution has an empty value")
    identity = _normalize_institution_identity(exact)
    return (
        f"({field_sql} = %s OR {_institution_identity_sql(field_sql)} = %s)",
        [exact, identity],
    )


def _filter_field_sql(*, field: str, expression: str) -> str:
    if field == "code":
        return f"SUBSTRING_INDEX({expression}, '.', 1)"
    return expression


def _normalize_filter_value(field: str, value: Any) -> Any:
    if field == "code":
        return str(value or "").strip().split(".", 1)[0]
    if field == "metric_code":
        text = str(value or "").strip()
        normalized = METRIC_CODE_ALIASES.get(text) or METRIC_CODE_ALIASES.get(text.upper())
        if not normalized:
            allowed = ", ".join(f"{code}({name})" for code, name in METRIC_CODE_NAMES.items())
            raise ValueError(f"metric_code must be one of: {allowed}")
        return normalized
    return value


def _build_order(*, source: ReportSource, args: Mapping[str, Any]) -> str:
    items = parse_order(str(args.get("order") or ""))
    if not items:
        return source.default_order
    parts: List[str] = []
    for item in items:
        raw_field = str(item.get("field") or "")
        field = FIELD_ALIASES.get(raw_field, raw_field)
        if field not in source.fields:
            raise ValueError(
                f"unsupported {source.dataview} order field={raw_field}; "
                f"available={sorted(source.fields)}"
            )
        direction = "ASC" if str(item.get("direction") or "").lower() == "asc" else "DESC"
        parts.append(f"{source.fields[field]} {direction}")
    return ", ".join(parts)


def _group_fields(*, source: ReportSource, raw: Any) -> List[str]:
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
    result: List[str] = []
    for value in values:
        raw_field = str(value or "").strip()
        if not raw_field:
            continue
        field = FIELD_ALIASES.get(raw_field, raw_field)
        if field not in source.fields:
            raise ValueError(
                f"unsupported {source.dataview} group_by field={raw_field}; "
                f"available={sorted(source.fields)}"
            )
        if field not in result:
            result.append(field)
    return result


def _aggregate_alias(*, outputs: List[str], default: str, group_fields: List[str]) -> str:
    alias = output_alias(outputs, default=default, exclude=set(group_fields))
    if not re.fullmatch(r"[A-Za-z_]\w*", alias):
        raise ValueError(f"invalid aggregate output alias={alias}")
    return alias


def _build_aggregate_order(
    *,
    source: ReportSource,
    raw: Any,
    group_fields: List[str],
    alias: str,
) -> str:
    items = parse_order(str(raw or ""))
    if not items:
        return f"`{alias}` DESC"
    allowed = set(group_fields) | {alias}
    parts: List[str] = []
    for item in items:
        field = str(item.get("field") or "").strip()
        canonical = FIELD_ALIASES.get(field, field)
        if canonical not in allowed:
            raise ValueError(
                f"unsupported aggregate order field={field}; available={sorted(allowed)}"
            )
        direction = "ASC" if str(item.get("direction") or "").lower() == "asc" else "DESC"
        parts.append(f"`{canonical}` {direction}")
    return ", ".join(parts)


def _report_hard_row_limit() -> int:
    try:
        value = int(
            os.getenv(
                "FIN_AGENT_REPORT_HARD_ROW_LIMIT",
                str(DEFAULT_REPORT_HARD_ROW_LIMIT),
            )
        )
    except (TypeError, ValueError):
        value = DEFAULT_REPORT_HARD_ROW_LIMIT
    return max(DEFAULT_REPORT_LIMIT, value)


def _limit_policy(value: Any) -> Dict[str, Any]:
    hard_limit = _report_hard_row_limit()
    if value in (None, ""):
        return {
            "fetch_limit": DEFAULT_REPORT_LIMIT,
            "hard_limit": hard_limit,
            "explicit_full": False,
            "detect_overflow": False,
            "rejected": False,
            "reason": "",
        }
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    if parsed == -1:
        return {
            "fetch_limit": hard_limit + 1,
            "hard_limit": hard_limit,
            "explicit_full": True,
            "detect_overflow": True,
            "rejected": False,
            "reason": "",
        }
    if parsed <= 0:
        return {
            "fetch_limit": 0,
            "hard_limit": hard_limit,
            "explicit_full": False,
            "detect_overflow": False,
            "rejected": True,
            "reason": "limit must be -1 for explicit full results or a positive integer",
        }
    if parsed > hard_limit:
        return {
            "fetch_limit": 0,
            "hard_limit": hard_limit,
            "explicit_full": False,
            "detect_overflow": False,
            "rejected": True,
            "reason": (
                f"requested limit={parsed} exceeds the safety maximum of {hard_limit}; "
                "use limit=-1 for an explicit full query"
            ),
        }
    return {
        "fetch_limit": parsed,
        "hard_limit": hard_limit,
        "explicit_full": False,
        "detect_overflow": False,
        "rejected": False,
        "reason": "",
    }


def _overflow_result(
    *,
    source: ReportSource,
    args: Mapping[str, Any],
    columns: List[str],
    hard_limit: int,
    api_suffix: str = "",
) -> Dict[str, Any]:
    return _result(
        status="result_too_large",
        source=source,
        args=args,
        columns=columns,
        rows=[],
        api_suffix=api_suffix,
        reason=(
            f"explicit full {source.dataview} query exceeds the safety maximum "
            f"of {hard_limit} rows; narrow the query or raise "
            "FIN_AGENT_REPORT_HARD_ROW_LIMIT for an authorized bulk run"
        ),
    )


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text.lower():
        text = re.split(r"\s+as\s+", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if "(" in text and ")" in text:
        text = text.split("(", 1)[1].rsplit(")", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _list_value(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    text = str(value or "").strip()
    if text[:1] in "[(" and text[-1:] in ")]":
        text = text[1:-1]
    return [item.strip().strip("\"'") for item in text.split(",") if item.strip()]


def _is_date_only(value: Any) -> bool:
    return bool(
        re.fullmatch(
            r"\d{4}-\d{1,2}-\d{1,2}|\d{8}",
            str(value or "").strip(),
        )
    )


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    result = {column: _normalize_value(row.get(column)) for column in columns}
    if "code" in result:
        result["code"] = _normalize_security_code(result.get("code"))
    if "analysts" in result and isinstance(result["analysts"], str):
        try:
            parsed = json.loads(result["analysts"])
            if isinstance(parsed, list):
                result["analysts"] = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    if "reliable" in result and result["reliable"] is not None:
        result["reliable"] = bool(result["reliable"])
    return result


def _normalize_security_code(value: Any) -> Any:
    text = str(value or "").strip().upper()
    if not text or "." in text or not re.fullmatch(r"\d{6}", text):
        return text or value
    if text.startswith("6"):
        return f"{text}.SH"
    if text.startswith(("4", "8")):
        return f"{text}.BJ"
    return f"{text}.SZ"


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _result(
    *,
    status: str,
    source: ReportSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    reason: str = "",
    sql_shape: Mapping[str, Any] | None = None,
    api_suffix: str = "",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": f"{source.api}{api_suffix}",
        "subject": "stock",
        "dataview": source.dataview,
        "provider": "kingdomai",
        "source": list(source.tables),
        "arguments": dict(args),
        "columns": columns,
        "available_fields": sorted(source.fields),
        "aggregate_fields": {
            field: list(methods)
            for field, methods in source.aggregate_fields.items()
        },
        "rows": rows,
        "row_count": len(rows),
    }
    if reason:
        result["reason"] = reason
    if sql_shape is not None:
        result["sql_shape"] = dict(sql_shape)
    return result
