from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils


@dataclass(frozen=True)
class MarginSource:
    subject: str
    table: str
    base_table: str
    join_on: str
    fields: Mapping[str, str]


MARGIN_FIELDS = [
    "code",
    "name",
    "tradedate",
    "financing_balance",
    "financing_buy",
    "financing_repay",
    "financing_net_buy",
    "securities_lending_balance",
    "securities_lending_balancevol",
    "securities_lending_sell_vol",
    "securities_lending_repay_vol",
    "margin_balance",
    "margin_diff",
    "financing_balance_float_mv_ratio",
]


MARGIN_SOURCE = MarginSource(
    subject="stock",
    table="kcrp_stock_margintrade",
    base_table="kcrp_stock_baseinfo",
    join_on="b.stk_code = q.stk_code",
    fields={
        "code": "q.stk_code",
        "name": "b.stk_name",
        "tradedate": "q.trade_date",
        "financing_balance": "q.trading_balance",
        "financing_buy": "q.purch_with_borrow_money",
        "financing_repay": "q.repayment_to_broker",
        "financing_net_buy": "(q.purch_with_borrow_money - q.repayment_to_broker)",
        "securities_lending_balance": "q.sec_lending_balance",
        "securities_lending_balancevol": "q.sec_lending_balancevol",
        "securities_lending_sell_vol": "q.sales_of_borrowedsec",
        "securities_lending_repay_vol": "q.repayment_of_borrowsec",
        "margin_balance": "q.margintrade_balance",
        "margin_diff": "q.diffrence",
        "financing_balance_float_mv_ratio": "q.fin_balance_to_liqmu",
    },
)


