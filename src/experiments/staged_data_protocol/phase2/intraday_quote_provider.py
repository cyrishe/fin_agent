from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping

import pymysql

from src.experiments.staged_data_protocol.phase2.agg_protocol import output_alias, parse_agg_spec
from src.utils.mysql_utils import StockInfoDbUtils


SNAPSHOT_TABLE = "aiia_stock_realtime_minute_snapshot"

FIELD_SQL = {
    "code": "code",
    "name": "name",
    "tradedate": "tradedate",
    "trade_date": "tradedate",
    "minute_index": "minute_index",
    "minute_time": "minute_time",
    "snapshot_time": "snapshot_time",
    "snapshot_slot": "snapshot_slot",
    "preclose": "preclose",
    "open": "open",
    "close": "close",
    "latest_price": "close",
    "high": "high",
    "low": "low",
    "differ": "differ",
    "pct": "pct",
    "amount": "amount",
    "volumn": "volumn",
    "volume": "volumn",
    "minute_amount": "minute_amount",
    "minute_volumn": "minute_volumn",
    "minute_volume": "minute_volumn",
    "source": "source",
    "is_fallback": "is_fallback",
}

NUMERIC_FIELDS = {
    "minute_index",
    "preclose",
    "open",
    "close",
    "high",
    "low",
    "differ",
    "pct",
    "amount",
    "volumn",
    "minute_amount",
    "minute_volumn",
}

AGG_METHODS = {"sum", "avg", "max", "min", "median", "count"}
KD_METHODS = {"sum", "avg", "max", "min", "median", "count", "percentile"}

FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|like|=|==|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|like|=|==|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_intraday_quote_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    columns = _requested_fields(outputs)
    limit = _bounded_limit(args.get("limit"), default=100)
    filters = _parse_filter(str(args.get("filter") or ""))
    slot = _resolve_slot(filters=filters, aggregate=False)
    where_sql, params = _where_sql(filters=filters, slot=slot)
    order_sql = _order_sql(str(args.get("order") or ""), default="tradedate DESC, minute_index DESC, code ASC")
    sql = f"""
        WITH base AS (
            SELECT
                s.stk_code AS code,
                s.stk_name AS name,
                s.trade_date AS tradedate,
                s.minute_index AS minute_index,
                TIME_FORMAT(s.snapshot_time, '%%H:%%i:%%s') AS minute_time,
                s.snapshot_time AS snapshot_time,
                s.snapshot_slot AS snapshot_slot,
                s.preclose_price AS preclose,
                s.open_price AS open,
                s.latest_price AS close,
                s.high_price AS high,
                s.low_price AS low,
                s.chg_value AS differ,
                s.chg_ratio AS pct,
                s.amount AS amount,
                s.volume AS volumn,
                GREATEST(s.amount - COALESCE(p.amount, 0), 0) AS minute_amount,
                GREATEST(s.volume - COALESCE(p.volume, 0), 0) AS minute_volumn,
                s.source AS source,
                s.is_fallback AS is_fallback
            FROM {SNAPSHOT_TABLE} s
            LEFT JOIN {SNAPSHOT_TABLE} p
              ON p.trade_date = s.trade_date
             AND p.stk_code = s.stk_code
             AND p.minute_index = s.minute_index - 1
            WHERE s.trade_date = %s
              AND s.stk_code REGEXP '^[0-9]{{6}}$'
        )
        SELECT {", ".join(f"`{column}`" for column in columns)}
        FROM base
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple([slot["trade_date"], *params, limit]))
            rows = [_normalize_row(row, columns) for row in cursor.fetchall()]
        return _result(
            status="ok",
            args=args,
            columns=columns,
            rows=rows,
            slot=slot,
            sql_shape={"where": where_sql, "order": order_sql, "limit": limit},
        )
    except Exception as exc:  # noqa: BLE001
        return _result(status="provider_error", args=args, columns=columns, rows=[], slot=slot, reason=str(exc))
    finally:
        db.close_db()


def execute_intraday_quote_agg_api(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    filters = _parse_filter(str(args.get("filter") or ""))
    slot = _resolve_slot(filters=filters, aggregate=True)
    where_sql, params = _where_sql(filters=filters, slot=slot)
    spec = parse_agg_spec(args)
    metric = _metric_column(spec.metric)
    agg = spec.method
    if metric not in FIELD_SQL:
        return _result(status="provider_error", args=args, columns=_agg_columns(args=args, outputs=outputs), rows=[], slot=slot, reason=f"unsupported metric={metric}")
    if agg not in AGG_METHODS:
        return _result(status="provider_error", args=args, columns=_agg_columns(args=args, outputs=outputs), rows=[], slot=slot, reason=f"unsupported agg={agg}")
    group_fields = [field for field in _field_list(args.get("group_by")) if field in FIELD_SQL]
    alias = _aggregate_alias(outputs, default=f"{agg}_{metric}", group_fields=group_fields)
    columns = [*group_fields, alias]
    select_parts = [f"`{field}`" for field in group_fields]
    group_sql = f"GROUP BY {', '.join(f'`{field}`' for field in group_fields)}" if group_fields else ""
    select_parts.append(f"{_agg_sql(agg, metric)} AS `{alias}`")
    order_sql = _order_sql(str(args.get("order") or ""), default=f"`{alias}` DESC")
    limit = _bounded_limit(args.get("limit"), default=100)
    sql = f"""
        WITH base AS (
            SELECT
                s.stk_code AS code,
                s.stk_name AS name,
                s.trade_date AS tradedate,
                s.minute_index AS minute_index,
                TIME_FORMAT(s.snapshot_time, '%%H:%%i:%%s') AS minute_time,
                s.snapshot_time AS snapshot_time,
                s.snapshot_slot AS snapshot_slot,
                s.preclose_price AS preclose,
                s.open_price AS open,
                s.latest_price AS close,
                s.high_price AS high,
                s.low_price AS low,
                s.chg_value AS differ,
                s.chg_ratio AS pct,
                s.amount AS amount,
                s.volume AS volumn,
                GREATEST(s.amount - COALESCE(p.amount, 0), 0) AS minute_amount,
                GREATEST(s.volume - COALESCE(p.volume, 0), 0) AS minute_volumn,
                s.source AS source,
                s.is_fallback AS is_fallback
            FROM {SNAPSHOT_TABLE} s
            LEFT JOIN {SNAPSHOT_TABLE} p
              ON p.trade_date = s.trade_date
             AND p.stk_code = s.stk_code
             AND p.minute_index = s.minute_index - 1
            WHERE s.trade_date = %s
              AND s.stk_code REGEXP '^[0-9]{{6}}$'
        )
        SELECT {", ".join(select_parts)}
        FROM base
        WHERE {where_sql}
        {group_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple([slot["trade_date"], *params, limit]))
            rows = [_normalize_row(row, columns) for row in cursor.fetchall()]
        return _result(
            status="ok",
            args=args,
            columns=columns,
            rows=rows,
            slot=slot,
            sql_shape={"where": where_sql, "group_by": group_fields, "order": order_sql, "limit": limit},
        )
    except Exception as exc:  # noqa: BLE001
        return _result(status="provider_error", args=args, columns=columns, rows=[], slot=slot, reason=str(exc))
    finally:
        db.close_db()


