from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.experiments.staged_data_protocol.phase2.agg_protocol import AGG_METHODS, output_alias, parse_agg_spec
from src.utils.mysql_utils import StockInfoDbUtils


TRADE_CALENDAR_TABLE = "aiia_trade_calendar"
DEFAULT_MARKET_CODE = "CN_A"


@dataclass(frozen=True)
class QuoteSource:
    subject: str
    table: str
    base_table: str
    join_on: str
    fields: Mapping[str, str]


QUOTE_SOURCES: Dict[str, QuoteSource] = {
    "stock": QuoteSource(
        subject="stock",
        table="kcrp_stock_price",
        base_table="kcrp_stock_baseinfo",
        join_on="b.stk_code = q.stk_code",
        fields={
            "code": "q.stk_code",
            "name": "b.stk_name",
            "tradedate": "q.trade_date",
            "preclose": "q.preclose",
            "open": "q.open",
            "high": "q.high",
            "low": "q.low",
            "close": "q.close",
            "avg_price": "q.avg_price",
            "differ": "q.differ",
            "pct": "q.rise_fall_rate",
            "turn_ratio": "q.turn_ratio",
            "volumn": "q.volume",
            "amount": "q.amount",
            "amplitude": "q.amplitude",
            "adjpreclose": "q.adjpreclose",
            "adjopen": "q.adjopen",
            "adjhigh": "q.adjhigh",
            "adjlow": "q.adjlow",
            "adjclose": "q.adjclose",
            "is_limit_price": "q.is_limit_price",
        },
    ),
    "fund": QuoteSource(
        subject="fund",
        table="kcrp_fund_price",
        base_table="kcrp_fund_baseinfo",
        join_on="b.fund_code = q.fund_code",
        fields={
            "code": "q.fund_code",
            "name": "b.fund_name",
            "tradedate": "q.trade_date",
            "nav_unit": "q.nav_unit",
            "preclose": "q.preclose",
            "open": "q.open",
            "high": "q.high",
            "low": "q.low",
            "close": "q.close",
            "avg_price": "q.avg_price",
            "differ": "q.differ",
            "pct": "q.differ_range",
            "turn_ratio": "q.turn_ratio",
            "volumn": "q.volume",
            "amount": "q.amount",
            "amplitude": "q.amplitude",
            "discount": "q.discount",
            "unit_total": "q.unit_total",
        },
    ),
    "bond": QuoteSource(
        subject="bond",
        table="kcrp_bond_price",
        base_table="kcrp_bond_baseinfo",
        join_on="b.bond_code = q.bond_code",
        fields={
            "code": "q.bond_code",
            "name": "b.bond_name",
            "tradedate": "q.trade_date",
            "preclose": "q.preclose",
            "open": "q.open",
            "high": "q.high",
            "low": "q.low",
            "close": "q.close",
            "pct": "q.rise_fall_rate",
            "turn_ratio": "q.turn_ratio",
            "volumn": "q.volume",
            "amount": "q.amount",
        },
    ),
    "plate": QuoteSource(
        subject="plate",
        table="kcrp_yp_plate_price",
        base_table="kcrp_yp_plate",
        join_on="b.plate_code = q.plate_code",
        fields={
            "code": "q.plate_code",
            "name": "q.plate_name",
            "tradedate": "q.trade_date",
            "preclose": "NULL",
            "open": "q.open",
            "high": "q.high",
            "low": "q.low",
            "close": "q.close",
            "avg_price": "q.avg_price",
            "differ": "q.differ",
            "pct": "q.rise_fall_rate",
            "turn_ratio": "NULL",
            "volumn": "q.volume",
            "amount": "q.amount",
        },
    ),
    "index": QuoteSource(
        subject="index",
        table="kcrp_index_price",
        base_table="kcrp_index_base",
        join_on="b.idx_code = q.idx_code",
        fields={
            "code": "q.idx_code",
            "name": "q.index_short_name",
            "tradedate": "q.trade_date",
            "preclose": "q.preclose",
            "open": "q.open",
            "high": "q.high",
            "low": "q.low",
            "close": "q.close",
            "avg_price": "NULL",
            "differ": "NULL",
            "pct": "q.rise_fall_rate",
            "turn_ratio": "q.turn_ratio",
            "volumn": "q.volume",
            "amount": "q.amount",
        },
    ),
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


FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|=|==|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|=|==|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_quote_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = QUOTE_SOURCES.get(subject)
    if not source:
        return {
            "status": "unsupported",
            "subject": subject,
            "reason": "quote provider is only available for stock, fund, bond, plate, index",
            "rows": [],
        }
    requested_fields = _requested_fields(source=source, outputs=outputs)
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=requested_fields,
            rows=[],
            reason="filter contains result references that must be materialized by the execution engine",
            mocked_fields=_mocked_fields(source=source, columns=requested_fields),
        )

    limit = _bounded_limit(args.get("limit"))
    where_sql, params = _build_where(source=source, args=args)
    order_sql = _build_order(source=source, args=args)
    sql = _build_sql(source=source, fields=requested_fields, where_sql=where_sql, order_sql=order_sql)
    params.append(limit)

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        rows = [_normalize_row(row, requested_fields) for row in raw_rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=requested_fields,
            rows=rows,
            sql_shape=_sql_shape(where_sql=where_sql, order_sql=order_sql, limit=limit),
            mocked_fields=_mocked_fields(source=source, columns=requested_fields),
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(
            status="provider_error",
            source=source,
            args=args,
            columns=requested_fields,
            rows=[],
            reason=str(exc),
            mocked_fields=_mocked_fields(source=source, columns=requested_fields),
        )
    finally:
        db.close_db()


def execute_quote_agg_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = QUOTE_SOURCES.get(subject)
    api_name = f"{_quote_api_prefix(subject)}.agg"
    if not source:
        return {"status": "unsupported", "api": api_name, "subject": subject, "reason": "quote agg provider is only available for configured quote subjects", "rows": []}
    spec = parse_agg_spec(args)
    metric = _metric_column(spec.metric)
    agg = spec.method
    if metric not in source.fields:
        return _standard_result(status="provider_error", source=source, args=args, columns=_agg_columns(args=args, outputs=outputs), rows=[], api_name=api_name, reason=f"unsupported metric={metric}")
    if agg not in AGG_METHODS:
        return _standard_result(status="provider_error", source=source, args=args, columns=_agg_columns(args=args, outputs=outputs), rows=[], api_name=api_name, reason=f"unsupported agg={agg}")

    group_fields = [field for field in _field_list(args.get("group_by")) if field in source.fields]
    alias = _aggregate_alias(outputs, default=f"{agg}_{metric}", group_fields=group_fields)
    columns = [*group_fields, alias]
    select_parts = [f"{source.fields[field]} AS `{field}`" for field in group_fields]
    select_parts.append(f"{_agg_sql(source=source, agg=agg, metric=metric)} AS `{alias}`")
    group_sql = f"GROUP BY {', '.join(source.fields[field] for field in group_fields)}" if group_fields else ""
    where_sql, params = _build_where(source=source, args=args)
    order_sql = _agg_order(str(args.get("order") or ""), alias=alias)
    limit = _bounded_limit(args.get("limit"))
    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {where_sql}
        {group_sql}
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
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            api_name=api_name,
            sql_shape={"where": where_sql, "group_by": group_fields, "order": order_sql, "limit": limit},
            mocked_fields=_mocked_fields(source=source, columns=[metric]),
        )
    except Exception as exc:  # noqa: BLE001
        return _standard_result(status="provider_error", source=source, args=args, columns=columns, rows=[], api_name=api_name, reason=str(exc), mocked_fields=_mocked_fields(source=source, columns=[metric]))
    finally:
        db.close_db()


