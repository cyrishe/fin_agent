from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.experiments.staged_data_protocol.phase2.agg_protocol import output_alias, parse_agg_spec
from src.experiments.staged_data_protocol.phase2.models import ResultHandle
from src.experiments.staged_data_protocol.phase2.financial_provider import DEFAULT_STATEMENT_TYPE, STOCK_FINANCIAL_SOURCE
from src.experiments.staged_data_protocol.phase2.moneyflow_provider import MONEYFLOW_SOURCES
from src.experiments.staged_data_protocol.phase2.quote_provider import QUOTE_SOURCES
from src.utils.mysql_utils import StockInfoDbUtils


@dataclass(frozen=True)
class ConstitutionSource:
    subject: str
    fields: Mapping[str, str]


CONSTITUTION_SOURCES: Dict[str, ConstitutionSource] = {
    "index": ConstitutionSource(
        subject="index",
        fields={
            "index_code": "m.idx_code",
            "index_name": "ib.index_short_name",
            "stock_code": "m.stk_code",
            "stock_name": "sb.stk_name",
            "weight": "NULL",
        },
    ),
    "plate": ConstitutionSource(
        subject="plate",
        fields={
            "plate_code": "m.plate_code",
            "plate_name": "p.plate_name",
            "stock_code": "m.stk_code",
            "stock_name": "sb.stk_name",
            "weight": "NULL",
        },
    ),
    "industry": ConstitutionSource(
        subject="industry",
        fields={
            "industry_code": "m.industry_code",
            "industry_name": "ib.industry_name",
            "level": "ib.level",
            "level1_industry_name": "m.level1_industry_name",
            "level2_industry_name": "m.level2_industry_name",
            "level3_industry_name": "m.level3_industry_name",
            "stock_code": "m.stk_code",
            "stock_name": "COALESCE(m.stk_name, sb.stk_name)",
            "weight": "NULL",
        },
    ),
}


INDUSTRY_BASE_SOURCE = ConstitutionSource(
    subject="industry",
    fields={
        "industry_code": "industry_code",
        "industry_name": "industry_name",
        "level": "level",
        "level1_industry_name": "level1_industry_name",
        "level2_industry_name": "level2_industry_name",
        "level3_industry_name": "level3_industry_name",
    },
)


MOCK_SUBJECTS = {"fund", "hot_event"}
OP_SQL = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*"
    r"(?P<op>in|=|==|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*(?:in|=|==|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)
PREVIOUS_METRIC_RE = re.compile(r"^(r\d+)\.([A-Za-z_]\w*)$")
AGG_METHODS = {"sum", "avg", "max", "min", "median", "count"}
DEFAULT_CONSTITUTION_LIMIT = 100
DEFAULT_CONSTITUTION_HARD_ROW_LIMIT = 10000


