from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping

import pymysql

from src.experiments.staged_data_protocol.phase2.agg_protocol import output_alias, parse_agg_spec
from src.utils.mysql_utils import StockInfoDbUtils


SNAPSHOT_TABLE = "aiia_stock_realtime_minute_snapshot"
# Kept as a shared session bound for same-minute comparison paths.
MINUTE_SESSION_START = "09:30:00"
MINUTE_SESSION_END = "15:00:00"
SUPPORTED_MINUTE_PERIODS = {1, 3, 5, 10, 15, 30, 60}
DEFAULT_LATEST_QUOTE_LIMIT = 100
DEFAULT_LATEST_QUOTE_HARD_LIMIT = 10_000
DEFAULT_INTRADAY_HARD_ROW_LIMIT = 500_000

FIELD_SQL = {
    "code": "code",
    "name": "name",
    "tradedate": "tradedate",
    "trade_date": "tradedate",
    "minute_index": "minute_index",
    "minute_time": "minute_time",
    "snapshot_time": "snapshot_time",
    "snapshot_slot": "snapshot_slot",
    "bar_start_time": "bar_start_time",
    "bar_end_time": "bar_end_time",
    "kline_type": "kline_type",
    "period_minutes": "period_minutes",
    "source_bar_count": "source_bar_count",
    "is_finalized": "is_finalized",
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
    "period_minutes",
    "source_bar_count",
}

AGG_METHODS = {"sum", "avg", "max", "min", "median", "count"}
KD_METHODS = {"sum", "avg", "max", "min", "median", "count", "percentile"}

FILTER_RE = re.compile(
    r"(?:(?P<connector>\band\b|\bor\b)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)\s*"
    r"(?P<op>in|like|==|=|!=|>=|<=|>|<)\s*"
    r"(?P<value>\[[^\]]+\]|\([^)]+\)|[^,;]+?)"
    r"(?=\s+(?:and|or)\s+[A-Za-z_]\w*\s*(?:in|like|==|=|!=|>=|<=|>|<)|[,;]|$)",
    flags=re.IGNORECASE,
)


def execute_intraday_quote_api(
    *,
    args: Mapping[str, Any],
    outputs: List[str],
    latest_only: bool = False,
) -> Dict[str, Any]:
    return (
        _execute_latest_daily_quote_per_code(args=args, outputs=outputs)
        if latest_only
        else _execute_stored_minute_bars(args=args, outputs=outputs)
    )


