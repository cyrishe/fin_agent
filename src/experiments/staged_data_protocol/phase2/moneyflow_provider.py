from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping

import pymysql

from src.experiments.staged_data_protocol.phase2.quote_provider import DEFAULT_MARKET_CODE, TRADE_CALENDAR_TABLE
from src.utils.mysql_utils import StockInfoDbUtils


@dataclass(frozen=True)
class MoneyflowSource:
    subject: str
    table: str
    base_table: str
    join_on: str
    fields: Mapping[str, str]


MONEYFLOW_FIELDS = [
    "code",
    "name",
    "tradedate",
    "total",
    "total_buy",
    "total_sell",
    "total_net",
    "huge",
    "huge_buy",
    "huge_sell",
    "huge_net",
    "huge_ratio",
    "large",
    "large_buy",
    "large_sell",
    "large_net",
    "large_ratio",
    "medium",
    "medium_buy",
    "medium_sell",
    "medium_net",
    "medium_ratio",
    "small",
    "small_buy",
    "small_sell",
    "small_net",
    "small_ratio",
    "main",
    "main_buy",
    "main_sell",
    "main_net",
    "main_ratio",
]


MONEYFLOW_SOURCES: Dict[str, MoneyflowSource] = {
    "stock": MoneyflowSource(
        subject="stock",
        table="kcrp_stock_moneyflow",
        base_table="kcrp_stock_baseinfo",
        join_on="b.stk_code = q.stk_code",
        fields={
            "code": "q.stk_code",
            "name": "b.stk_name",
            "tradedate": "q.trade_date",
            "total": "NULL",
            "total_buy": "NULL",
            "total_sell": "NULL",
            "total_net": "NULL",
            "huge": "NULL",
            "huge_buy": "q.huge_buy_value",
            "huge_sell": "q.huge_sell_value",
            "huge_net": "NULL",
            "huge_ratio": "q.huge_net_buy_value_ratio",
            "large": "NULL",
            "large_buy": "q.large_buy_value",
            "large_sell": "q.large_sell_value",
            "large_net": "q.large_net_buy_value",
            "large_ratio": "q.large_net_buy_value_ratio",
            "medium": "NULL",
            "medium_buy": "q.medium_buy_value",
            "medium_sell": "q.medium_sell_value",
            "medium_net": "NULL",
            "medium_ratio": "NULL",
            "small": "NULL",
            "small_buy": "q.small_buy_value",
            "small_sell": "q.small_sell_value",
            "small_net": "NULL",
            "small_ratio": "NULL",
            "main": "NULL",
            "main_buy": "q.main_buy_value",
            "main_sell": "q.main_sell_value",
            "main_net": "q.main_net_buy_value",
            "main_ratio": "q.main_net_buy_value_ratio",
        },
    ),
    "plate": MoneyflowSource(
        subject="plate",
        table="kcrp_yp_plate_moneyflow",
        base_table="kcrp_yp_plate",
        join_on="b.plate_code = q.plate_code",
        fields={
            "code": "q.plate_code",
            "name": "q.plate_name",
            "tradedate": "q.trade_date",
            "total": "NULL",
            "total_buy": "NULL",
            "total_sell": "NULL",
            "total_net": "NULL",
            "huge": "NULL",
            "huge_buy": "q.sbl_btm",
            "huge_sell": "q.sbl_stm",
            "huge_net": "q.sbl_btm_net",
            "huge_ratio": "q.sbl_btm_net_pct",
            "large": "NULL",
            "large_buy": "q.bl_btm",
            "large_sell": "q.bl_stm",
            "large_net": "q.bl_btm_net",
            "large_ratio": "q.bl_btm_net_pct",
            "medium": "NULL",
            "medium_buy": "q.ml_btm",
            "medium_sell": "q.ml_stm",
            "medium_net": "q.ml_btm_net",
            "medium_ratio": "q.ml_btm_net_pct",
            "small": "NULL",
            "small_buy": "q.sl_btm",
            "small_sell": "q.sl_stm",
            "small_net": "q.sl_btm_net",
            "small_ratio": "q.sl_btm_net_pct",
            "main": "NULL",
            "main_buy": "q.main_btm",
            "main_sell": "q.main_stm",
            "main_net": "q.main_btm_net",
            "main_ratio": "q.main_btm_net_pct",
        },
    ),
}