def execute_kd_intraday_quote_api(*, field: str, method: str, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    metric = _canonical_field(field)
    method = str(method or "").strip().lower()
    columns = _kd_columns(outputs)
    if metric not in FIELD_SQL or metric not in NUMERIC_FIELDS:
        return _result(status="provider_error", args=args, columns=columns, rows=[], slot={}, reason=f"unsupported field={field}")
    if method not in KD_METHODS:
        return _result(status="provider_error", args=args, columns=columns, rows=[], slot={}, reason=f"unsupported method={method}")

    filters = _parse_filter(str(args.get("filter") or ""))
    slot = _resolve_slot(filters=filters, aggregate=True)
    k = _bounded_k(args.get("k"))
    history_dates = _history_trade_dates(anchor_date=str(slot.get("trade_date") or ""), k=k)
    if not history_dates:
        return _result(status="ok", args=args, columns=columns, rows=[], slot=slot, reason="no historical intraday snapshots for same-minute comparison")

    raw_rows = _load_same_minute_rows(
        trade_dates=[str(slot["trade_date"]), *history_dates],
        minute_index=int(slot.get("minute_index") or 0),
    )
    current_rows = [row for row in raw_rows if str(row.get("tradedate")) == str(slot["trade_date"])]
    history_by_code: Dict[str, List[float]] = {}
    for row in raw_rows:
        if str(row.get("tradedate")) == str(slot["trade_date"]):
            continue
        code = str(row.get("code") or "")
        value = _number(row.get(metric))
        if value is not None:
            history_by_code.setdefault(code, []).append(value)

    result_rows: List[Dict[str, Any]] = []
    for row in current_rows:
        if not _matches_base_filters(row, filters):
            continue
        code = str(row.get("code") or "")
        history_values = history_by_code.get(code, [])
        current_value = _number(row.get(metric))
        value = _kd_value(method=method, current_value=current_value, history_values=history_values)
        change_ratio = (current_value / value) if current_value is not None and value not in (None, 0) else None
        change_pct = ((current_value - value) / value * 100) if current_value is not None and value not in (None, 0) else None
        item = {
            **row,
            "k": k,
            "current_value": current_value,
            "value": value,
            "change_ratio": change_ratio,
            "change_pct": change_pct,
            "window_count": len(history_values),
            "end_date": str(slot.get("trade_date") or ""),
            "tradedate": str(slot.get("trade_date") or ""),
        }
        if _matches_result_filters(item, filters):
            result_rows.append(item)

    result_rows = _sort_rows(result_rows, str(args.get("order") or "value desc"))
    limit = _bounded_limit(args.get("limit"), default=100)
    result_rows = result_rows[:limit]
    projected = [_project_row(row, outputs=outputs, columns=columns) for row in result_rows]
    payload = _result(
        status="ok",
        args=args,
        columns=columns,
        rows=projected,
        slot={**slot, "history_dates": history_dates},
        sql_shape={"same_minute_dates": len(history_dates) + 1, "metric": metric, "method": method, "limit": limit},
    )
    payload["metric"] = metric
    payload["method"] = method
    return payload


def _resolve_slot(*, filters: List[Dict[str, str]], aggregate: bool) -> Dict[str, Any]:
    requested_date = ""
    requested_minute = None
    requested_slot = ""
    for item in filters:
        field_name = _canonical_field(item["field"])
        op = item["op"].lower()
        value = _clean_value(item["value"])
        if field_name == "tradedate" and op in {"=", "=="} and not value.startswith("-"):
            requested_date = value[:10]
        elif field_name == "minute_index" and op in {"=", "=="}:
            try:
                requested_minute = int(value)
            except Exception:
                requested_minute = None
        elif field_name == "snapshot_slot" and op in {"=", "=="}:
            requested_slot = value

    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if requested_date:
                trade_date = requested_date
            else:
                cursor.execute(f"SELECT MAX(trade_date) AS trade_date FROM {SNAPSHOT_TABLE}")
                trade_date = str((cursor.fetchone() or {}).get("trade_date") or "")
            if requested_minute is not None:
                minute_index = requested_minute
            elif requested_slot:
                cursor.execute(
                    f"""
                    SELECT MAX(minute_index) AS minute_index
                    FROM {SNAPSHOT_TABLE}
                    WHERE trade_date = %s AND snapshot_slot = %s
                    """,
                    (trade_date, requested_slot),
                )
                minute_index = int((cursor.fetchone() or {}).get("minute_index") or 0)
            else:
                cursor.execute(
                    f"SELECT MAX(minute_index) AS minute_index FROM {SNAPSHOT_TABLE} WHERE trade_date = %s",
                    (trade_date,),
                )
                minute_index = int((cursor.fetchone() or {}).get("minute_index") or 0)
    finally:
        db.close_db()
    return {"trade_date": trade_date, "minute_index": minute_index, "snapshot_slot": requested_slot}


def _history_trade_dates(*, anchor_date: str, k: int) -> List[str]:
    if not anchor_date:
        return []
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                f"""
                SELECT trade_date
                FROM {SNAPSHOT_TABLE}
                WHERE trade_date < %s
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (anchor_date, k),
            )
            return [str(row.get("trade_date")) for row in cursor.fetchall() if row.get("trade_date")]
    finally:
        db.close_db()


def _load_same_minute_rows(*, trade_dates: List[str], minute_index: int) -> List[Dict[str, Any]]:
    if not trade_dates or minute_index <= 0:
        return []
    placeholders = ", ".join(["%s"] * len(trade_dates))
    sql = f"""
        SELECT
            s.stk_code AS code,
            s.stk_name AS name,
            s.trade_date AS tradedate,
            s.minute_index AS minute_index,
            TIME_FORMAT(s.snapshot_time, '%%H:%%i:%%s') AS minute_time,
            s.snapshot_time AS snapshot_time,
            s.snapshot_slot AS snapshot_slot,
            s.preclose_price AS preclose,
            s.open_price AS open,
            s.latest_price AS close,
            s.high_price AS high,
            s.low_price AS low,
            s.chg_value AS differ,
            s.chg_ratio AS pct,
            s.amount AS amount,
            s.volume AS volumn,
            GREATEST(s.amount - COALESCE(p.amount, 0), 0) AS minute_amount,
            GREATEST(s.volume - COALESCE(p.volume, 0), 0) AS minute_volumn,
            s.source AS source,
            s.is_fallback AS is_fallback
        FROM {SNAPSHOT_TABLE} s
        LEFT JOIN {SNAPSHOT_TABLE} p
          ON p.trade_date = s.trade_date
         AND p.stk_code = s.stk_code
         AND p.minute_index = s.minute_index - 1
        WHERE s.trade_date IN ({placeholders})
          AND s.minute_index = %s
          AND s.stk_code REGEXP '^[0-9]{{6}}$'
    """
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple([*trade_dates, minute_index]))
            return [_normalize_row(row, list(FIELD_SQL)) for row in cursor.fetchall()]
    finally:
        db.close_db()


def _where_sql(*, filters: List[Dict[str, str]], slot: Mapping[str, Any]) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if int(slot.get("minute_index") or 0) > 0:
        clauses.append("minute_index = %s")
        params.append(int(slot["minute_index"]))
    for item in filters:
        field_name = _canonical_field(item["field"])
        if field_name in {"tradedate", "minute_index"}:
            continue
        if field_name not in FIELD_SQL:
            continue
        op = item["op"].lower()
        sql_op = "=" if op == "==" else op.upper()
        raw_value = item["value"]
        connector = "OR" if item.get("connector", "").lower().strip() == "or" else "AND"
        prefix = connector if clauses else ""
        if op == "in":
            values = [_normalize_filter_value(field_name, value) for value in _list_values(raw_value)]
            if not values:
                continue
            clauses.append(f"{prefix} `{field_name}` IN ({', '.join(['%s'] * len(values))})".strip())
            params.extend(values)
            continue
        value = _normalize_filter_value(field_name, _clean_value(raw_value))
        clauses.append(f"{prefix} `{field_name}` {sql_op} %s".strip())
        params.append(value)
    return (" ".join(clauses) if clauses else "1=1"), params


def _parse_filter(filter_text: str) -> List[Dict[str, str]]:
    return [
        {
            "connector": str(match.group("connector") or ""),
            "field": str(match.group("field") or ""),
            "op": str(match.group("op") or ""),
            "value": str(match.group("value") or ""),
        }
        for match in FILTER_RE.finditer(str(filter_text or ""))
    ]


def _requested_fields(outputs: Iterable[str]) -> List[str]:
    fields: List[str] = []
    for output in outputs:
        token = _output_token(output)
        if token in FIELD_SQL and token not in fields:
            fields.append(token)
    return fields or ["code", "name", "tradedate", "minute_index", "snapshot_slot", "close", "pct", "amount", "volumn"]


def _output_token(output: str) -> str:
    text = str(output or "").strip()
    if " as " in text:
        text = text.split(" as ", 1)[1].strip()
    if "(" in text and ")" in text:
        return ""
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return _canonical_field(text)


def _order_sql(raw_order: str, *, default: str) -> str:
    parts: List[str] = []
    for item in str(raw_order or "").split(","):
        tokens = item.strip().split()
        if not tokens:
            continue
        field_name = _canonical_field(tokens[0].strip("`"))
        direction = tokens[1].upper() if len(tokens) > 1 and tokens[1].lower() in {"asc", "desc"} else "ASC"
        if field_name in FIELD_SQL or re.fullmatch(r"[A-Za-z_]\w*", field_name):
            parts.append(f"`{field_name}` {direction}")
    return ", ".join(parts) if parts else default


def _field_list(raw_fields: object) -> List[str]:
    if isinstance(raw_fields, (list, tuple, set)):
        return [_canonical_field(item) for item in raw_fields if str(item).strip()]
    return [_canonical_field(item) for item in str(raw_fields or "").split(",") if item.strip()]


def _metric_column(metric: str) -> str:
    text = str(metric or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return _canonical_field(text)


def _aggregate_alias(outputs: List[str], *, default: str, group_fields: List[str] | None = None) -> str:
    return output_alias(outputs, default=default, exclude=set(group_fields or []))


def _agg_sql(agg: str, metric: str) -> str:
    if agg == "count":
        return "COUNT(*)"
    if agg == "median":
        # MySQL 5.7-compatible fallback: provider will rarely need grouped median.
        return f"AVG(`{metric}`)"
    return f"{agg.upper()}(`{metric}`)"


def _agg_columns(*, args: Mapping[str, Any], outputs: List[str]) -> List[str]:
    group_fields = _field_list(args.get("group_by"))
    return [*group_fields, _aggregate_alias(outputs, default="value", group_fields=group_fields)]


def _kd_columns(outputs: List[str]) -> List[str]:
    columns: List[str] = []
    for output in outputs:
        text = str(output or "").strip()
        if " as " in text:
            token = text.split(" as ", 1)[1].strip()
        elif "." in text:
            token = text.rsplit(".", 1)[-1].strip()
        else:
            token = text
        if token and token not in columns:
            columns.append(token)
    return columns or ["code", "name", "tradedate", "minute_index", "snapshot_slot", "current_value", "value", "window_count"]


def _normalize_row(row: Mapping[str, Any], columns: List[str]) -> Dict[str, Any]:
    return {column: _normalize_value(row.get(column)) for column in columns}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def _normalize_filter_value(field_name: str, value: str) -> Any:
    if field_name == "code":
        return _code6(value)
    if field_name in NUMERIC_FIELDS:
        try:
            return float(value)
        except Exception:
            return value
    return value


def _matches_base_filters(row: Mapping[str, Any], filters: List[Dict[str, str]]) -> bool:
    return _matches_filters(row, [item for item in filters if _canonical_field(item["field"]) not in {"tradedate", "minute_index", "snapshot_slot", "value", "current_value", "window_count"}])


def _matches_result_filters(row: Mapping[str, Any], filters: List[Dict[str, str]]) -> bool:
    return _matches_filters(row, [item for item in filters if _canonical_field(item["field"]) in {"value", "current_value", "change_ratio", "change_pct", "window_count"}])


def _matches_filters(row: Mapping[str, Any], filters: List[Dict[str, str]]) -> bool:
    result = True
    for item in filters:
        field_name = _canonical_field(item["field"])
        if field_name not in row:
            continue
        op = item["op"].lower()
        actual = row.get(field_name)
        expected_raw = _clean_value(item["value"])
        if op == "in":
            expected_values = [_normalize_filter_value(field_name, value) for value in _list_values(item["value"])]
            matched = actual in expected_values
        else:
            expected = _normalize_filter_value(field_name, expected_raw)
            matched = _compare(actual, op, expected)
        if item.get("connector", "").lower().strip() == "or":
            result = result or matched
        else:
            result = result and matched
    return result


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op in {"=", "=="}:
        return str(actual) == str(expected)
    if op == "!=":
        return str(actual) != str(expected)
    if op == "like":
        return str(expected).strip("%") in str(actual)
    actual_num = _number(actual)
    expected_num = _number(expected)
    if actual_num is None or expected_num is None:
        return False
    if op == ">":
        return actual_num > expected_num
    if op == ">=":
        return actual_num >= expected_num
    if op == "<":
        return actual_num < expected_num
    if op == "<=":
        return actual_num <= expected_num
    return False


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _kd_value(*, method: str, current_value: float | None, history_values: List[float]) -> float | None:
    values = [value for value in history_values if value is not None]
    if not values:
        return None
    if method == "sum":
        return sum(values)
    if method == "avg":
        return sum(values) / len(values)
    if method == "max":
        return max(values)
    if method == "min":
        return min(values)
    if method == "median":
        return float(median(values))
    if method == "percentile":
        if current_value is None:
            return None
        return sum(1 for value in values if value <= current_value) / len(values)
    if method == "count":
        return float(len(values))
    return None


def _sort_rows(rows: List[Dict[str, Any]], raw_order: str) -> List[Dict[str, Any]]:
    parts = [item.strip().split() for item in str(raw_order or "").split(",") if item.strip()]
    if not parts:
        return rows
    sorted_rows = list(rows)
    for tokens in reversed(parts):
        field_name = _canonical_field(tokens[0])
        reverse = len(tokens) > 1 and tokens[1].lower() == "desc"
        sorted_rows.sort(key=lambda row: (row.get(field_name) is None, row.get(field_name)), reverse=reverse)
    return sorted_rows


def _project_row(row: Mapping[str, Any], *, outputs: List[str], columns: List[str]) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for output, column in zip(outputs or columns, columns):
        text = str(output or "").strip()
        source = text.split(" as ", 1)[0].strip() if " as " in text else text
        if "." in source:
            source = source.rsplit(".", 1)[-1]
        projected[column] = _normalize_value(row.get(_canonical_field(source)))
    if not projected:
        projected = {column: _normalize_value(row.get(_canonical_field(column))) for column in columns}
    return projected


def _list_values(raw: str) -> List[str]:
    text = _clean_value(raw)
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return [_clean_value(item) for item in text.split(",") if _clean_value(item)]


def _clean_value(value: str) -> str:
    text = str(value or "").strip()
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        text = text[1:-1]
    return text.strip()


def _canonical_field(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "trade_date": "tradedate",
        "date": "tradedate",
        "volume": "volumn",
        "minute_volume": "minute_volumn",
        "latest_price": "close",
    }
    return aliases.get(text, text)


def _code6(value: str) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else text


def _bounded_limit(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    if parsed < 0:
        return 10000
    return max(1, min(parsed, 10000))


def _bounded_k(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 5
    return max(1, min(parsed, 60))


def _result(
    *,
    status: str,
    args: Mapping[str, Any],
    columns: List[str],
    rows: List[Dict[str, Any]],
    slot: Mapping[str, Any],
    reason: str = "",
    sql_shape: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "source": ["aiia_stock_realtime_minute_snapshot"],
        "api": "stock.quote",
        "arguments": dict(args),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "slot": dict(slot),
    }
    if reason:
        payload["reason"] = reason
    if sql_shape:
        payload["sql_shape"] = dict(sql_shape)
    return payload