def _execute_stored_minute_bars(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    """Read the source system's fixed-period bars directly, without resampling."""
    columns = _requested_fields(outputs)
    period = _minute_period(args.get("period"))
    count = _minute_bar_count(args.get("count"), fallback=args.get("limit", 240))
    if period not in SUPPORTED_MINUTE_PERIODS:
        return _result(
            status="provider_error",
            args=args,
            columns=columns,
            rows=[],
            slot={"mode": 1},
            reason=f"period must be one of {sorted(SUPPORTED_MINUTE_PERIODS)}",
        )

    filters = _filters_from_args(args)
    slot = {
        "mode": 1,
        "period": period,
        "count": count,
    }
    source_where_sql, source_params = _snapshot_identity_where(filters)
    date_sql, date_params = _trade_date_predicate(filters)
    where_sql, where_params = _where_sql(filters=filters, slot={})
    order_sql = _order_sql(str(args.get("order") or ""), default="code ASC, tradedate ASC, bar_end_time ASC")
    hard_row_limit = _intraday_hard_row_limit()
    sql = f"""
        {_stored_bar_cte(
            kline_type=f"{period}m",
            period_minutes=period,
            source_where_sql=source_where_sql,
            date_sql=date_sql,
        )},
        recent AS (
            SELECT
                base.*,
                ROW_NUMBER() OVER (
                    PARTITION BY code
                    ORDER BY tradedate DESC, bar_end_time DESC, snapshot_time DESC
                ) AS recent_row
            FROM base
        )
        SELECT {", ".join(f"`{column}`" for column in columns)}
        FROM recent
        WHERE recent_row <= %s
          AND {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                sql,
                tuple([*source_params, *date_params, count, *where_params, hard_row_limit + 1]),
            )
            bars = [dict(row) for row in cursor.fetchall()]
    except Exception as exc:  # noqa: BLE001
        return _result(status="provider_error", args=args, columns=columns, rows=[], slot=slot, reason=str(exc))
    finally:
        db.close_db()
    if len(bars) > hard_row_limit:
        return _result(
            status="result_too_large",
            args=args,
            columns=columns,
            rows=[],
            slot=slot,
            reason=(
                "matching minute K rows exceed the safety limit "
                f"({hard_row_limit}); reduce count or the target list, or raise "
                "FIN_AGENT_INTRADAY_HARD_ROW_LIMIT for an authorized bulk run"
            ),
        )
    if "limit" in args and "count" in args:
        bars = bars[: _bounded_limit(args.get("limit"), default=len(bars) or 1)]
    rows = [_normalize_row(row, columns) for row in bars]
    returned_dates = [row.get("tradedate") for row in bars if row.get("tradedate")]
    if returned_dates:
        slot["latest_trade_date"] = str(max(returned_dates))
    return _result(
        status="ok",
        args=args,
        columns=columns,
        rows=rows,
        slot=slot,
        sql_shape={
            "mode": 1,
            "period": period,
            "count_per_code": count,
            "source": "stored_fixed_period_kline",
            "where": where_sql,
            "order": order_sql,
            "hard_row_limit": hard_row_limit,
        },
    )


def _execute_latest_daily_quote_per_code(*, args: Mapping[str, Any], outputs: List[str]) -> Dict[str, Any]:
    """Return each security's latest daily snapshot, the authoritative current quote."""
    columns = _requested_fields(outputs)
    filters = _filters_from_args(args)
    slot: Dict[str, Any] = {"mode": 2, "kline_type": "1d"}
    limit_policy = _latest_quote_limit_policy(args=args, filters=filters)
    if limit_policy["error"]:
        return _result(status="result_too_large", args=args, columns=columns, rows=[], slot=slot, reason=str(limit_policy["error"]))

    source_where_sql, source_params = _snapshot_identity_where(filters)
    date_sql, date_params = _trade_date_predicate(filters)
    where_sql, where_params = _where_sql(filters=filters, slot={})
    order_sql = _order_sql(str(args.get("order") or ""), default="code ASC")
    sql = f"""
        {_stored_bar_cte(
            kline_type="1d",
            period_minutes=1440,
            source_where_sql=source_where_sql,
            date_sql=date_sql,
        )},
        latest_by_code AS (
            SELECT
                base.*,
                ROW_NUMBER() OVER (
                    PARTITION BY code
                    ORDER BY tradedate DESC, snapshot_time DESC, bar_end_time DESC
                ) AS latest_row
            FROM base
        )
        SELECT {", ".join(f"`{column}`" for column in columns)}
        FROM latest_by_code
        WHERE latest_row = 1
          AND {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
    """
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple([*source_params, *date_params, *where_params, int(limit_policy["fetch_limit"])]))
            raw_rows = list(cursor.fetchall())
    except Exception as exc:  # noqa: BLE001
        return _result(status="provider_error", args=args, columns=columns, rows=[], slot=slot, reason=str(exc))
    finally:
        db.close_db()

    if limit_policy["detect_overflow"] and len(raw_rows) > int(limit_policy["hard_limit"]):
        return _result(
            status="result_too_large",
            args=args,
            columns=columns,
            rows=[],
            slot=slot,
            reason=f"matching latest quotes exceed the safety limit ({limit_policy['hard_limit']})",
        )
    returned_dates = [row.get("tradedate") for row in raw_rows if row.get("tradedate")]
    if returned_dates:
        slot["latest_trade_date"] = str(max(returned_dates))
    return _result(
        status="ok",
        args=args,
        columns=columns,
        rows=[_normalize_row(row, columns) for row in raw_rows],
        slot=slot,
        sql_shape={
            "mode": 2,
            "selection": "latest_daily_bar_per_code",
            "source": "stored_1d_snapshot",
            "where": where_sql,
            "order": order_sql,
            "limit": limit_policy["business_limit"],
            "hard_row_limit": limit_policy["hard_limit"],
        },
    )


