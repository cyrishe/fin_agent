"""Live quotes behind the existing stock.quote(mode=2) contract.

Only the security universe comes from the database. Prices, limits and market
timestamps come from stockHq; failed batches never fall back to stored bars.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import re
from statistics import median
from typing import Any, Mapping

import pymysql
import requests

from src.experiments.staged_data_protocol.phase2 import python_filter as pf
from src.experiments.staged_data_protocol.phase2 import intraday_quote_provider as intraday
from src.experiments.staged_data_protocol.phase2.agg_protocol import AGG_METHODS, output_alias, parse_agg_spec
from src.utils.mysql_utils import StockInfoDbUtils


QUOTE_URL = "http://jzyzwup.upoem1.com/json/hq_basichq/stockHq"
BATCH_SIZE = 500
FIELDS = set(intraday.FIELD_SQL) | {"avg_price", "turn_ratio", "amplitude", "is_limit_price"}
DEFAULT_COLUMNS = ["code", "name", "tradedate", "snapshot_time", "close", "pct", "amount", "volumn"]


def _condition(args: Mapping[str, Any]):
    # Use the same parser for current Python conditions and compatible old DSL.
    from src.experiments.staged_data_protocol.phase2.call_structure import parse_filter_expression

    tree = args.get("_filter_expression") if "_filter_expression" in args else parse_filter_expression(str(args.get("filter") or ""))
    direct = []
    for key in ("code", "name", "codes", "names", "date", "tradedate"):
        value = args.get(key)
        if value is None or value == "":
            continue
        multiple = key in {"codes", "names"}
        if multiple and not isinstance(value, (list, tuple, set)):
            value = [value]
        direct.append({"field": key[:-1] if multiple else key, "operator": "in" if multiple else "==", "value": list(value) if multiple else value})
    trees = [*direct, *([tree] if tree is not None else [])]
    if not trees:
        return None

    def normalize(p):
        field = intraday._canonical_field(p["field"])
        value = p["value"]
        if field == "code" and p["operator"] != "contains":
            value = [intraday._code6(str(v)) for v in value] if isinstance(value, list) else intraday._code6(str(value)) if value is not None else None
        return {**p, "field": field, "value": value}

    return pf.map_predicates({"and": trees}, normalize)


def _load_securities(tree) -> list[dict[str, Any]]:
    # Code-only scopes do not need name resolution or any database access.
    scope = pf.project(tree, {"code"})
    leaves = list(pf.predicates(scope))
    if leaves and all(p["operator"] in {"=", "==", "in"} and p["value"] is not None for p in leaves):
        codes = sorted({str(v) for p in leaves for v in (p["value"] if isinstance(p["value"], list) else [p["value"]])})
        markets = {"6": 1, "0": 0, "2": 0, "3": 0, "4": 7, "8": 7, "9": 7}
        return [{"code": code, "setcode": 1 if code.startswith("900") else markets[code[0]]}
                for code in codes if len(code) == 6 and code.isdigit() and code[0] in markets
                and pf.evaluate(scope, {"code": code})]

    # Name matching happens against the live name (including N/ST prefixes), not
    # static baseinfo names. Without a code prerequisite scan the active universe.
    where, params = pf.compile_tree(scope, {"code": "LEFT(stk_code, 6)"})
    db = StockInfoDbUtils(database="kingdomai")
    try:
        with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT LEFT(stk_code, 6) AS code, "
                "CASE RIGHT(stk_code, 2) WHEN 'SH' THEN 1 WHEN 'SZ' THEN 0 WHEN 'BJ' THEN 7 END AS setcode "
                "FROM kcrp_stock_baseinfo WHERE (delist_date IS NULL OR delist_date > CURRENT_DATE) "
                "AND RIGHT(stk_code, 2) IN ('SH', 'SZ', 'BJ') AND " + (where or "1=1"), tuple(params),
            )
            return list(cursor.fetchall())
    finally:
        db.close_db()


def _fetch_batch(securities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response = requests.post(QUOTE_URL, json={"stReq": {
        "vStock": [{"shtSetcode": row["setcode"], "sCode": row["code"]} for row in securities],
        "eHqData": 3,  # Basic (1) + extended (2): includes limits and exchange time.
    }}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("taf_ret") != 0 or not isinstance(payload.get("stRsp", {}).get("vStockHq"), list):
        raise ValueError("stockHq returned an unsuccessful or malformed response")
    requested = {row["code"] for row in securities}
    return [item for item in payload["stRsp"]["vStockHq"] if item.get("sCode") in requested]


def _normalize_quote(item: Mapping[str, Any]) -> dict[str, Any] | None:
    sim, ex = item.get("stSimHq") or {}, item.get("stExHq") or {}
    # An unknown/delisted security can return an all-zero placeholder.
    if not ex.get("iTradeDate"):
        return None
    stamp = datetime.strptime(f"{int(ex['iTradeDate']):08d}{int(ex['iTradeTime']):06d}", "%Y%m%d%H%M%S")
    quantum = Decimal(1).scaleb(-int(item.get("shtPrecise", 2)))

    def price(value):
        return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP) if value is not None else None

    current, upper, lower = (price(value) for value in (sim.get("fNowPrice"), ex.get("fZTPrice"), ex.get("fDTPrice")))
    limit_flag = None
    if current is not None and current > 0:
        if upper is not None and upper > 0 and current == upper:
            limit_flag = 1
        elif lower is not None and lower > 0 and current == lower:
            limit_flag = 2
        elif upper is not None and lower is not None:
            limit_flag = 0  # Zero bounds denote no price limit, not a zero limit price.
    row = {field: None for field in FIELDS}
    row.update({
        "code": item["sCode"], "name": item.get("sName"),
        "tradedate": stamp.date().isoformat(), "snapshot_time": stamp.isoformat(sep=" "),
        "snapshot_slot": stamp.strftime("%H:%M"), "minute_time": stamp.strftime("%H:%M:%S"),
        "open": sim.get("fOpen"), "close": sim.get("fNowPrice"), "preclose": sim.get("fClose"),
        "high": sim.get("fHigh"), "low": sim.get("fLow"), "pct": sim.get("fChgRatio"),
        "differ": sim.get("fChgValue"), "amount": sim.get("fAmount"), "volumn": sim.get("lVolume"),
        "amplitude": sim.get("fZhenfu"), "avg_price": ex.get("fAveragePrice"), "turn_ratio": ex.get("fTurnoverRate"),
        "is_limit_price": limit_flag, "source": "upchina", "is_fallback": False,
    })
    for alias in FIELDS:
        canonical = intraday._canonical_field(alias)
        if alias != canonical:
            row[alias] = row.get(canonical)
    return row


def load_realtime_rows(args: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tree = _condition(args)
    if tree is not None and pf.certainly_empty(tree):
        return [], {"requested_count": 0, "received_count": 0, "missing_codes": []}
    securities = _load_securities(tree)
    batches = [securities[i:i + BATCH_SIZE] for i in range(0, len(securities), BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        raw = [item for batch in pool.map(_fetch_batch, batches) for item in batch]
    rows = {row["code"]: row for item in raw if (row := _normalize_quote(item)) is not None}
    diagnostics = {"requested_count": len(securities), "received_count": len(rows),
                   "missing_codes": sorted({s["code"] for s in securities} - set(rows))}
    return [row for row in rows.values() if _matches(tree, row)], diagnostics


@lru_cache(maxsize=128)
def _legacy_like_pattern(pattern: str):
    # Compatibility only: new model-facing conditions use 'text' in field.
    pieces, escaped = [], False
    for char in pattern:
        if escaped:
            pieces.append(re.escape(char))
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            pieces.append(".*" if char == "%" else "." if char == "_" else re.escape(char))
    if escaped:
        pieces.append(re.escape("\\"))
    return re.compile("".join(pieces), re.DOTALL | re.IGNORECASE)


def _matches(tree, row):
    if tree is None:
        return True
    def legacy(p):
        if p["operator"] == "like":
            value = row.get(p["field"])
            return {"constant": value is not None and _legacy_like_pattern(str(p["value"])).fullmatch(str(value)) is not None}
        return p
    return pf.evaluate(pf.map_predicates(tree, legacy), row)


def _sort(rows, order):
    from src.experiments.staged_data_protocol.phase2.call_structure import parse_order
    for item in reversed(parse_order(str(order or ""))):
        field = intraday._canonical_field(item["field"])
        present = [row for row in rows if row.get(field) is not None]
        missing = [row for row in rows if row.get(field) is None]
        rows = sorted(present, key=lambda row: row[field], reverse=item["direction"] == "desc") + missing
    return rows


def execute_realtime_quote_api(*, args: Mapping[str, Any], outputs: list[str], aggregate: bool = False) -> dict[str, Any]:
    columns = []
    projections = []
    for output in outputs or DEFAULT_COLUMNS:
        source, _, alias = output.partition(" as ")
        source = intraday._canonical_field(source.strip().rsplit(".", 1)[-1])
        column = alias.strip() or source
        columns.append(column)
        projections.append((source, column))
    payload = {"status": "ok", "source": ["upchina"], "api": "stock.quote.agg" if aggregate else "stock.quote",
               "arguments": dict(args), "columns": columns, "rows": [], "row_count": 0, "slot": {"mode": 2}}
    try:
        policy = intraday._latest_quote_limit_policy(args=args, filters=intraday._filters_from_args(args))
        if policy["error"]:
            payload.update(status="result_too_large", reason=policy["error"])
            return payload
        rows, diagnostics = load_realtime_rows(args)
        payload["diagnostics"] = diagnostics
        if rows:
            payload["slot"]["latest_trade_date"] = max(row["tradedate"] for row in rows)
        if aggregate:
            spec = parse_agg_spec(args)
            metric = intraday._canonical_field(spec.metric_column)
            if spec.method not in AGG_METHODS or metric not in FIELDS:
                raise ValueError(f"unsupported aggregate={spec.method}({spec.metric})")
            groups = [intraday._canonical_field(field.strip()) for field in str(args.get("group_by") or "").split(",") if field.strip()]
            if any(field not in FIELDS for field in groups):
                raise ValueError("group_by contains an unsupported quote field")
            alias = output_alias(outputs, default=f"{spec.method}_{metric}", exclude=set(groups))
            grouped = {} if groups else {(): []}
            for row in rows:
                grouped.setdefault(tuple(row[field] for field in groups), []).append(row[metric])
            rows = []
            for key, values in grouped.items():
                values = [v for v in values if v is not None]
                value = len(values) if spec.method == "count" else None
                if values and spec.method != "count":
                    value = {"sum": sum, "avg": lambda v: sum(v) / len(v), "min": min, "max": max, "median": median}[spec.method](values)
                rows.append({**dict(zip(groups, key)), alias: value})
            columns = [*groups, alias]
            projections = [(field, field) for field in columns]
            payload["columns"] = columns
        rows = _sort(rows, args.get("order") or (f"{columns[-1]} desc" if aggregate else "code asc"))
        if policy["detect_overflow"] and len(rows) > policy["hard_limit"]:
            payload.update(status="result_too_large", reason="matching live quotes exceed the safety limit")
            return payload
        rows = rows[:policy["fetch_limit"]]
        payload.update(rows=[{column: row.get(source) for source, column in projections} for row in rows], row_count=len(rows))
    except Exception as exc:
        payload.update(status="provider_error", reason=str(exc))
    return payload