def execute_kd_quote_api(
    *,
    subject: str,
    field: str,
    method: str,
    args: Mapping[str, Any],
    outputs: List[str],
) -> Dict[str, Any]:
    source = QUOTE_SOURCES.get(subject)
    api_name = f"{_quote_api_prefix(subject)}.kd_{field}_{method}"
    if not source or field not in source.fields:
        return {
            "status": "unsupported",
            "api": api_name,
            "subject": subject,
            "reason": "kd quote provider is only available for configured quote fields",
            "rows": [],
        }
    if _has_unresolved_ref(args):
        columns = _requested_kd_columns(outputs)
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            api_name=api_name,
            reason="filter contains result references that must be materialized by the execution engine",
            mocked_fields=_mocked_fields(source=source, columns=columns),
        )

    k = _bounded_k(args.get("k"))
    limit = _bounded_limit(args.get("limit"))
    identity_sql, identity_params = _build_identity_where(source=source, args=args)
    sql = _build_kd_aggregate_sql(source=source, field=field, method=method, identity_sql=identity_sql)
    market_code = _calendar_market_code(args)
    as_of = _calendar_as_of(args)
    params = [market_code, market_code, as_of, k, *identity_params]
    if field == "amplitude" and method == "sum":
        params = [market_code, market_code, as_of, k, market_code, *identity_params]

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        metric_rows = [_normalize_kd_metric_row(row, source=source, field=field, method=method, k=k) for row in raw_rows]
        metric_rows = _filter_kd_rows(metric_rows, args=args)
        metric_rows = _sort_kd_rows(metric_rows, args=args)
        if limit > 0:
            metric_rows = metric_rows[:limit]
        columns = _requested_kd_columns(outputs)
        rows = [_project_kd_row(row, outputs=outputs, columns=columns) for row in metric_rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            api_name=api_name,
            sql_shape={
                "calendar": TRADE_CALENDAR_TABLE,
                "market_code": market_code,
                "as_of": as_of,
                "window_end": "previous trade day before as_of",
                "window": "latest k trade_seq dates ending at t-1",
                "identity_filter": identity_sql,
                "k": k,
                "limit": limit,
            },
            mocked_fields=_mocked_fields(source=source, columns=[field]),
        )
    except Exception as exc:  # noqa: BLE001 - experiment boundary should return structured failures.
        return _standard_result(
            status="provider_error",
            source=source,
            args=args,
            columns=_requested_kd_columns(outputs),
            rows=[],
            api_name=api_name,
            reason=str(exc),
            mocked_fields=_mocked_fields(source=source, columns=[field]),
        )
    finally:
        db.close_db()