def execute_intraday_quote_agg_api(
    *,
    args: Mapping[str, Any],
    outputs: List[str],
    latest_only: bool = False,
) -> Dict[str, Any]:
    filters = _filters_from_args(args)
    slot = _resolve_slot(filters=filters, latest_only=False)
    source_where_sql, source_params = _snapshot_identity_where(filters)
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
    if latest_only:
        date_sql, date_params = _trade_date_predicate(filters)
        base_cte = _stored_bar_cte(
            kline_type="1d",
            period_minutes=1440,
            source_where_sql=source_where_sql,
            date_sql=date_sql,
        )
        query_params = [*source_params, *date_params, *params, limit]
    else:
        base_cte = _minute_bar_cte(date_predicate="s.trade_date = %s", source_where_sql=source_where_sql)
        query_params = [slot["trade_date"], *source_params, *params, limit]
    sql = f"""
        {base_cte}
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
            cursor.execute(sql, tuple(query_params))
            rows = [_normalize_row(row, columns) for row in cursor.fetchall()]
        return _result(
            status="ok",
            args=args,
            columns=columns,
            rows=rows,
            slot=slot,
            sql_shape={
                "mode": 2 if latest_only else 1,
                "source": "stored_1d_snapshot" if latest_only else "stored_1m_kline",
                "where": where_sql,
                "group_by": group_fields,
                "order": order_sql,
                "limit": limit,
            },
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

    filters = _filters_from_args(args)
    slot = _resolve_slot(filters=filters, latest_only=True)
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


def _resolve_slot(*, filters: List[Dict[str, str]], latest_only: bool) -> Dict[str, Any]:
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

    source_where_sql, source_params = _snapshot_identity_where(filters)
    source_filter = f" AND {source_where_sql}" if source_where_sql else ""
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if requested_date:
                trade_date = requested_date
            else:
                cursor.execute(
                    f"""
                    SELECT MAX(s.trade_date) AS trade_date
                    FROM {SNAPSHOT_TABLE} s
                    WHERE s.kline_type = '1m'
                      AND s.period_minutes = 1
                      {source_filter}
                    """,
                    tuple(source_params),
                )
                trade_date = str((cursor.fetchone() or {}).get("trade_date") or "")
            if requested_minute is not None:
                minute_index = requested_minute
            elif requested_slot:
                cursor.execute(
                    f"""
                    SELECT MAX(s.minute_index) AS minute_index
                    FROM {SNAPSHOT_TABLE} s
                    WHERE s.trade_date = %s
                      AND s.kline_type = '1m'
                      AND s.period_minutes = 1
                      AND s.snapshot_slot = %s
                      {source_filter}
                    """,
                    tuple([trade_date, requested_slot, *source_params]),
                )
                minute_index = int((cursor.fetchone() or {}).get("minute_index") or 0)
            elif latest_only:
                cursor.execute(
                    f"""
                    SELECT MAX(s.minute_index) AS minute_index
                    FROM {SNAPSHOT_TABLE} s
                    WHERE s.trade_date = %s
                      AND s.kline_type = '1m'
                      AND s.period_minutes = 1
                      {source_filter}
                    """,
                    tuple([trade_date, *source_params]),
                )
                minute_index = int((cursor.fetchone() or {}).get("minute_index") or 0)
            else:
                minute_index = 0
    finally:
        db.close_db()
    return {
        "trade_date": trade_date,
        "minute_index": minute_index,
        "snapshot_slot": requested_slot,
        "mode": 2 if latest_only else 1,
    }


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
                  AND kline_type = '1m'
                  AND period_minutes = 1
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
        {_minute_bar_cte(date_predicate=f"s.trade_date IN ({placeholders})")}
        SELECT *
        FROM base
        WHERE minute_index = %s
    """
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple([*trade_dates, minute_index]))
            return [_normalize_row(row, list(FIELD_SQL)) for row in cursor.fetchall()]
    finally:
        db.close_db()


def _minute_bar_cte(*, date_predicate: str, source_where_sql: str = "") -> str:
    """One-minute source bars for same-minute historical comparisons.

    The snapshot table now stores native fixed-period bars.  This CTE is kept
    solely for the same-minute KD/dynamic paths, which require the native 1m
    sequence; it must never derive bars by differencing a daily snapshot.
    """
    source_filter = f" AND {source_where_sql}" if source_where_sql else ""
    return f"""
        WITH base AS (
            SELECT
                s.stk_code AS code,
                s.stk_name AS name,
                s.trade_date AS tradedate,
                s.minute_index AS minute_index,
                TIME_FORMAT(s.bar_end_time, '%%H:%%i:%%s') AS minute_time,
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
                s.amount AS minute_amount,
                s.volume AS minute_volumn,
                s.source AS source,
                s.is_fallback AS is_fallback,
                s.is_finalized AS is_finalized,
                s.source_bar_count AS source_bar_count,
                s.bar_start_time AS bar_start_time,
                s.bar_end_time AS bar_end_time,
                s.kline_type AS kline_type,
                s.period_minutes AS period_minutes
            FROM {SNAPSHOT_TABLE} s
            WHERE {date_predicate}
              AND s.kline_type = '1m'
              AND s.period_minutes = 1
              AND s.stk_code REGEXP '^[0-9]{{6}}$'
              {source_filter}
        )
    """