BUCKETS = {"huge", "large", "medium", "small", "main", "total"}
BUCKET_SUFFIXES = {
    "total": ["buy", "sell", "net"],
    "huge": ["buy", "sell", "net"],
    "large": ["buy", "sell", "net"],
    "medium": ["buy", "sell", "net"],
    "small": ["buy", "sell", "net"],
    "main": ["buy", "sell", "net"],
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


def execute_moneyflow_api(*, subject: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    source = MONEYFLOW_SOURCES.get(subject) or _mock_source(subject)
    columns = _requested_columns(source=source, outputs=outputs)
    ignored_filters = _ignored_filters(source=source, args=args)
    if not source.table:
        return _standard_result(status="ok", source=source, args=args, columns=columns, rows=[], mocked_fields=columns, ignored_filters=ignored_filters)
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
            mocked_fields=_mocked_fields(source=source, columns=columns),
            ignored_filters=ignored_filters,
            sql_shape={"where": where_sql, "order": order_sql, "limit": limit},
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


def execute_kd_moneyflow_api(
    *,
    subject: str,
    field: str,
    method: str,
    args: Mapping[str, Any],
    outputs: List[str],
) -> Dict[str, Any]:
    source = MONEYFLOW_SOURCES.get(subject) or _mock_source(subject)
    api_name = f"{subject}.moneyflow.kd_{field}_{method}"
    columns = _requested_kd_columns(outputs)
    if not source.table or field not in source.fields:
        return _standard_result(status="ok", source=source, args=args, columns=columns, rows=[], api_name=api_name, mocked_fields=columns)
    if _has_unresolved_ref(args):
        return _standard_result(
            status="requires_upstream_values",
            source=source,
            args=args,
            columns=columns,
            rows=[],
            api_name=api_name,
            mocked_fields=_mocked_fields(source=source, columns=[field]),
            reason="filter contains result references that must be materialized by the execution engine",
        )

    k = _bounded_k(args.get("k"))
    limit = _bounded_limit(args.get("limit"))
    identity_sql, identity_params = _build_identity_where(source=source, args=args)
    sql = _build_kd_sql(source=source, field=field, method=method, identity_sql=identity_sql)
    market_code = _calendar_market_code(args)
    as_of = _calendar_as_of(args)
    params = [market_code, market_code, as_of, k, *identity_params]

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        metric_rows = [_normalize_kd_metric_row(row, source=source, field=field, method=method, k=k) for row in raw_rows]
        metric_rows = _filter_kd_rows(metric_rows, args=args)
        metric_rows = _sort_kd_rows(metric_rows, args=args)[:limit]
        rows = [_project_kd_row(row, outputs=outputs, columns=columns) for row in metric_rows]
        return _standard_result(
            status="ok",
            source=source,
            args=args,
            columns=columns,
            rows=rows,
            api_name=api_name,
            mocked_fields=_mocked_fields(source=source, columns=[field]),
            sql_shape={
                "calendar": TRADE_CALENDAR_TABLE,
                "market_code": market_code,
                "as_of": as_of,
                "window": "latest k trade_seq dates ending at t-1",
                "identity_filter": identity_sql,
                "k": k,
                "limit": limit,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _standard_result(status="provider_error", source=source, args=args, columns=columns, rows=[], api_name=api_name, reason=str(exc))
    finally:
        db.close_db()


def _mock_source(subject: str) -> MoneyflowSource:
    return MoneyflowSource(subject=subject, table="", base_table="", join_on="", fields={field: "NULL" for field in MONEYFLOW_FIELDS})


def _requested_columns(*, source: MoneyflowSource, outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        token = _output_token(output)
        if token in BUCKETS:
            for column in _bucket_columns(source=source, bucket=token):
                if column not in columns:
                    columns.append(column)
            continue
        if token in source.fields and source.fields[token] == "NULL":
            for column in _fallback_columns_for_missing_field(source=source, field_name=token):
                if column not in columns:
                    columns.append(column)
            continue
        if token in source.fields and token not in columns:
            columns.append(token)
    return columns or ["code", "name", "tradedate", *_bucket_columns(source=source, bucket="total")]


def _fallback_columns_for_missing_field(*, source: MoneyflowSource, field_name: str) -> List[str]:
    if "_" not in field_name:
        return [field_name]
    bucket, suffix = field_name.split("_", 1)
    if bucket not in BUCKETS or suffix not in {"net", "ratio"}:
        return [field_name]
    fallback = [f"{bucket}_buy", f"{bucket}_sell"]
    available = [column for column in fallback if source.fields.get(column) and source.fields[column] != "NULL"]
    return available or [field_name]


def _bucket_columns(*, source: MoneyflowSource, bucket: str) -> List[str]:
    columns: List[str] = []
    for suffix in BUCKET_SUFFIXES.get(bucket, []):
        field_name = f"{bucket}_{suffix}"
        if field_name in source.fields and source.fields[field_name] != "NULL":
            columns.append(field_name)
    if columns:
        return columns
    return [field_name for field_name in [f"{bucket}_buy", f"{bucket}_sell", f"{bucket}_net"] if field_name in source.fields]


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


def _calendar_market_code(args: Mapping[str, Any]) -> str:
    return str(args.get("market_code") or args.get("market") or DEFAULT_MARKET_CODE).strip() or DEFAULT_MARKET_CODE


def _calendar_as_of(args: Mapping[str, Any]) -> str:
    value = str(args.get("as_of") or args.get("asof") or "").strip()
    return value or date.today().isoformat()


def _build_where(*, source: MoneyflowSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
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

    filter_sql, filter_params = _build_filter_clauses(source=source, args=args, allowed=set(source.fields))
    if filter_sql:
        clauses.append(filter_sql)
        params.extend(filter_params)
    return " AND ".join(clauses), params


def _build_identity_where(*, source: MoneyflowSource, args: Mapping[str, Any]) -> tuple[str, List[Any]]:
    filter_sql, params = _build_filter_clauses(source=source, args=args, allowed={"code", "name"})
    if not filter_sql:
        return "", []
    return f"AND {filter_sql}", params


def _build_filter_clauses(*, source: MoneyflowSource, args: Mapping[str, Any], allowed: set[str]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for connector, field_name, op, value in _explicit_filters(args):
        if field_name not in allowed:
            continue
        expression = source.fields.get(field_name)
        if not expression:
            continue
        if expression == "NULL":
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


def _ignored_filters(*, source: MoneyflowSource, args: Mapping[str, Any]) -> List[str]:
    ignored: List[str] = []
    for _connector, field_name, op, value in _explicit_filters(args):
        if source.fields.get(field_name) == "NULL":
            ignored.append(f"{field_name} {op} {value}")
    return ignored


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


def _build_order(*, source: MoneyflowSource, args: Mapping[str, Any]) -> str:
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


def _build_sql(*, source: MoneyflowSource, columns: List[str], where_sql: str, order_sql: str) -> str:
    select_sql = ", ".join(f"{source.fields[column]} AS `{column}`" for column in columns)
    return f"""
        SELECT {select_sql}
        FROM {source.table} q
        LEFT JOIN {source.base_table} b ON {source.join_on}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """


def _build_kd_sql(*, source: MoneyflowSource, field: str, method: str, identity_sql: str) -> str:
    if method == "median":
        return _build_kd_median_sql(source=source, field=field, identity_sql=identity_sql)
    agg = {"max": "MAX", "min": "MIN", "avg": "AVG", "sum": "SUM", "count": "COUNT"}.get(method)
    if not agg:
        agg = "SUM"
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


def _build_kd_median_sql(*, source: MoneyflowSource, field: str, identity_sql: str) -> str:
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


def _normalize_kd_metric_row(row: Mapping[str, Any], *, source: MoneyflowSource, field: str, method: str, k: int) -> Dict[str, Any]:
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
    source: MoneyflowSource,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    api_name: str = "",
    mocked_fields: List[str] | None = None,
    ignored_filters: List[str] | None = None,
    reason: str = "",
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": status,
        "api": api_name or f"{source.subject}.moneyflow",
        "subject": source.subject,
        "source": [item for item in [source.table, source.base_table] if item],
        "arguments": dict(args),
        "columns": columns,
        "available_fields": MONEYFLOW_FIELDS,
        "mocked_fields": mocked_fields or [],
        "ignored_filters": ignored_filters or [],
        "rows": rows,
        "row_count": len(rows),
    }
    if reason:
        result["reason"] = reason
    if sql_shape:
        result["sql_shape"] = dict(sql_shape)
    return result


def _mocked_fields(*, source: MoneyflowSource, columns: List[str]) -> List[str]:
    return [field for field in columns if source.fields.get(field) == "NULL"]
