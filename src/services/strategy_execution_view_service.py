"""Read-only, user-scoped view of recent strategy selections and outcomes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import os
import json
import re
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import pymysql


@contextmanager
def kingdom_connection(env_name: str):
    """Use the configured kingdomai database in a read-only transaction."""
    url = os.environ.get(env_name, "")
    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    if parsed.scheme != "mysql" or parsed.path.strip("/") != "kingdomai":
        raise ValueError(f"{env_name} must point to kingdomai")
    conn = pymysql.connect(
        host=parsed.hostname, port=parsed.port or 3306,
        user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
        database="kingdomai", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=8,
        read_timeout=60, write_timeout=15, autocommit=False,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=45000")
            cursor.execute("START TRANSACTION READ ONLY")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def query(conn, sql: str, args: tuple = ()) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(sql, args)
        return list(cursor.fetchall())


def _iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _number(value):
    return float(value) if isinstance(value, Decimal) else value


def _tables(conn) -> set[str]:
    rows = query(conn, """SELECT TABLE_NAME AS table_name FROM information_schema.tables
        WHERE table_schema=DATABASE() AND table_name IN
        ('automl_strategy_definition','automl_strategy_selection',
         'automl_strategy_run','automl_strategy_execution',
         'strategy_daily_run','strategy_execution')""")
    return {str(row["table_name"]) for row in rows}


def completed_session_minutes(now: datetime) -> int:
    """Count completed A-share one-minute bars from today's market open."""
    clock = now.astimezone(ZoneInfo("Asia/Shanghai")).time()
    def elapsed(start: time, end: time) -> int:
        minute = clock.hour * 60 + clock.minute
        return max(0, min(minute, end.hour * 60 + end.minute)
                   - (start.hour * 60 + start.minute))
    return elapsed(time(9, 30), time(11, 30)) + elapsed(time(13), time(15))