def _stored_bar_cte(
    *,
    kline_type: str,
    period_minutes: int,
    source_where_sql: str = "",
    date_sql: str = "",
) -> str:
    """Project native fixed-period source rows into the stable quote fields."""
    source_filter = f" AND {source_where_sql}" if source_where_sql else ""
    requested_date_filter = f" AND {date_sql}" if date_sql else ""
    return f"""
        WITH base AS (
            SELECT
                s.stk_code AS code,
                s.stk_name AS name,
                s.trade_date AS tradedate,
                s.minute_index AS minute_index,
                TIME_FORMAT(s.bar_end_time, '%%H:%%i:%%s') AS minute_time,
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
                s.amount AS minute_amount,
                s.volume AS minute_volumn,
                s.source AS source,
                s.is_fallback AS is_fallback,
                s.is_finalized AS is_finalized,
                s.source_bar_count AS source_bar_count,
                s.bar_start_time AS bar_start_time,
                s.bar_end_time AS bar_end_time,
                s.kline_type AS kline_type,
                s.period_minutes AS period_minutes
            FROM {SNAPSHOT_TABLE} s
            WHERE s.kline_type = '{kline_type}'
              AND s.period_minutes = {int(period_minutes)}
              AND s.stk_code REGEXP '^[0-9]{{6}}$'
              {source_filter}
              {requested_date_filter}
        )
    """


def _trade_date_predicate(filters: Iterable[Mapping[str, str]]) -> tuple[str, List[Any]]:
    """Push an unambiguous trade-date filter into the native K-line read.

    Date filtering must happen before the per-code `count` window is selected;
    otherwise a request such as `tradedate >= 2026-08-01` could be truncated by
    newer bars before its requested range is evaluated.
    """
    items = list(filters)
    if any(str(item.get("connector") or "").strip().lower() == "or" for item in items):
        return "", []
    clauses: List[str] = []
    params: List[Any] = []
    for item in items:
        if _canonical_field(item.get("field")) != "tradedate":
            continue
        op = str(item.get("op") or "").lower()
        sql_op = "=" if op == "==" else op.upper()
        if op == "in":
            values = [value[:10] for value in _list_values(str(item.get("value") or "")) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10])]
            if values:
                clauses.append(f"s.trade_date IN ({', '.join(['%s'] * len(values))})")
                params.extend(values)
            continue
        value = _clean_value(str(item.get("value") or ""))[:10]
        if sql_op in {"=", "!=", ">", ">=", "<", "<=", "LIKE"} and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            clauses.append(f"s.trade_date {sql_op} %s")
            params.append(value)
    return (" AND ".join(clauses), params)


def _snapshot_identity_where(filters: List[Dict[str, str]]) -> tuple[str, List[Any]]:
    if any(item.get("connector", "").strip().lower() == "or" for item in filters):
        return "", []
    clauses: List[str] = []
    params: List[Any] = []
    for item in filters:
        field_name = _canonical_field(item["field"])
        if field_name not in {"code", "name"}:
            continue
        column = "s.stk_code" if field_name == "code" else "s.stk_name"
        op = item["op"].lower()
        if op == "in":
            values = [_normalize_filter_value(field_name, value) for value in _list_values(item["value"])]
            if values:
                clauses.append(f"{column} IN ({', '.join(['%s'] * len(values))})")
                params.extend(values)
            continue
        sql_op = "=" if op == "==" else op.upper()
        if sql_op not in {"=", "!=", "LIKE"}:
            continue
        clauses.append(f"{column} {sql_op} %s")
        params.append(_normalize_filter_value(field_name, _clean_value(item["value"])))
    return " AND ".join(clauses), params


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


