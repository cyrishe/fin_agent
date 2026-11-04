"""Expose shared daily and bounded minute indicators through the existing catalog."""
from __future__ import annotations

import ast
from datetime import date, datetime
from decimal import Decimal
import math
import re
import statistics

import pymysql

from src.experiments.staged_data_protocol.phase2 import python_filter as pf
from src.experiments.staged_data_protocol.phase2.agg_protocol import parse_agg_spec
from src.services.stock_indicator_minute import SHANGHAI, read_minute_indicators, read_minute_window
from src.services.stock_indicator_realtime import read_realtime_indicators
from src.services.stock_indicator_store import market_connection, read_daily_indicators
from src.services.technical_indicator_calculator import STANDARD_TECHNICAL_FEATURES


def _value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return float(value) if isinstance(value, Decimal) else value


def execute_technical_api(*, minute: bool = False, realtime: bool = False, window: bool = False, args, outputs) -> dict:
    api = "stock.technical_minute_series.query" if window else "stock.technical_realtime.query" if realtime else "stock.technical_minute.query" if minute else "stock.technical.query"
    selected_outputs = outputs or (["code", "data_as_of", "lag_seconds", "return_from_open", "unavailable_reason"] if realtime
        else ["code", "bar_end_time", "is_finalized", "close", "ma20", "volume_ratio5"] if window
        else ["code", "data_as_of", "lag_seconds", "is_finalized", "ma20", "volume_ratio5", "unavailable_reason"] if minute
        else ["code", "trade_date", "ma20", "volume_ratio5"])
    mapping = []
    for item in selected_outputs:
        parts = re.split(r"\s+as\s+", item.strip(), flags=re.IGNORECASE)
        field = parts[0].rsplit(".", 1)[-1]
        mapping.append((field, parts[-1] if len(parts) > 1 else field))
    columns = [alias for _, alias in mapping]
    try:
        codes = args.get("codes") or []
        if isinstance(codes, str):
            codes = ast.literal_eval(codes)
        if not isinstance(codes, (list, tuple)) or any(not isinstance(c, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", c) for c in codes):
            raise ValueError("codes must be a list of full stock codes")
        if realtime:
            data = read_realtime_indicators(codes=codes, max_lag_seconds=args.get("max_lag_seconds", 60))
        elif window:
            data = read_minute_window(codes=codes, period=args.get("period", 5),
                max_lag_seconds=args.get("max_lag_seconds"),
                as_of=datetime.fromisoformat(args["as_of"]) if args.get("as_of") else None,
                since=datetime.fromisoformat(args["since"]) if args.get("since") else None)
        elif minute:
            partial = args.get("include_partial", False)
            if isinstance(partial, str):
                partial = ast.literal_eval(partial)
            data = read_minute_indicators(codes=codes, period=args.get("period", 1),
                include_partial=partial, max_lag_seconds=args.get("max_lag_seconds"),
                as_of=datetime.fromisoformat(args["as_of"]) if args.get("as_of") else None)
        else:
            as_of = date.fromisoformat(args["as_of"]) if args.get("as_of") else datetime.now(SHANGHAI).date()
            with market_connection() as conn:
                data = read_daily_indicators(conn, as_of=as_of, codes=codes, count=args.get("count", 1), limit=100000)
        records = [{**{k: _value(v) for k, v in row.items()}, "code": row.get("code", row.get("stk_code"))}
                   for row in data["rows"]]
        expression = pf.condition(args)
        records = [row for row in records if pf.evaluate(expression, row)]
        for term in reversed(str(args.get("order") or "").split(",")):
            if not term.strip():
                continue
            parts = term.split()
            field = parts[0]
            if len(parts) > 2 or (len(parts) == 2 and parts[1].lower() not in ("asc", "desc")):
                raise ValueError("order requires field asc/desc")
            if records and field not in records[0]:
                raise ValueError(f"unknown order field: {field}")
            present = [r for r in records if r.get(field) is not None]
            missing = [r for r in records if r.get(field) is None]
            records = sorted(present, key=lambda r: r[field], reverse=len(parts) == 2 and parts[1].lower() == "desc") + missing
        limit = args.get("limit", -1 if window else 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit == 0 or limit < -1:
            raise ValueError("limit must be -1 or a positive integer")
        if limit != -1:
            records = records[:limit]
        projected = [{alias: row[field] for field, alias in mapping} for row in records]
        evidence = {k: v for k, v in data.items() if k not in ("rows", "row_count")}
        if not minute and not realtime and not window:
            evidence["batch_ids"] = sorted({row["batch_id"] for row in records})
        return {"status": "ok", "api": api, "columns": columns, "rows": projected,
                "row_count": len(projected), "evidence": evidence}
    except (ValueError, KeyError, TypeError, SyntaxError) as exc:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0, "reason": str(exc)}
    except pymysql.MySQLError:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0,
                "reason": "indicator database query failed; check the configured market database"}
    except (TimeoutError, RuntimeError, OSError) as exc:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0,
                "reason": str(exc)}