def _requested_fields(*, source: QuoteSource, outputs: List[str]) -> List[str]:
    fields: List[str] = []
    for output in outputs:
        token = _output_token(output)
        if token in source.fields and token not in fields:
            fields.append(token)
    if fields:
        return fields
    return [field for field in ["code", "name", "tradedate", "open", "close", "high", "low", "pct", "amount", "volumn"] if field in source.fields]


def _quote_api_prefix(subject: str) -> str:
    return f"{subject}.quote"


def _metric_column(metric: str) -> str:
    text = str(metric or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _field_list(raw_fields: object) -> List[str]:
    if isinstance(raw_fields, (list, tuple, set)):
        return [str(item).strip() for item in raw_fields if str(item).strip()]
    return [item.strip() for item in str(raw_fields or "").split(",") if item.strip()]


def _agg_columns(*, args: Mapping[str, Any], outputs: List[str]) -> List[str]:
    group_fields = _field_list(args.get("group_by"))
    return [*group_fields, _aggregate_alias(outputs, default="value", group_fields=group_fields)]


def _aggregate_alias(outputs: List[str], *, default: str, group_fields: List[str] | None = None) -> str:
    return output_alias(outputs, default=default, exclude=set(group_fields or []))


def _agg_sql(*, source: QuoteSource, agg: str, metric: str) -> str:
    if agg == "count":
        return "COUNT(*)"
    if agg == "median":
        return f"AVG({source.fields[metric]})"
    return f"{agg.upper()}({source.fields[metric]})"


def _agg_order(raw_order: str, *, alias: str) -> str:
    text = str(raw_order or "").strip()
    if not text or text.lower() == "none":
        return f"`{alias}` DESC"
    parts = re.split(r"\s+", text, maxsplit=1)
    field_name = parts[0].strip("`")
    direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
    direction_sql = "ASC" if direction.startswith("asc") else "DESC"
    if re.fullmatch(r"[A-Za-z_]\w*", field_name):
        return f"`{field_name}` {direction_sql}"
    return f"`{alias}` DESC"


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


def _bounded_k(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 5
    return max(1, min(parsed, 250))


def _calendar_market_code(args: Mapping[str, Any]) -> str:
    return str(args.get("market_code") or args.get("market") or DEFAULT_MARKET_CODE).strip() or DEFAULT_MARKET_CODE


def _calendar_as_of(args: Mapping[str, Any]) -> str:
    value = str(args.get("as_of") or args.get("asof") or "").strip()
    return value or date.today().isoformat()


def _build_where(*, source: QuoteSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    start = str(args.get("start") or args.get("start_date") or "").strip()
    end = str(args.get("end") or args.get("end_date") or "").strip()
    exact_date = str(args.get("date") or args.get("tradedate") or "").strip()
    if exact_date:
        clauses.append(f"{source.fields['tradedate']} = %s")
        params.append(exact_date)
    elif start and end:
        clauses.append(f"{source.fields['tradedate']} BETWEEN %s AND %s")
        params.extend(sorted([start, end]))
    elif not _identity_time_series_request(source=source, args=args):
        clauses.append(f"{source.fields['tradedate']} = (SELECT MAX(trade_date) FROM {source.table})")

    filters = _explicit_filters(args)
    filter_clauses: List[str] = []
    filter_params: List[Any] = []
    for connector, field_name, op, value in filters:
        expression = source.fields.get(field_name)
        if not expression:
            continue
        prefix = connector if filter_clauses else ""
        if op == "in":
            values = _list_value(value)
            if not values:
                continue
            placeholders = ", ".join(["%s"] * len(values))
            filter_clauses.append(f"{prefix} {expression} IN ({placeholders})".strip())
            filter_params.extend(values)
            continue
        if op not in OP_SQL:
            continue
        filter_clauses.append(f"{prefix} {expression} {OP_SQL[op]} %s".strip())
        filter_params.append(value)
    if filter_clauses:
        clauses.append(f"({' '.join(filter_clauses)})")
        params.extend(filter_params)

    return " AND ".join(clauses), params


def _build_identity_where(*, source: QuoteSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for connector, field_name, op, value in _explicit_filters(args):
        if field_name not in {"code", "name"}:
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
        if op in {"=", "=="}:
            clauses.append(f"{prefix} {expression} = %s".strip())
            params.append(value)
    if not clauses:
        return "", []
    return f"AND ({' '.join(clauses)})", params


def _identity_time_series_request(*, source: QuoteSource, args: Mapping[str, Any]) -> bool:
    if _bounded_limit(args.get("limit")) <= 1:
        return False
    order = str(args.get("order") or "").lower()
    if "tradedate" not in order and "trade_date" not in order:
        return False
    for _connector, field_name, op, _value in _explicit_filters(args):
        if field_name in {"code", "name"} and op in {"=", "==", "in"} and field_name in source.fields:
            return True
    return False


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
        connector = str(match.group("connector") or "AND").upper()
        field_name = str(match.group("field") or "").strip()
        op = str(match.group("op") or "").strip().lower()
        value = _clean_value(str(match.group("value") or ""))
        rows.append((connector, field_name, op, value))
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


def _build_order(*, source: QuoteSource, args: Mapping[str, Any]) -> str:
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


def _build_sql(*, source: QuoteSource, fields: List[str], where_sql: str, order_sql: str) -> str:
    select_sql = ", ".join(f"{source.fields[field]} AS `{field}`" for field in fields)
    return f"""
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _build_kd_aggregate_sql(*, source: QuoteSource, field: str, method: str, identity_sql: str) -> str:
    if field == "pct" and method == "sum":
        return _build_kd_pct_sum_sql(source=source, identity_sql=identity_sql)
    if field == "amplitude" and method == "sum":
        return _build_kd_amplitude_sum_sql(source=source, identity_sql=identity_sql)
    if method == "median":
        return _build_kd_median_sql(source=source, field=field, identity_sql=identity_sql)
    agg = {
        "max": "MAX",
        "high": "MAX",
        "min": "MIN",
        "avg": "AVG",
        "sum": "SUM",
        "count": "COUNT",
    }.get(method)
    if not agg:
        return _build_kd_base_rows_sql(source=source, field=field, identity_sql=identity_sql)
    return f"""
        {_kd_dates_cte()}
        SELECT
            {source.fields['code']} AS `code`,
            {source.fields['name']} AS `name`,
            {agg}({source.fields[field]}) AS `value`,
            MAX({source.fields['tradedate']}) AS `end_date`,
            COUNT({source.fields[field]}) AS `window_count`
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
        {identity_sql}
        GROUP BY {source.fields['code']}, {source.fields['name']}
        HAVING `value` IS NOT NULL
    """


def _build_kd_pct_sum_sql(*, source: QuoteSource, identity_sql: str) -> str:
    return f"""
        {_kd_dates_cte()}
        SELECT
            code,
            name,
            (
                MAX(CASE WHEN tradedate = end_date THEN close_value END)
                - MAX(CASE WHEN tradedate = start_date THEN close_value END)
            ) / NULLIF(MAX(CASE WHEN tradedate = start_date THEN close_value END), 0) * 100 AS `value`,
            end_date,
            COUNT(close_value) AS `window_count`
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
                    {source.fields['close']} AS `close_value`
                FROM {source.table} q
                LEFT JOIN {source.base_table} b ON {source.join_on}
                WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
                {identity_sql}
            ) base_rows
        ) windowed
        GROUP BY code, name, start_date, end_date
        HAVING `value` IS NOT NULL
    """


def _build_kd_amplitude_sum_sql(*, source: QuoteSource, identity_sql: str) -> str:
    return f"""
        {_kd_dates_cte()},
        window_bounds AS (
            SELECT MIN(trade_date) AS start_date, MAX(trade_date) AS end_date
            FROM kd_dates
        ),
        prev_date AS (
            SELECT MAX(calendar_date) AS trade_date
            FROM {TRADE_CALENDAR_TABLE}
            WHERE market_code = %s
              AND is_trade_day = 1
              AND calendar_date < (SELECT start_date FROM window_bounds)
        )
        SELECT
            code,
            name,
            (
                MAX(CASE WHEN in_window = 1 THEN high_value END)
                - MIN(CASE WHEN in_window = 1 THEN low_value END)
            ) / NULLIF(MAX(CASE WHEN tradedate = (SELECT trade_date FROM prev_date) THEN close_value END), 0) * 100 AS `value`,
            MAX(CASE WHEN in_window = 1 THEN tradedate END) AS `end_date`,
            COUNT(CASE WHEN in_window = 1 THEN close_value END) AS `window_count`
        FROM (
            SELECT
                {source.fields['code']} AS `code`,
                {source.fields['name']} AS `name`,
                {source.fields['tradedate']} AS `tradedate`,
                {source.fields['high']} AS `high_value`,
                {source.fields['low']} AS `low_value`,
                {source.fields['close']} AS `close_value`,
                CASE WHEN {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates) THEN 1 ELSE 0 END AS `in_window`
            FROM {source.table} q
            LEFT JOIN {source.base_table} b ON {source.join_on}
            WHERE (
                {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
                OR {source.fields['tradedate']} = (SELECT trade_date FROM prev_date)
            )
            {identity_sql}
        ) base_rows
        GROUP BY code, name
        HAVING `value` IS NOT NULL
    """


def _build_kd_median_sql(*, source: QuoteSource, field: str, identity_sql: str) -> str:
    return f"""
        {_kd_dates_cte()}
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
                {identity_sql}
            ) base_rows
            WHERE metric_value IS NOT NULL
        ) ranked
        WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
        GROUP BY code, name
        HAVING `value` IS NOT NULL
    """


def _build_kd_base_rows_sql(*, source: QuoteSource, field: str, identity_sql: str) -> str:
    return f"""
        {_kd_dates_cte()}
        SELECT
            {source.fields['code']} AS `code`,
            {source.fields['name']} AS `name`,
            {source.fields[field]} AS `value`,
            MAX({source.fields['tradedate']}) AS `end_date`,
            COUNT({source.fields[field]}) AS `window_count`
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {source.fields['tradedate']} IN (SELECT trade_date FROM kd_dates)
        {identity_sql}
        GROUP BY {source.fields['code']}, {source.fields['name']}
        HAVING `value` IS NOT NULL
    """


def _kd_dates_cte() -> str:
    return f"""
        WITH kd_dates AS (
            SELECT calendar_date AS trade_date
            FROM {TRADE_CALENDAR_TABLE}
            WHERE market_code = %s
              AND is_trade_day = 1
              AND trade_seq <= (
                  SELECT trade_seq
                  FROM {TRADE_CALENDAR_TABLE}
                  WHERE market_code = %s
                    AND is_trade_day = 1
                    AND calendar_date < %s
                  ORDER BY calendar_date DESC
                  LIMIT 1
              )
            ORDER BY trade_seq DESC
            LIMIT %s
        )
    """


def _normalize_kd_metric_row(
    row: Mapping[str, Any],
    *,
    source: QuoteSource,
    field: str,
    method: str,
    k: int,
) -> Dict[str, Any]:
    return {
        "code": str(row.get("code") or "").strip(),
        "name": row.get("name"),
        "value": _normalize_value(row.get("value")),
        "k": k,
        "end_date": _normalize_value(row.get("end_date")),
        "tradedate": _normalize_value(row.get("end_date")),
        "window_count": int(row.get("window_count") or 0),
        "method": f"kd_{field}_{method}",
        "source": source.table,
    }


def _filter_kd_rows(rows: List[Dict[str, Any]], *, args: Mapping[str, Any]) -> List[Dict[str, Any]]:
    filters = [item for item in _explicit_filters(args) if item[1] in {"value", "k", "end_date", "tradedate"}]
    if not filters:
        return rows
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
        return sorted(rows, key=lambda row: (str(row.get("code") or "")))
    parts = re.split(r"\s+", text, maxsplit=1)
    field_name = parts[0].strip()
    direction = parts[1].strip().lower() if len(parts) > 1 else "desc"
    reverse = not direction.startswith("asc")
    if field_name not in {"code", "name", "value", "k", "end_date", "tradedate"}:
        field_name = "value"
    return sorted(rows, key=lambda row: (row.get(field_name) is None, row.get(field_name)), reverse=reverse)


def _requested_kd_columns(outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        name = _output_column_name(output)
        if name and name not in columns:
            columns.append(name)
    return columns or ["code", "name", "value"]


def _project_kd_row(row: Mapping[str, Any], *, outputs: List[str], columns: List[str]) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for output, column in zip(outputs, columns):
        token = _output_token(output)
        projected[column] = _normalize_value(row.get(token))
    return projected


def _output_column_name(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        return text.split(" as ", 1)[1].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _normalize_row(row: Mapping[str, Any], fields: List[str]) -> Dict[str, Any]:
    return {field: _normalize_value(row.get(field)) for field in fields}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _standard_result(
    *,
    status: str,
    source: QuoteSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    api_name: str = "",
    reason: str = "",
    sql_shape: Mapping[str, Any] | None = None,
    mocked_fields: List[str] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": api_name or _quote_api_prefix(source.subject),
        "subject": source.subject,
        "source": [source.table, source.base_table],
        "arguments": dict(args),
        "columns": columns,
        "available_fields": list(source.fields.keys()),
        "mocked_fields": mocked_fields or [],
        "rows": rows,
        "row_count": len(rows),
    }
    if reason:
        result["reason"] = reason
    if sql_shape:
        result["sql_shape"] = dict(sql_shape)
    return result


def _sql_shape(*, where_sql: str, order_sql: str, limit: int) -> Dict[str, Any]:
    return {"where": where_sql, "order": order_sql, "limit": limit}


def _mocked_fields(*, source: QuoteSource, columns: List[str]) -> List[str]:
    return [field for field in columns if source.fields.get(field) == "NULL"]