def execute_constitution_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = CONSTITUTION_SOURCES.get(subject) or _mock_source(subject)
    columns = _requested_columns(source=source, outputs=outputs)
    ignored_filters = _ignored_filters(source=source, args=args)
    if subject in MOCK_SUBJECTS or subject not in CONSTITUTION_SOURCES:
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=columns,
            ignored_filters=ignored_filters,
        )
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason="filter contains result references that must be materialized by the execution engine",
        )

    limit_policy = _constitution_limit_policy(args.get("limit"))
    if limit_policy["rejected"]:
        return _standard_result(
            status="result_too_large",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason=str(limit_policy["reason"]),
        )
    limit = int(limit_policy["fetch_limit"])
    where_sql, params = _build_where(source=source, args=args)
    order_sql = _build_order(source=source, args=args)
    sql = _build_sql(source=source, columns=columns, where_sql=where_sql, order_sql=order_sql)
    params.append(limit)

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        if limit_policy["detect_overflow"] and len(raw_rows) > int(limit_policy["hard_limit"]):
            return _standard_result(
                status="result_too_large",
                source=source,
                args=args,
                columns=columns,
                rows=[],
                mocked_fields=_mocked_fields(source=source, columns=columns),
                ignored_filters=ignored_filters,
                reason=(
                    "explicit full constitution query exceeds the safety maximum "
                    f"of {limit_policy['hard_limit']} rows; narrow the universe or date"
                ),
            )
        rows = [_normalize_row(row, columns) for row in raw_rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            sql_shape={
                "where": where_sql,
                "order": order_sql,
                "limit": -1 if limit_policy["explicit_full"] else limit,
            },
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(
            status="provider_error",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason=str(exc),
        )
    finally:
        db.close_db()


def execute_industry_base_info_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = INDUSTRY_BASE_SOURCE
    columns = _requested_columns(source=source, outputs=outputs)
    if not columns:
        columns = ["industry_code", "industry_name", "level"]
    ignored_filters = _ignored_filters(source=source, args=args)
    filter_sql, params = _build_filter_clauses(source=source, args=args)
    where_sql = filter_sql or "1=1"
    order_sql = _build_order(source=source, args=args)
    limit_policy = _constitution_limit_policy(args.get("limit"))
    if limit_policy["rejected"]:
        data = _standard_result(
            status="result_too_large",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason=str(limit_policy["reason"]),
        )
        data["api"] = "industry.basic_info"
        return data
    limit = int(limit_policy["fetch_limit"])
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    sql = f"""
        SELECT {select_sql}
        FROM kcrp_industry_base
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
        if limit_policy["detect_overflow"] and len(raw_rows) > int(limit_policy["hard_limit"]):
            data = _standard_result(
                status="result_too_large",
                source=source,
                args=args,
                columns=columns,
                rows=[],
                mocked_fields=_mocked_fields(source=source, columns=columns),
                ignored_filters=ignored_filters,
                reason=(
                    "explicit full industry query exceeds the safety maximum "
                    f"of {limit_policy['hard_limit']} rows; narrow the query"
                ),
            )
            data["api"] = "industry.basic_info"
            return data
        rows = [_normalize_row(row, columns) for row in raw_rows]
        data = _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            sql_shape={"where": where_sql, "order": order_sql, "limit": limit},
        )
        data["api"] = "industry.basic_info"
        return data
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        data = _standard_result(
            status="provider_error",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            reason=str(exc),
        )
        data["api"] = "industry.basic_info"
        return data
    finally:
        db.close_db()


def execute_constitution_agg_api(
    *,
    subject: str,
    args: Mapping[str, Any],
    outputs: List[str],
    previous_results: Mapping[str, ResultHandle],
) -> Dict[str, Any]:
    source = CONSTITUTION_SOURCES.get(subject) or _mock_source(subject)
    columns = _agg_columns(outputs)
    agg_spec = parse_agg_spec(args)
    metric_ref = PREVIOUS_METRIC_RE.fullmatch(agg_spec.metric)
    group_fields = _group_fields(source=source, args=args)
    constitution_outputs = _unique(group_fields + ["stock_code", "stock_name"])
    constitution_args = {key: value for key, value in args.items() if key not in {"metric", "agg", "group_by", "order"}}
    constitution_args["limit"] = 10000
    constitution_data = execute_constitution_api(subject=subject, args=constitution_args, outputs=constitution_outputs)
    constitution_rows = constitution_data.get("rows") if isinstance(constitution_data, Mapping) else None
    if not isinstance(constitution_rows, list):
        constitution_rows = []

    if metric_ref:
        result_name, metric_column = metric_ref.groups()
        metric_handle = previous_results.get(result_name)
        if not metric_handle:
            return _standard_agg_result(
                status="provider_error",
                source=source,
                args=args,
                columns=columns,
                rows=[],
                reason=f"previous result not found: {result_name}",
            )
        metric_rows = metric_handle.data.get("rows") if isinstance(metric_handle.data, Mapping) else None
        if not isinstance(metric_rows, list):
            return _standard_agg_result(
                status="provider_error",
                source=source,
                args=args,
                columns=columns,
                rows=[],
                reason=f"previous result has no rows: {result_name}",
            )
    else:
        result_name = agg_spec.metric
        stock_codes = _unique([_code_key(row.get("stock_code")) for row in constitution_rows if isinstance(row, Mapping)])
        metric_column, metric_rows, metric_error = _standard_metric_rows(metric=result_name, args=args, stock_codes=stock_codes)
        if metric_error:
            return _standard_agg_result(
                status="provider_error",
                source=source,
                args=args,
                columns=columns,
                rows=[],
                reason=metric_error,
            )

    agg = agg_spec.method
    metric_by_code, metric_stats = _metric_values_by_code(metric_rows, metric_column, count_mode=(agg == "count"))

    alias = _aggregate_alias(outputs, default=f"{agg}_{metric_column}", group_fields=group_fields)
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    joined_row_count = 0
    for row in constitution_rows:
        if not isinstance(row, Mapping):
            continue
        stock_code = _code_key(row.get("stock_code"))
        if not stock_code or stock_code not in metric_by_code:
            continue
        values = metric_by_code[stock_code]
        if not values:
            continue
        joined_row_count += 1
        group_key = tuple(row.get(field) for field in group_fields)
        bucket = grouped.setdefault(group_key, {field: row.get(field) for field in group_fields} | {"_values": []})
        bucket["_values"].extend(values)

    rows: list[dict[str, Any]] = []
    for bucket in grouped.values():
        values = bucket.pop("_values")
        bucket[alias] = _aggregate(values, agg)
        rows.append({column: _normalize_value(bucket.get(column)) for column in columns})
    rows = _sort_and_limit(rows, args=args, alias=alias)
    return _standard_agg_result(
        status="ok",
        source=source,
        args=args,
        columns=columns,
        rows=rows,
        diagnostics={
            "metric_result": result_name,
            "metric_column": metric_column,
            "metric_input_row_count": len(metric_rows),
            "metric_code_count": len(metric_by_code),
            "constitution_row_count": len(constitution_rows),
            "joined_row_count": joined_row_count,
            "group_count": len(grouped),
            **metric_stats,
        },
    )


def _metric_values_by_code(rows: list[Any], metric_column: str, *, count_mode: bool = False) -> tuple[dict[str, list[float]], dict[str, int]]:
    values_by_code: dict[str, list[float]] = {}
    dropped_null_metric_count = 0
    dropped_non_numeric_count = 0
    dropped_missing_code_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = _code_key(row.get("code") or row.get("stock_code"))
        if not code:
            dropped_missing_code_count += 1
            continue
        raw_value = row.get(metric_column)
        if count_mode:
            values_by_code.setdefault(code, []).append(1.0)
            continue
        if raw_value in (None, ""):
            dropped_null_metric_count += 1
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            dropped_non_numeric_count += 1
            continue
        values_by_code.setdefault(code, []).append(value)
    return values_by_code, {
        "dropped_missing_code_count": dropped_missing_code_count,
        "dropped_null_metric_count": dropped_null_metric_count,
        "dropped_non_numeric_count": dropped_non_numeric_count,
    }


def _code_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _group_fields(*, source: ConstitutionSource, args: Mapping[str, Any]) -> list[str]:
    raw = str(args.get("group_by") or "").strip()
    fields = [item.strip() for item in raw.split(",") if item.strip()]
    if fields:
        return [field for field in fields if field in source.fields]
    return [field for field in [f"{source.subject}_code", f"{source.subject}_name"] if field in source.fields]


def _unique(values: list[str]) -> list[str]:
    rows: list[str] = []
    for value in values:
        if value and value not in rows:
            rows.append(value)
    return rows


def _agg_columns(outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        column = _agg_output_column(output)
        if column and column not in columns:
            columns.append(column)
    return columns


def _agg_output_column(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        return text.split(" as ", 1)[1].strip()
    if "(" in text and ")" in text:
        inner = text.split("(", 1)[1].split(")", 1)[0].strip()
        return inner or text
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _aggregate_alias(outputs: List[str], *, default: str, group_fields: List[str] | None = None) -> str:
    return output_alias(outputs, default=default, exclude=set(group_fields or []))


def _aggregate(values: list[float], method: str) -> float | int | None:
    if not values:
        return None
    if method == "count":
        return len(values)
    if method == "sum":
        return float(sum(values))
    if method == "avg":
        return float(sum(values) / len(values))
    if method == "max":
        return float(max(values))
    if method == "min":
        return float(min(values))
    if method == "median":
        return float(statistics.median(values))
    return None


def _standard_metric_rows(*, metric: str, args: Mapping[str, Any], stock_codes: list[str]) -> tuple[str, list[dict[str, Any]], str]:
    parts = str(metric or "").strip().split(".")
    if len(parts) != 3:
        return "", [], f"unsupported metric={metric}"
    metric_subject, dataview, field_name = parts
    if metric_subject != "stock":
        return "", [], f"constitution.agg standard metric only supports stock metrics, got={metric}"
    if not stock_codes:
        return field_name, [], ""
    if dataview == "quote":
        source = QUOTE_SOURCES.get("stock")
        if not source or field_name not in source.fields:
            return "", [], f"unsupported stock.quote metric field={field_name}"
        return field_name, _query_quote_metric_rows(source=source, field_name=field_name, args=args, stock_codes=stock_codes), ""
    if dataview == "moneyflow":
        source = MONEYFLOW_SOURCES.get("stock")
        if not source or field_name not in source.fields or source.fields[field_name] == "NULL":
            return "", [], f"unsupported stock.moneyflow metric field={field_name}"
        return field_name, _query_moneyflow_metric_rows(source=source, field_name=field_name, args=args, stock_codes=stock_codes), ""
    if dataview == "financial_3_table":
        source = STOCK_FINANCIAL_SOURCE
        if field_name not in source.fields or source.fields[field_name] == "NULL":
            return "", [], f"unsupported stock.financial_3_table metric field={field_name}"
        return field_name, _query_financial_metric_rows(source=source, field_name=field_name, args=args, stock_codes=stock_codes), ""
    return "", [], f"unsupported stock metric dataview={dataview}"


def _query_quote_metric_rows(*, source: Any, field_name: str, args: Mapping[str, Any], stock_codes: list[str]) -> list[dict[str, Any]]:
    where_sql, params = _metric_market_where(source=source, args=args, stock_codes=stock_codes)
    select_sql = f"{source.fields['code']} AS `code`, {source.fields[field_name]} AS `{field_name}`"
    sql = f"""
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {where_sql}
    """
    return _fetch_metric_rows(sql=sql, params=params, field_name=field_name)


def _query_moneyflow_metric_rows(*, source: Any, field_name: str, args: Mapping[str, Any], stock_codes: list[str]) -> list[dict[str, Any]]:
    where_sql, params = _metric_market_where(source=source, args=args, stock_codes=stock_codes)
    select_sql = f"{source.fields['code']} AS `code`, {source.fields[field_name]} AS `{field_name}`"
    sql = f"""
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {where_sql}
    """
    return _fetch_metric_rows(sql=sql, params=params, field_name=field_name)


def _query_financial_metric_rows(*, source: Any, field_name: str, args: Mapping[str, Any], stock_codes: list[str]) -> list[dict[str, Any]]:
    statement_type = str(args.get("statement_type") or DEFAULT_STATEMENT_TYPE).strip() or DEFAULT_STATEMENT_TYPE
    report_date = str(args.get("report_date") or args.get("report_period") or "").strip()
    if report_date:
        date_sql = "i.report_period = %s"
        date_params: list[Any] = [report_date]
    else:
        date_sql = "i.report_period = (SELECT MAX(report_period) FROM kcrp_stock_income)"
        date_params = []
    code_sql, code_params = _code_in_sql("i.stk_code", stock_codes)
    filter_sql, filter_params = _metric_filter_clauses(fields=source.fields, args=args, allowed=set(source.fields))
    clauses = [date_sql, "i.statement_type = %s", code_sql]
    params = [*date_params, statement_type, *code_params]
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    sql = f"""
        SELECT {source.fields['code']} AS `code`, {source.fields[field_name]} AS `{field_name}`
        FROM kcrp_stock_income i
        LEFT JOIN kcrp_stock_baseinfo base ON base.stk_code = i.stk_code
        LEFT JOIN kcrp_stock_balancesheet bs
            ON bs.stk_code = i.stk_code
           AND bs.report_period = i.report_period
           AND bs.statement_type = i.statement_type
        LEFT JOIN kcrp_stock_cashflow cf
            ON cf.stk_code = i.stk_code
           AND cf.report_period = i.report_period
           AND cf.statement_type = i.statement_type
        LEFT JOIN kcrp_stock_financial_indicator fi
            ON fi.stk_code = i.stk_code
           AND fi.report_period = i.report_period
        WHERE {" AND ".join(clauses)}
    """
    return _fetch_metric_rows(sql=sql, params=params, field_name=field_name)


def _metric_market_where(*, source: Any, args: Mapping[str, Any], stock_codes: list[str]) -> tuple[str, list[Any]]:
    exact_date = str(args.get("date") or args.get("tradedate") or "").strip()
    clauses: list[str] = []
    params: list[Any] = []
    if exact_date:
        clauses.append(f"{source.fields['tradedate']} = %s")
        params.append(exact_date)
    else:
        clauses.append(f"{source.fields['tradedate']} = (SELECT MAX(trade_date) FROM {source.table})")
    code_sql, code_params = _code_in_sql(source.fields["code"], stock_codes)
    clauses.append(code_sql)
    params.extend(code_params)
    filter_sql, filter_params = _metric_filter_clauses(fields=source.fields, args=args, allowed=set(source.fields))
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return " AND ".join(clauses), params


def _metric_filter_clauses(*, fields: Mapping[str, str], args: Mapping[str, Any], allowed: set[str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for connector, field_name, op, value in _metric_explicit_filters(args):
        if field_name not in allowed or op not in (set(OP_SQL) | {"in"}):
            continue
        expression = fields.get(field_name)
        if not expression or expression == "NULL":
            continue
        prefix = connector if clauses else ""
        if op == "in":
            values = _list_value(value)
            if not values:
                continue
            clauses.append(f"{prefix} {expression} IN ({', '.join(['%s'] * len(values))})".strip())
            params.extend(values)
            continue
        clauses.append(f"{prefix} {expression} {OP_SQL[op]} %s".strip())
        params.append(value)
    if not clauses:
        return "", []
    return f"({' '.join(clauses)})", params


def _metric_explicit_filters(args: Mapping[str, Any]) -> list[tuple[str, str, str, Any]]:
    rows: list[tuple[str, str, str, Any]] = []
    filter_text = str(args.get("filter") or "").strip()
    for match in FILTER_RE.finditer(filter_text):
        rows.append(
            (
                str(match.group("connector") or "AND").upper(),
                str(match.group("field") or "").strip().rsplit(".", 1)[-1],
                str(match.group("op") or "").strip().lower(),
                _clean_value(str(match.group("value") or "")),
            )
        )
    return rows


def _code_in_sql(expression: str, stock_codes: list[str]) -> tuple[str, list[Any]]:
    codes = [code for code in stock_codes if code]
    return f"{expression} IN ({', '.join(['%s'] * len(codes))})", codes


def _fetch_metric_rows(*, sql: str, params: list[Any], field_name: str) -> list[dict[str, Any]]:
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        return [{"code": _normalize_value(row.get("code")), field_name: _normalize_value(row.get(field_name))} for row in raw_rows]
    finally:
        db.close_db()


def _sort_and_limit(rows: list[dict[str, Any]], *, args: Mapping[str, Any], alias: str) -> list[dict[str, Any]]:
    order = str(args.get("order") or "").strip()
    if order and order.lower() != "none":
        parts = re.split(r"\s+", order, maxsplit=1)
        field_name = parts[0].strip()
        direction = parts[1].strip().lower() if len(parts) > 1 else "asc"
        reverse = direction.startswith("desc")
        rows.sort(key=lambda row: (row.get(field_name) is None, row.get(field_name)), reverse=reverse)
    elif alias:
        rows.sort(key=lambda row: (row.get(alias) is None, row.get(alias)))
    limit = args.get("limit")
    if isinstance(limit, int) and limit > 0:
        return rows[:limit]
    return rows


def _standard_agg_result(
    *,
    status: str,
    source: ConstitutionSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    provider: str | None = None,
    reason: str | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": f"{source.subject}.constitution.agg",
        "subject": source.subject,
        "arguments": dict(args),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }
    if provider:
        result["provider"] = provider
    if reason:
        result["reason"] = reason
    if diagnostics:
        result["diagnostics"] = dict(diagnostics)
    return result


def _mock_source(subject: str) -> ConstitutionSource:
    fields = {
        f"{subject}_code": "NULL",
        f"{subject}_name": "NULL",
        "stock_code": "NULL",
        "stock_name": "NULL",
        "weight": "NULL",
    }
    return ConstitutionSource(subject=subject, fields=fields)


def _requested_columns(*, source: ConstitutionSource, outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        token = _normalize_field_token(source=source, token=_output_token(output))
        if token in source.fields and token not in columns:
            columns.append(token)
    if columns:
        return columns
    return [field for field in [f"{source.subject}_code", f"{source.subject}_name", "stock_code", "stock_name", "weight"] if field in source.fields]


def _normalize_field_token(*, source: ConstitutionSource, token: str) -> str:
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    if token == "code":
        return f"{source.subject}_code"
    if token == "name":
        return f"{source.subject}_name"
    return token


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _has_unresolved_ref(args: Mapping[str, Any]) -> bool:
    return any(isinstance(value, str) and re.search(r"\br\d+\.", value) for value in args.values())


def _constitution_hard_row_limit() -> int:
    try:
        configured = int(
            os.getenv(
                "FIN_AGENT_CONSTITUTION_HARD_ROW_LIMIT",
                str(DEFAULT_CONSTITUTION_HARD_ROW_LIMIT),
            )
        )
    except (TypeError, ValueError):
        configured = DEFAULT_CONSTITUTION_HARD_ROW_LIMIT
    return max(DEFAULT_CONSTITUTION_LIMIT, configured)


def _constitution_limit_policy(value: Any) -> Dict[str, Any]:
    hard_limit = _constitution_hard_row_limit()
    if value in (None, ""):
        return {
            "fetch_limit": DEFAULT_CONSTITUTION_LIMIT,
            "hard_limit": hard_limit,
            "explicit_full": False,
            "detect_overflow": False,
            "rejected": False,
            "reason": "",
        }
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_CONSTITUTION_LIMIT
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
                f"requested limit={parsed} exceeds the safety maximum of "
                f"{hard_limit}; use limit=-1 for an explicit full query"
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


def _bounded_limit(value: Any) -> int:
    policy = _constitution_limit_policy(value)
    if policy["rejected"]:
        return DEFAULT_CONSTITUTION_LIMIT
    return int(policy["fetch_limit"])


def _as_of(args: Mapping[str, Any]) -> str:
    value = str(args.get("as_of") or args.get("date") or args.get("tradedate") or "").strip()
    return value or date.today().isoformat()


def _build_where(*, source: ConstitutionSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    as_of = _as_of(args)
    if source.subject == "index":
        clauses.append("m.begin_date <= %s AND m.end_date >= %s")
        params.extend([as_of, as_of])
    elif source.subject == "plate":
        clauses.append("p.begin_date <= %s AND p.end_date >= %s")
        params.extend([as_of, as_of])
    elif source.subject == "industry":
        exchange = str(args.get("exchange") or args.get("market") or "A股").strip()
        if exchange and exchange not in {"全部", "all", "ALL", "*"}:
            clauses.append("m.exchange = %s")
            params.append(exchange)

    filter_sql, filter_params = _build_filter_clauses(source=source, args=args)
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return " AND ".join(clauses) or "1=1", params


def _build_filter_clauses(*, source: ConstitutionSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for connector, field_name, op, value in _explicit_filters(source=source, args=args):
        expression = source.fields.get(field_name)
        if not expression or expression == "NULL":
            continue
        prefix = connector if clauses else ""
        if source.subject == "industry" and field_name in {"industry_code", "industry_name"}:
            condition_sql, condition_params = _industry_filter_sql(field_name=field_name, op=op, value=value)
            if condition_sql:
                clauses.append(f"{prefix} {condition_sql}".strip())
                params.extend(condition_params)
                continue
        if op == "in":
            values = _list_value(value)
            if not values:
                continue
            placeholders = ", ".join(["%s"] * len(values))
            clauses.append(f"{prefix} {expression} IN ({placeholders})".strip())
            params.extend(values)
            continue
        if op not in OP_SQL:
            continue
        equivalent_values = _equivalent_filter_values(source=source, field_name=field_name, value=value)
        if len(equivalent_values) > 1 and op in {"=", "=="}:
            placeholders = " OR ".join([f"{expression} = %s"] * len(equivalent_values))
            clauses.append(f"{prefix} ({placeholders})".strip())
            params.extend(equivalent_values)
            continue
        clauses.append(f"{prefix} {expression} {OP_SQL[op]} %s".strip())
        params.append(value)
    if not clauses:
        return "", []
    return f"({' '.join(clauses)})", params


def _equivalent_filter_values(*, source: ConstitutionSource, field_name: str, value: Any) -> List[Any]:
    text = str(value or "").strip()
    if source.subject == "plate" and field_name == "plate_name" and text.endswith("板块") and len(text) > 2:
        return [value, text.removesuffix("板块")]
    return [value]


def _industry_filter_sql(*, field_name: str, op: str, value: Any) -> tuple[str, list[Any]]:
    if field_name == "industry_name":
        names = _list_value(value) if op == "in" else [value]
        if not names:
            return "", []
        name_exprs = ["m.level1_industry_name", "m.level2_industry_name", "m.level3_industry_name"]
        if op == "in":
            placeholders = ", ".join(["%s"] * len(names))
            return f"({' OR '.join(f'{expr} IN ({placeholders})' for expr in name_exprs)})", names * len(name_exprs)
        if op in {"=", "=="}:
            return f"({' OR '.join(f'{expr} = %s' for expr in name_exprs)})", names * len(name_exprs)
        return "", []
    if field_name == "industry_code":
        codes = _list_value(value) if op == "in" else [value]
        if not codes:
            return "", []
        placeholders = ", ".join(["%s"] * len(codes))
        code_sql = (
            f"(m.industry_code IN ({placeholders}) OR EXISTS ("
            "SELECT 1 FROM kcrp_industry_base ib "
            f"WHERE ib.industry_code IN ({placeholders}) "
            "AND ib.industry_name IN (m.level1_industry_name, m.level2_industry_name, m.level3_industry_name)"
            "))"
        )
        return code_sql, codes + codes
    return "", []


def _ignored_filters(*, source: ConstitutionSource, args: Mapping[str, Any]) -> List[str]:
    ignored: List[str] = []
    for _connector, field_name, op, value in _explicit_filters(source=source, args=args):
        if source.fields.get(field_name) == "NULL":
            ignored.append(f"{field_name} {op} {value}")
    return ignored


def _explicit_filters(*, source: ConstitutionSource, args: Mapping[str, Any]) -> List[tuple[str, str, str, Any]]:
    rows: List[tuple[str, str, str, Any]] = []
    for raw_field in source.fields:
        value = args.get(raw_field)
        if value not in (None, ""):
            rows.append(("AND", raw_field, "=", value))
    for alias in ["code", "name"]:
        value = args.get(alias)
        if value not in (None, ""):
            rows.append(("AND", _normalize_field_token(source=source, token=alias), "=", value))
    for plural, field_name in [("codes", "stock_code"), ("names", "stock_name")]:
        value = args.get(plural)
        if value not in (None, ""):
            rows.append(("AND", field_name, "in", value))
    filter_text = str(args.get("filter") or "").strip()
    for match in FILTER_RE.finditer(filter_text):
        rows.append(
            (
                str(match.group("connector") or "AND").upper(),
                _normalize_field_token(source=source, token=str(match.group("field") or "").strip()),
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


def _build_order(*, source: ConstitutionSource, args: Mapping[str, Any]) -> str:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return f"{source.fields.get(f'{source.subject}_code', '1')} ASC, {source.fields.get('stock_code', '1')} ASC"
    parts = re.split(r"\s+", text, maxsplit=1)
    field_name = _normalize_field_token(source=source, token=parts[0].strip())
    direction = parts[1].strip().lower() if len(parts) > 1 else "asc"
    expression = source.fields.get(field_name)
    if not expression or expression == "NULL":
        return f"{source.fields.get(f'{source.subject}_code', '1')} ASC, {source.fields.get('stock_code', '1')} ASC"
    direction_sql = "DESC" if direction.startswith("desc") else "ASC"
    return f"{expression} {direction_sql}, {source.fields.get('stock_code', '1')} ASC"


def _build_sql(*, source: ConstitutionSource, columns: List[str], where_sql: str, order_sql: str) -> str:
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    if source.subject == "index":
        return f"""
            SELECT {select_sql}
            FROM kcrp_index_member m
            LEFT JOIN kcrp_index_base ib ON ib.idx_code = m.idx_code
            LEFT JOIN kcrp_stock_baseinfo sb ON sb.stk_code = m.stk_code
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s
        """
    if source.subject == "plate":
        return f"""
            SELECT {select_sql}
            FROM kcrp_yp_plate_member m
            LEFT JOIN kcrp_yp_plate p ON p.plate_code = m.plate_code
            LEFT JOIN kcrp_stock_baseinfo sb ON sb.stk_code = m.stk_code
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s
        """
    if source.subject == "industry":
        return f"""
            SELECT {select_sql}
            FROM kcrp_industry_member m
            LEFT JOIN kcrp_industry_base ib ON ib.industry_code = m.industry_code
            LEFT JOIN kcrp_stock_baseinfo sb ON sb.stk_code = m.stk_code
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s
        """
    return f"""
        SELECT {select_sql}
        FROM kcrp_stock_industry m
        LEFT JOIN kcrp_stock_baseinfo sb ON sb.stk_code = m.stk_code
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: _normalize_value(row.get(column)) for column in columns}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _mocked_fields(*, source: ConstitutionSource, columns: List[str]) -> List[str]:
    return [column for column in columns if source.fields.get(column) == "NULL"]


def _standard_result(
    *,
    status: str,
    source: ConstitutionSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    mocked_fields: List[str] | None = None,
    ignored_filters: List[str] | None = None,
    reason: str | None = None,
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": f"{source.subject}.constitution",
        "subject": source.subject,
        "arguments": dict(args),
        "columns": columns,
        "rows": rows,
        "mocked_fields": mocked_fields or [],
        "ignored_filters": ignored_filters or [],
    }
    if reason:
        result["reason"] = reason
    if sql_shape:
        result["sql_shape"] = dict(sql_shape)
    return result