MARGIN_KD_FIELDS = {
    "financing_buy",
    "financing_repay",
    "financing_net_buy",
    "securities_lending_sell_vol",
    "securities_lending_repay_vol",
    "financing_balance",
    "securities_lending_balance",
    "securities_lending_balancevol",
    "margin_balance",
    "margin_diff",
    "financing_balance_float_mv_ratio",
}
MARGIN_KD_METHODS = {"sum", "avg", "max", "min", "median", "count", "change", "pct_change"}
MARGIN_KD_METHOD_RE = re.compile(r"^[A-Za-z_]\w*$")
MARGIN_DEFAULT_METHOD = "sum"
OP_SQL = {"=": "=", "==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|==|=|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|==|=|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_margin_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = MARGIN_SOURCE
    if subject != "stock":
        return _standard_result(
            status="unsupported",
            source=source,
            args=args,
            columns=_requested_columns(outputs),
            rows=[],
            reason="margin provider is currently available for stock subject only",
        )
    columns = _requested_columns(outputs)
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

    db = StockInfoDbUtils(database="kingdomai")
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


def execute_kd_margin_api(
    *,
    subject: str,
    field: str,
    method: str,
    args: Mapping[str, Any],
    outputs: List[str],
) -> Dict[str, Any]:
    source = MARGIN_SOURCE
    api_name = f"{subject}.margin.kd_{field}_{method}"
    columns = _requested_kd_columns(outputs)
    if subject != "stock" or field not in source.fields:
        return _standard_result(
            status="unsupported",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            api_name=api_name,
            reason="kd margin provider is currently available for configured stock margin fields only",
        )
    if not _method_allowed(field=field, method=method):
        return _standard_result(
            status="unsupported",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            api_name=api_name,
            reason=f"method={method} is not supported for field={field}",
        )
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            api_name=api_name,
            reason="filter contains result references that must be materialized by the execution engine",
        )

    k = _bounded_k(args.get("k"))
    limit = _bounded_limit(args.get("limit"))
    identity_sql, identity_params = _build_identity_where(source=source, args=args)
    executed_method = _executed_method(method)
    sql = _build_kd_sql(source=source, field=field, method=executed_method, identity_sql=identity_sql)
    params = [_calendar_as_of(args), k, *identity_params]

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        metric_rows = [_normalize_kd_metric_row(row, field=field, method=method, k=k) for row in raw_rows]
        metric_rows = _filter_kd_rows(metric_rows, args=args)
        metric_rows = _sort_kd_rows(metric_rows, args=args)
        if limit > 0:
            metric_rows = metric_rows[:limit]
        rows = [_project_kd_row(row, outputs=outputs, columns=columns) for row in metric_rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            api_name=api_name,
            sql_shape={
                "as_of": _calendar_as_of(args),
                "window": "latest k source trade dates before as_of",
                "identity_filter": identity_sql,
                "field": field,
                "requested_method": method,
                "executed_method": executed_method,
                "k": k,
                "limit": limit,
            },
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(status="provider_error", source=source, args=args, columns=columns, rows=[], api_name=api_name, reason=str(exc))
    finally:
        db.close_db()


def _method_allowed(*, field: str, method: str) -> bool:
    return field in MARGIN_KD_FIELDS and bool(MARGIN_KD_METHOD_RE.fullmatch(str(method or "")))


def _executed_method(method: str) -> str:
    if method in MARGIN_KD_METHODS:
        return method
    return MARGIN_DEFAULT_METHOD


def _requested_columns(outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        token = _output_token(output)
        if token in MARGIN_SOURCE.fields and token not in columns:
            columns.append(token)
    return columns or ["code", "name", "tradedate", "financing_balance", "financing_buy", "financing_repay", "financing_net_buy", "margin_balance"]


def _requested_kd_columns(outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        name = _output_column_name(output)
        if name and name not in columns:
            columns.append(name)
    return columns or ["code", "name", "value"]


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[0].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _output_column_name(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        return text.split(" as ", 1)[1].strip()
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


def _bounded_k(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 5
    return max(1, min(parsed, 250))


def _calendar_as_of(args: Mapping[str, Any]) -> str:
    value = str(args.get("as_of") or args.get("asof") or "").strip()
    return value or date.today().isoformat()


def _build_where(*, source: MarginSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    exact_date = str(args.get("date") or args.get("tradedate") or "").strip()
    start = str(args.get("start") or args.get("start_date") or "").strip()
    end = str(args.get("end") or args.get("end_date") or "").strip()
    if exact_date:
        clauses.append(f"{source.fields['tradedate']} = %s")
        params.append(exact_date)
    elif start and end:
        clauses.append(f"{source.fields['tradedate']} BETWEEN %s AND %s")
        params.extend(sorted([start, end]))
    else:
        clauses.append(f"{source.fields['tradedate']} = (SELECT MAX(trade_date) FROM {source.table})")
    clauses.append("b.stk_code IS NOT NULL")

    filter_sql, filter_params = _build_filter_clauses(source=source, args=args, allowed=set(source.fields))
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return " AND ".join(clauses), params


def _build_identity_where(*, source: MarginSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    filter_sql, params = _build_filter_clauses(source=source, args=args, allowed={"code", "name"})
    if not filter_sql:
        return "", []
    return f"AND {filter_sql}", params


def _build_filter_clauses(*, source: MarginSource, args: Mapping[str, Any], allowed: set[str]) -> tuple[str, List[Any]]:
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
        if op not in OP_SQL:
            continue
        clauses.append(f"{prefix} {expression} {OP_SQL[op]} %s".strip())
        params.append(value)
    if not clauses:
        return "", []
    return f"({' '.join(clauses)})", params


def _explicit_filters(args: Mapping[str, Any]) -> List[tuple[str, str, str, Any]]:
    rows: List[tuple[str, str, str, Any]] = []
    for field_name in ["code", "name"]:
        value = args.get(field_name)
        if value not in (None, ""):
            rows.append(("AND", field_name, "=", value))
    for field_name in ["codes", "names"]:
        value = args.get(field_name)
        if value not in (None, ""):
            rows.append(("AND", field_name[:-1], "in", value))
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


def _build_order(*, source: MarginSource, args: Mapping[str, Any]) -> str:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return f"{source.fields['tradedate']} DESC"
    parts = re.split(r"\s+", text, maxsplit=1)
    field_name = parts[0].strip()
    direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
    expression = source.fields.get(field_name)
    if not expression:
        return f"{source.fields['tradedate']} DESC"
    direction_sql = "ASC" if direction.startswith("asc") else "DESC"
    return f"{expression} {direction_sql}, {source.fields['tradedate']} DESC"


def _build_sql(*, source: MarginSource, columns: List[str], where_sql: str, order_sql: str) -> str:
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    return f"""
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _build_kd_sql(*, source: MarginSource, field: str, method: str, identity_sql: str) -> str:
    if method in {"change", "pct_change"}:
        return _build_kd_change_sql(source=source, field=field, pct=(method == "pct_change"), identity_sql=identity_sql)
    if method == "median":
        return _build_kd_median_sql(source=source, field=field, identity_sql=identity_sql)
    agg = {"max": "MAX", "min": "MIN", "avg": "AVG", "sum": "SUM", "count": "COUNT"}.get(method) or "SUM"
    return f"""
        {_kd_dates_cte(source)}
        SELECT
            {source.fields['code']} AS `code`,
            {source.fields['name']} AS `name`,
            {agg}({source.fields[field]}) AS `value`,
            MAX({source.fields['tradedate']}) AS `end_date`,
            COUNT({source.fields[field]}) AS `window_count`
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
        AND b.stk_code IS NOT NULL
        {identity_sql}
        GROUP BY {source.fields['code']}, {source.fields['name']}
        HAVING `value` IS NOT NULL
    """


def _build_kd_change_sql(*, source: MarginSource, field: str, pct: bool, identity_sql: str) -> str:
    value_expr = (
        "(MAX(CASE WHEN tradedate = end_date THEN metric_value END) "
        "- MAX(CASE WHEN tradedate = start_date THEN metric_value END))"
    )
    if pct:
        value_expr = f"{value_expr} / NULLIF(MAX(CASE WHEN tradedate = start_date THEN metric_value END), 0) * 100"
    return f"""
        {_kd_dates_cte(source)}
        SELECT
            code,
            name,
            {value_expr} AS `value`,
            MAX(CASE WHEN tradedate = start_date THEN metric_value END) AS `start_value`,
            MAX(CASE WHEN tradedate = end_date THEN metric_value END) AS `current_value`,
            start_date,
            end_date,
            COUNT(metric_value) AS `window_count`
        FROM (
            SELECT
                base_rows.*,
                MIN(tradedate) OVER (PARTITION BY code) AS start_date,
                MAX(tradedate) OVER (PARTITION BY code) AS end_date
            FROM (
                SELECT
                    {source.fields['code']} AS `code`,
                    {source.fields['name']} AS `name`,
                    {source.fields['tradedate']} AS `tradedate`,
                    {source.fields[field]} AS `metric_value`
                FROM {source.table} q
                LEFT JOIN {source.base_table} b ON {source.join_on}
                WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
                AND b.stk_code IS NOT NULL
                {identity_sql}
            ) base_rows
            WHERE metric_value IS NOT NULL
        ) windowed
        GROUP BY code, name, start_date, end_date
        HAVING `value` IS NOT NULL
    """


def _build_kd_median_sql(*, source: MarginSource, field: str, identity_sql: str) -> str:
    return f"""
        {_kd_dates_cte(source)}
        SELECT
            code,
            name,
            AVG(metric_value) AS `value`,
            MAX(end_date) AS `end_date`,
            MAX(cnt) AS `window_count`
        FROM (
            SELECT
                base_rows.*,
                ROW_NUMBER() OVER (PARTITION BY code ORDER BY metric_value) AS rn,
                COUNT(*) OVER (PARTITION BY code) AS cnt,
                MAX(tradedate) OVER (PARTITION BY code) AS end_date
            FROM (
                SELECT
                    {source.fields['code']} AS `code`,
                    {source.fields['name']} AS `name`,
                    {source.fields['tradedate']} AS `tradedate`,
                    {source.fields[field]} AS `metric_value`
                FROM {source.table} q
                LEFT JOIN {source.base_table} b ON {source.join_on}
                WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
                AND b.stk_code IS NOT NULL
                {identity_sql}
            ) base_rows
            WHERE metric_value IS NOT NULL
        ) ranked
        WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
        GROUP BY code, name
        HAVING `value` IS NOT NULL
    """


def _kd_dates_cte(source: MarginSource) -> str:
    return f"""
        WITH kd_dates AS (
            SELECT DISTINCT trade_date
            FROM {source.table}
            WHERE trade_date < %s
            ORDER BY trade_date DESC
            LIMIT %s
        )
    """


def _normalize_kd_metric_row(row: Mapping[str, Any], *, field: str, method: str, k: int) -> Dict[str, Any]:
    return {
        "code": str(row.get("code") or "").strip(),
        "name": row.get("name"),
        "value": _normalize_value(row.get("value")),
        "start_value": _normalize_value(row.get("start_value")),
        "current_value": _normalize_value(row.get("current_value")),
        "k": k,
        "start_date": _normalize_value(row.get("start_date")),
        "end_date": _normalize_value(row.get("end_date")),
        "tradedate": _normalize_value(row.get("end_date")),
        "window_count": int(row.get("window_count") or 0),
        "method": f"kd_{field}_{method}",
        "source": MARGIN_SOURCE.table,
    }


def _filter_kd_rows(rows: List[Dict[str, Any]], *, args: Mapping[str, Any]) -> List[Dict[str, Any]]:
    filters = [item for item in _explicit_filters(args) if item[1] in {"value", "k", "start_value", "current_value", "start_date", "end_date", "tradedate"}]
    result = rows
    for _connector, field_name, op, expected in filters:
        if op not in OP_SQL:
            continue
        result = [row for row in result if _compare(row.get(field_name), op, expected)]
    return result


def _compare(left: Any, op: str, right: Any) -> bool:
    try:
        left_value: Any = float(left)
        right_value: Any = float(right)
    except (TypeError, ValueError):
        left_value = str(left or "")
        right_value = str(right or "")
    if op in {"=", "=="}:
        return left_value == right_value
    if op == "!=":
        return left_value != right_value
    if op == ">":
        return left_value > right_value
    if op == ">=":
        return left_value >= right_value
    if op == "<":
        return left_value < right_value
    if op == "<=":
        return left_value <= right_value
    return False


def _sort_kd_rows(rows: List[Dict[str, Any]], *, args: Mapping[str, Any]) -> List[Dict[str, Any]]:
    text = str(args.get("order") or "").strip()
    if not text or text.lower() == "none":
        return sorted(rows, key=lambda row: str(row.get("code") or ""))
    parts = re.split(r"\s+", text, maxsplit=1)
    field_name = parts[0].strip()
    direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
    reverse = not direction.startswith("asc")
    if field_name not in {"code", "name", "value", "k", "start_value", "current_value", "start_date", "end_date", "tradedate", "window_count"}:
        field_name = "value"
    return sorted(rows, key=lambda row: (row.get(field_name) is None, row.get(field_name)), reverse=reverse)


def _project_kd_row(row: Mapping[str, Any], *, outputs: List[str], columns: List[str]) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for output, column in zip(outputs, columns):
        projected[column] = _normalize_value(row.get(_output_token(output)))
    return projected


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
    source: MarginSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    api_name: str = "",
    reason: str = "",
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": api_name or f"{source.subject}.margin",
        "subject": source.subject,
        "source": [source.table, source.base_table],
        "arguments": dict(args),
        "columns": columns,
        "available_fields": MARGIN_FIELDS,
        "rows": rows,
        "row_count": len(rows),
    }
    if reason:
        result["reason"] = reason
    if sql_shape:
        result["sql_shape"] = dict(sql_shape)
    return result