class StrategyExecutionViewService:
    """Only reads persisted decisions; it never estimates missing outcomes."""

    def load_today_market(self, *, owner_user_id: str, now: datetime | None = None,
                          quote_loader=None) -> dict:
        current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
            ZoneInfo("Asia/Shanghai"))
        today = current.date().isoformat()
        requested_minutes = completed_session_minutes(current)
        picks: dict[str, str] = {}
        bars: dict[str, list[dict]] = {}
        with kingdom_connection("KINGDOMAI_DB_URL") as conn:
            tables = _tables(conn)
            for table in (("strategy_execution",) if "strategy_execution" in tables else
                          ("automl_strategy_execution", "automl_strategy_selection")):
                if table not in tables:
                    continue
                for row in query(conn, f"""SELECT DISTINCT stock_code,stock_name FROM {table}
                    WHERE user_id=%s AND signal_date=%s""", (owner_user_id, today)):
                    code = str(row["stock_code"]).strip()
                    if re.fullmatch(r"[0-9]{6}", code):
                        picks.setdefault(code, str(row["stock_name"]).strip())
            codes = sorted(picks)
            if codes and requested_minutes:
                placeholders = ",".join(["%s"] * len(codes))
                rows = query(conn, f"""SELECT stk_code,bar_end_time,open_price,
                    high_price,low_price,latest_price,volume,amount
                    FROM aiia_stock_realtime_minute_snapshot
                    WHERE stk_code IN ({placeholders}) AND trade_date=%s
                      AND kline_type='1m' AND period_minutes=1
                      AND is_finalized=1 AND source_bar_count=1
                      AND bar_end_time<=%s
                    ORDER BY stk_code,bar_end_time""",
                    (*codes, today, current.replace(tzinfo=None)))
                bars = {code: [] for code in codes}
                for row in rows:
                    code = str(row["stk_code"]).strip()
                    end = row["bar_end_time"]
                    clock = end.time()
                    if not (time(9, 30) < clock <= time(11, 30)
                            or time(13) < clock <= time(15)):
                        continue
                    bars[code].append({
                        "time": end.strftime("%H:%M"),
                        "open": _number(row["open_price"]),
                        "high": _number(row["high_price"]),
                        "low": _number(row["low_price"]),
                        "close": _number(row["latest_price"]),
                        "volume": _number(row["volume"]),
                        "amount": _number(row["amount"]),
                    })
        codes = sorted(picks)
        quote_error = None
        quote_rows = []
        if codes:
            try:
                if quote_loader is None:
                    from src.experiments.staged_data_protocol.phase2.realtime_quote_provider import load_realtime_rows
                    quote_loader = load_realtime_rows
                quote_rows, _ = quote_loader({"codes": codes})
            except Exception:
                quote_error = "实时行情源暂不可用"
        quotes = {str(row.get("code")): row for row in quote_rows
                  if str(row.get("tradedate")) == today}
        return {
            "trade_date": today,
            "as_of": current.isoformat(),
            "requested_minutes": requested_minutes,
            "quote_error": quote_error,
            "stocks": [{
                "code": code, "name": picks[code],
                "bars": bars.get(code, []),
                "quote": ({key: _number(quotes[code].get(key)) for key in
                           ("close", "pct", "preclose", "open", "high", "low",
                            "amount", "volumn", "snapshot_time", "source")}
                          if code in quotes else None),
            } for code in codes],
        }

    def load(self, *, owner_user_id: str, include_owners: bool = False) -> dict:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with kingdom_connection("KINGDOMAI_DB_URL") as conn:
            tables = _tables(conn)
            owners = []
            if include_owners and "strategy_daily_run" in tables:
                owners = [str(r["user_id"]) for r in query(conn,
                    "SELECT DISTINCT user_id FROM strategy_daily_run ORDER BY user_id LIMIT 100")]
            elif include_owners and "automl_strategy_definition" in tables:
                owners = [str(row["user_id"]) for row in query(
                    conn, """SELECT DISTINCT user_id FROM automl_strategy_definition
                        ORDER BY user_id LIMIT 100""")]
            days = [_iso(row["calendar_date"]) for row in query(
                conn, """SELECT calendar_date FROM aiia_trade_calendar
                    WHERE market_code='CN_A' AND is_trade_day=1
                      AND calendar_date<=%s
                    ORDER BY calendar_date DESC LIMIT 20""", (today,))]
            if not days:
                return {"owner_user_id": owner_user_id, "owners": owners,
                        "trade_dates": [], "strategies": [], "runs": [], "picks": []}
            first, last = days[-1], days[0]
            if "strategy_daily_run" in tables and "strategy_execution" in tables:
                return self._load_readable(conn, owner_user_id, owners, days)
            strategies = []
            if "automl_strategy_definition" in tables:
                strategies = [{"strategy_id": str(row["strategy_id"]),
                               "name": str(row["title"]),
                               "summary": str(row["summary"])}
                              for row in query(conn, """SELECT strategy_id,title,summary
                                  FROM automl_strategy_definition WHERE user_id=%s
                                  ORDER BY title,strategy_id""", (owner_user_id,))]
            runs = []
            if "automl_strategy_run" in tables:
                runs = [{"strategy_id": str(row["strategy_id"]),
                         "signal_date": _iso(row["signal_date"]),
                         "candidate_count": row["candidate_count"],
                         "selection_count": row["selection_count"],
                         "signal_at": _iso(row["signal_at"]) if row["signal_at"] else None}
                        for row in query(conn, """SELECT strategy_id,signal_date,
                            candidate_count,selection_count,signal_at
                            FROM automl_strategy_run WHERE user_id=%s
                              AND signal_date BETWEEN %s AND %s
                            ORDER BY signal_date DESC,strategy_id""",
                            (owner_user_id, first, last))]
            picks = []
            if "automl_strategy_execution" in tables:
                picks = [{"strategy_id": str(row["strategy_id"]),
                          "signal_date": _iso(row["signal_date"]),
                          "selection_rank": row["selection_rank"],
                          "stock_code": str(row["stock_code"]),
                          "stock_name": str(row["stock_name"]),
                          "actionable": bool(row["actionable"]),
                          "signal_at": _iso(row["signal_at"]) if row["signal_at"] else None,
                          "signal_price": _number(row["signal_price"]),
                          "entry_at": _iso(row["entry_at"]) if row["entry_at"] else None,
                          "entry_price": _number(row["entry_price"]),
                          "entry_price_basis": row["entry_price_basis"],
                          "exit_rule": row["exit_rule"],
                          "exit_at": _iso(row["exit_at"]) if row["exit_at"] else None,
                          "exit_price": _number(row["exit_price"]),
                          "exit_price_basis": row["exit_price_basis"],
                          "gross_return_pct": _number(row["gross_return_pct"]),
                          "record_source": "execution"}
                         for row in query(conn, """SELECT strategy_id,signal_date,
                             selection_rank,stock_code,stock_name,actionable,
                             signal_at,signal_price,entry_at,entry_price,
                             entry_price_basis,exit_rule,exit_at,exit_price,
                             exit_price_basis,gross_return_pct
                             FROM automl_strategy_execution WHERE user_id=%s
                               AND signal_date BETWEEN %s AND %s
                             ORDER BY signal_date DESC,strategy_id,selection_rank""",
                             (owner_user_id, first, last))]
            existing = {(p["strategy_id"], p["signal_date"], p["selection_rank"])
                        for p in picks}
            if "automl_strategy_selection" in tables:
                for row in query(conn, """SELECT strategy_id,signal_date,selection_rank,
                    stock_code,stock_name,actionable,signal_capture_at,signal_price
                    FROM automl_strategy_selection WHERE user_id=%s
                      AND signal_date BETWEEN %s AND %s
                    ORDER BY signal_date DESC,strategy_id,selection_rank""",
                    (owner_user_id, first, last)):
                    key = (str(row["strategy_id"]), _iso(row["signal_date"]),
                           row["selection_rank"])
                    if key in existing:
                        continue
                    actionable = bool(row["actionable"])
                    picks.append({"strategy_id": key[0], "signal_date": key[1],
                                  "selection_rank": key[2],
                                  "stock_code": str(row["stock_code"]),
                                  "stock_name": str(row["stock_name"]),
                                  "actionable": actionable,
                                  "signal_at": _iso(row["signal_capture_at"])
                                  if row["signal_capture_at"] else None,
                                  "signal_price": _number(row["signal_price"]),
                                  "entry_at": None, "entry_price": None,
                                  "entry_price_basis": None,
                                  "exit_rule": None, "exit_at": None,
                                  "exit_price": None, "exit_price_basis": None,
                                  "gross_return_pct": None,
                                  "record_source": "selection_only"})
            picks.sort(key=lambda p: (p["signal_date"], p["strategy_id"],
                                      -p["selection_rank"]), reverse=True)
            return {"owner_user_id": owner_user_id, "owners": owners,
                    "trade_dates": days, "strategies": strategies,
                    "runs": runs, "picks": picks}

    def _load_readable(self, conn, owner, owners, days):
        def decode(value):
            return json.loads(value) if isinstance(value, str) else (value or {})
        def key(row):
            return row["strategy_name"] + " / " + row["model_version"]
        def normalized(row):
            return {k: _iso(v) if isinstance(v, (date, datetime)) else _number(v)
                    for k, v in row.items()}
        versions = query(conn, """SELECT strategy_name,model_version,version_desc,
            MIN(signal_date) AS first_date,MAX(signal_date) AS last_date
            FROM strategy_daily_run WHERE user_id=%s
            GROUP BY strategy_name,model_version,version_desc ORDER BY strategy_name,model_version""", (owner,))
        runs = query(conn, """SELECT * FROM strategy_daily_run WHERE user_id=%s
            AND signal_date BETWEEN %s AND %s ORDER BY signal_date DESC,strategy_name,model_version""",
            (owner, days[-1], days[0]))
        # Open positions survive the 20-day window until their own job settles them.
        rows = query(conn, """SELECT * FROM strategy_execution WHERE user_id=%s
            AND (signal_date BETWEEN %s AND %s OR
                 (signal_date<%s AND buy_price IS NOT NULL AND sell_price IS NULL))
            ORDER BY signal_date DESC,strategy_name,model_version,selection_rank""",
            (owner, days[-1], days[0], days[-1]))
        picks = []
        for row in rows:
            value = normalized(row)
            details = decode(row['details_json'])
            value.update(strategy_id=key(row), record_source='execution',
                details=details, exit_rule=details.get('exit_rule'))
            for new, old in [('buy_at','entry_at'),('buy_price','entry_price'),
                             ('buy_price_basis','entry_price_basis'),('sell_at','exit_at'),
                             ('sell_price','exit_price'),('sell_price_basis','exit_price_basis')]:
                value[old] = value[new]
            value.pop('details_json', None)
            picks.append(value)
        return dict(owner_user_id=owner, owners=owners, trade_dates=days,
            strategies=[dict(strategy_id=key(r),name=key(r),summary=r['version_desc'],
                             strategy_name=r['strategy_name'],model_version=r['model_version'],
                             first_date=_iso(r['first_date']),last_date=_iso(r['last_date'])) for r in versions],
            runs=[{**normalized(r),'strategy_id':key(r),
                   'environment_json':decode(r['environment_json'])} for r in runs],picks=picks)