_WINDOW_METHODS = {"avg", "max", "min", "median"}
_AGG_METHODS = _WINDOW_METHODS | {"count"}
_FULL_CODE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)\Z")


def _codes(args, *, required: bool) -> list[str]:
    codes = args.get("codes") or []
    if isinstance(codes, str):
        codes = ast.literal_eval(codes)
    if not isinstance(codes, (list, tuple)) or any(
        not isinstance(code, str) or not _FULL_CODE.fullmatch(code) for code in codes
    ):
        raise ValueError("codes must be a list of full stock codes")
    maximum = 20 if required else 10000
    if (required and not codes) or len(codes) > maximum:
        raise ValueError(f"codes must contain {'1..20' if required else '0..10000'} stocks")
    return list(dict.fromkeys(codes))


def _as_of(args) -> date:
    return date.fromisoformat(args["as_of"]) if args.get("as_of") else datetime.now(SHANGHAI).date()


def _numeric(value):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("indicator value must be finite or null")
    return number


def _statistic(values: list[float], method: str):
    if not values:
        return 0 if method == "count" else None
    return {"avg": statistics.fmean, "median": statistics.median,
            "max": max, "min": min, "count": len}[method](values)


def _limit(args, *, default: int = 100) -> int:
    limit = args.get("limit", default)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit == 0 or limit < -1:
        raise ValueError("limit must be -1 or a positive integer")
    return limit


def _window_columns(outputs):
    selected = outputs or ["code", "trade_date", "value", "current_value", "window_count"]
    mapping = []
    for output in selected:
        match = re.fullmatch(r"\s*([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?\s*", output, flags=re.IGNORECASE)
        if not match or (match.group(2) and match.group(1) != "value"):
            raise ValueError("window output must be a result field; only value supports an alias")
        mapping.append((match.group(1), match.group(2) or match.group(1)))
    return mapping


def _sort_window(rows, order):
    for term in reversed(str(order or "").split(",")):
        if not term.strip():
            continue
        parts = term.split()
        if len(parts) > 2 or (len(parts) == 2 and parts[1].lower() not in {"asc", "desc"}):
            raise ValueError("order requires field asc/desc")
        field = parts[0]
        if field not in {"code", "trade_date", "value", "current_value", "k", "window_count"}:
            raise ValueError(f"unknown order field: {field}")
        present = [row for row in rows if row.get(field) is not None]
        missing = [row for row in rows if row.get(field) is None]
        rows = sorted(present, key=lambda row: row[field], reverse=len(parts) == 2 and parts[1].lower() == "desc") + missing
    return rows