def _filters_from_args(args: Mapping[str, Any]) -> List[Dict[str, str]]:
    filters: List[Dict[str, str]] = []
    for field_name in ("code", "name"):
        value = args.get(field_name)
        if value not in (None, ""):
            filters.append(
                {
                    "connector": "AND",
                    "field": field_name,
                    "op": "=",
                    "value": str(value),
                }
            )
    for field_name in ("codes", "names"):
        value = args.get(field_name)
        if value in (None, ""):
            continue
        values = list(value) if isinstance(value, (list, tuple, set)) else [value]
        filters.append(
            {
                "connector": "AND",
                "field": field_name[:-1],
                "op": "in",
                "value": ",".join(str(item) for item in values if str(item).strip()),
            }
        )
    filters.extend(_parse_filter(str(args.get("filter") or "")))
    return filters


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


def _latest_quote_hard_limit() -> int:
    try:
        configured = int(
            os.getenv(
                "FIN_AGENT_LATEST_QUOTE_HARD_LIMIT",
                str(DEFAULT_LATEST_QUOTE_HARD_LIMIT),
            )
        )
    except (TypeError, ValueError):
        configured = DEFAULT_LATEST_QUOTE_HARD_LIMIT
    return max(DEFAULT_LATEST_QUOTE_LIMIT, configured)


def _intraday_hard_row_limit() -> int:
    try:
        configured = int(
            os.getenv(
                "FIN_AGENT_INTRADAY_HARD_ROW_LIMIT",
                str(DEFAULT_INTRADAY_HARD_ROW_LIMIT),
            )
        )
    except (TypeError, ValueError):
        configured = DEFAULT_INTRADAY_HARD_ROW_LIMIT
    return max(10_000, configured)


def _latest_quote_limit_policy(
    *,
    args: Mapping[str, Any],
    filters: List[Dict[str, str]],
) -> Dict[str, Any]:
    hard_limit = _latest_quote_hard_limit()
    has_limit = "limit" in args and args.get("limit") not in (None, "")
    try:
        requested = int(args.get("limit")) if has_limit else None
    except (TypeError, ValueError):
        requested = None
    if requested is not None and requested < -1:
        return {
            "error": "limit must be -1 or a positive integer",
            "hard_limit": hard_limit,
        }
    if requested == 0:
        return {
            "error": "limit must be -1 or a positive integer",
            "hard_limit": hard_limit,
        }
    if requested is not None and requested > hard_limit:
        return {
            "error": (
                f"requested limit {requested} exceeds the safety limit {hard_limit}; "
                "use limit=-1 for explicit full results and configure a larger "
                "FIN_AGENT_LATEST_QUOTE_HARD_LIMIT only for an authorized bulk run"
            ),
            "hard_limit": hard_limit,
        }
    if requested == -1:
        return {
            "error": "",
            "business_limit": -1,
            "fetch_limit": hard_limit + 1,
            "hard_limit": hard_limit,
            "detect_overflow": True,
        }
    if requested is not None:
        return {
            "error": "",
            "business_limit": requested,
            "fetch_limit": requested,
            "hard_limit": hard_limit,
            "detect_overflow": False,
        }

    explicit_target_count = _identity_filter_count(filters)
    if explicit_target_count > hard_limit:
        return {
            "error": (
                f"explicit target list contains {explicit_target_count} securities, "
                f"above the safety limit {hard_limit}"
            ),
            "hard_limit": hard_limit,
        }
    business_limit = explicit_target_count or DEFAULT_LATEST_QUOTE_LIMIT
    return {
        "error": "",
        "business_limit": business_limit,
        "fetch_limit": business_limit,
        "hard_limit": hard_limit,
        "detect_overflow": False,
    }


def _identity_filter_count(filters: List[Dict[str, str]]) -> int:
    identities: set[tuple[str, str]] = set()
    for item in filters:
        field_name = _canonical_field(item.get("field"))
        if field_name not in {"code", "name"}:
            continue
        op = str(item.get("op") or "").lower()
        if op == "in":
            values = _list_values(str(item.get("value") or ""))
        elif op in {"=", "=="}:
            values = [_clean_value(str(item.get("value") or ""))]
        else:
            continue
        for value in values:
            normalized = _normalize_filter_value(field_name, value)
            if str(normalized).strip():
                identities.add((field_name, str(normalized)))
    return len(identities)


def _minute_period(value: Any) -> int:
    text = str(value if value not in (None, "") else 1).strip().lower()
    if text.endswith("m"):
        text = text[:-1]
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def _minute_bar_count(value: Any, *, fallback: Any = None) -> int:
    raw = value if value not in (None, "") else fallback
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(parsed, 1000))


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