def execute_technical_window_api(*, field: str, method: str, args, outputs) -> dict:
    api = f"stock.technical.kd_{field}_{method}"
    columns = []
    try:
        mapping = _window_columns(outputs)
        columns = [alias for _, alias in mapping]
        if field not in STANDARD_TECHNICAL_FEATURES or method not in _WINDOW_METHODS:
            raise ValueError("unsupported daily indicator window")
        codes = _codes(args, required=True)
        k = args.get("k")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 252:
            raise ValueError("k must be an integer from 1 to 252")
        with market_connection() as conn:
            data = read_daily_indicators(conn, as_of=_as_of(args), codes=codes, count=k, limit=20 * k)
        grouped = {code: [] for code in codes}
        for row in data["rows"]:
            code = row["stk_code"]
            if code in grouped:
                grouped[code].append(row)
        rows = []
        batch_ids = set()
        for code, history in grouped.items():
            if not history:
                continue
            history.sort(key=lambda row: row["trade_date"])
            batch_ids.update(row["batch_id"] for row in history)
            values = [_numeric(row.get(field)) for row in history]
            usable = [value for value in values if value is not None]
            record = {"code": code, "trade_date": _value(history[-1]["trade_date"]),
                      "value": _statistic(usable, method), "current_value": values[-1],
                      "k": k, "window_count": len(usable)}
            rows.append(record)
        expression = pf.condition(args)
        rows = _sort_window([row for row in rows if pf.evaluate(expression, row)], args.get("order"))
        limit = _limit(args)
        if limit != -1:
            rows = rows[:limit]
        return {"status": "ok", "api": api, "columns": columns,
                "rows": [{alias: row[field_name] for field_name, alias in mapping} for row in rows],
                "row_count": len(rows), "evidence": {**{key: value for key, value in data.items() if key not in {"rows", "row_count"}},
                    "batch_ids": sorted(batch_ids)}}
    except pymysql.MySQLError:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0,
                "reason": "indicator database query failed; check the configured market database"}
    except (ValueError, KeyError, TypeError, SyntaxError, TimeoutError, RuntimeError, OSError) as exc:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0, "reason": str(exc)}


def execute_technical_aggregate_api(*, args, outputs) -> dict:
    api = "stock.technical.agg"
    spec = parse_agg_spec(args)
    match = re.fullmatch(r"\s*(?:[A-Za-z_]\w*\([^)]*\)|[A-Za-z_]\w*)\s*(?:as\s+([A-Za-z_]\w*))?\s*", outputs[0], flags=re.IGNORECASE) if len(outputs) == 1 else None
    alias = match.group(1) if match and match.group(1) else (outputs[0].strip() if match and "(" not in outputs[0] else f"{spec.method}_{spec.metric_column}")
    columns = [alias]
    try:
        if spec.metric != f"stock.technical.{spec.metric_column}":
            raise ValueError("aggregate metric must belong to stock.technical")
        metric = spec.metric_column
        if spec.method not in _AGG_METHODS or (metric != "code" and metric not in STANDARD_TECHNICAL_FEATURES) or (metric == "code" and spec.method != "count"):
            raise ValueError("unsupported daily indicator aggregate")
        codes = _codes(args, required=False)
        with market_connection() as conn:
            data = read_daily_indicators(conn, as_of=_as_of(args), codes=codes, count=1, limit=100000)
        dates = {_value(row["trade_date"]) for row in data["rows"]}
        if len(dates) > 1:
            raise ValueError("aggregate source contains multiple trade dates")
        batch_ids = {row["batch_id"] for row in data["rows"]}
        if len(batch_ids) > 1:
            raise ValueError("aggregate source contains multiple published batches")
        expression = pf.condition(args)
        records = [{**{key: _value(value) for key, value in row.items()}, "code": row["stk_code"]}
                   for row in data["rows"]]
        selected = [row for row in records if pf.evaluate(expression, row)]
        values = ([1.0 for _ in selected] if metric == "code" else
                  [number for row in selected if (number := _numeric(row.get(metric))) is not None])
        result = _statistic(values, spec.method)
        rows = [{alias: result}] if dates else []
        first = data["rows"][0] if data["rows"] else {}
        return {"status": "ok", "api": api, "columns": columns, "rows": rows, "row_count": len(rows),
                "evidence": {**{key: value for key, value in data.items() if key not in {"rows", "row_count"}},
                    "trade_date": next(iter(dates), None), "batch_ids": sorted(batch_ids),
                    "selected_count": len(selected), "sample_count": len(values),
                    "requested_count": first.get("requested_count"), "written_count": first.get("written_count")}}
    except pymysql.MySQLError:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0,
                "reason": "indicator database query failed; check the configured market database"}
    except (ValueError, KeyError, TypeError, SyntaxError, TimeoutError, RuntimeError, OSError) as exc:
        return {"status": "provider_error", "api": api, "columns": columns, "rows": [], "row_count": 0, "reason": str(exc)}
