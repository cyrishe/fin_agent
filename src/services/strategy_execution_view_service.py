"""Read-only, user-scoped view of recent strategy selections and outcomes."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import os
import json
import re
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import pymysql
import requests


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


def today_minute_bars(rows: list[dict], now: datetime) -> list[dict]:
    """Keep only today's completed session bars, deduplicated and capped at N."""
    current = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    count = completed_session_minutes(now)
    if not count:
        return []
    by_end = {}
    for row in rows:
        end = row["end"]
        if end.date() != current.date() or end > current or end.second or end.microsecond:
            continue
        if not (time(9,30) < end.time() <= time(11,30) or time(13) < end.time() <= time(15)):
            continue
        by_end[end] = {"date": end.date().isoformat(), "time": end.strftime("%H:%M"),
                       **{k: _number(row.get(k)) for k in ("open","high","low","close","volume","amount")}}
    return [by_end[end] for end in sorted(by_end)[-count:]]


def load_live_minutes(code: str, now: datetime) -> list[dict]:
    """Stock Agent's Upchina endpoint; native line type 1 means ONE minute."""
    count = completed_session_minutes(now)
    if not count:
        return []
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    clock = local.time()
    # Upchina includes the forming bar. One spare retains 09:31 before we
    # discard that unfinished bar; the displayed result still contains <= N.
    forming = time(9,30) <= clock < time(11,30) or time(13) <= clock < time(15)
    market = 1 if code.startswith("6") else 7 if code.startswith(("9","4","8")) else 0
    response = requests.post("http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData",
        json={"stReq": {"stHeader": {"shtMarket": market}, "sCode": code,
                        "eLineType": 1, "shtStartxh": 0, "shtWantNum": count + int(forming)}},
        timeout=(2,4))
    response.raise_for_status()
    values = []
    for item in response.json().get("stRsp", {}).get("vAnalyData", []):
        stamp = item["sttDateTime"]
        end = datetime.strptime(str(stamp["iDate"]), "%Y%m%d") + timedelta(minutes=int(stamp["shtTime"]))
        values.append(dict(end=end, open=float(item["fOpen"]), high=float(item["fHigh"]),
            low=float(item["fLow"]), close=float(item["fClose"]),
            volume=float(item.get("lVolume") or 0)*100, amount=float(item.get("fAmount") or 0)))
    return today_minute_bars(values, now)


class StrategyExecutionViewService:
    """Read selection-day charts: historical DB snapshots or today’s live minute API."""

    def load_today_market(self, *, owner_user_id: str, now: datetime | None = None,
                          quote_loader=None, minute_loader=None, signal_date: str | None = None) -> dict:
        current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
        today = current.date().isoformat()
        target = date.fromisoformat(signal_date).isoformat() if signal_date else today
        if target > today:
            raise ValueError("选股日期不能晚于今天")
        live = target == today
        requested_minutes = completed_session_minutes(current) if live else 240
        picks, daily, bars, errors, quotes = {}, {}, {}, {}, {}
        quote_error = None
        with kingdom_connection("KINGDOMAI_DB_URL") as conn:
            tables = _tables(conn)
            # Exactly the same 20-trading-day scope and older unsold positions
            # as the execution list. Codes are never accepted from the browser.
            days = query(conn, """SELECT calendar_date FROM aiia_trade_calendar
                WHERE market_code='CN_A' AND is_trade_day=1 AND calendar_date<=%s
                ORDER BY calendar_date DESC LIMIT 20""", (today,))
            first = _iso(days[-1]["calendar_date"]) if days else today
            for table in (("strategy_execution",) if "strategy_execution" in tables else
                          ("automl_strategy_execution", "automl_strategy_selection")):
                if table not in tables:
                    continue
                where = "signal_date BETWEEN %s AND %s"
                args = (owner_user_id, first, today)
                if table == "strategy_execution":
                    where = "(signal_date BETWEEN %s AND %s OR (signal_date<%s AND buy_price IS NOT NULL AND sell_price IS NULL))"
                    args += (first,)
                for row in query(conn, f"""SELECT DISTINCT stock_code,stock_name FROM {table}
                    WHERE user_id=%s AND {where} AND signal_date=%s""", (*args, target)):
                    code = str(row["stock_code"]).strip()
                    if re.fullmatch(r"[0-9]{6}", code):
                        picks.setdefault(code, str(row["stock_name"]).strip())
            codes = sorted(picks)
            if codes:
                # Daily history is raw/unadjusted, matching the live quotes.
                symbols = tuple(c + (".SH" if c.startswith("6") else ".BJ" if c.startswith(("9","4","8")) else ".SZ") for c in codes)
                marks = ','.join(['%s'] * len(symbols))
                rows = query(conn, f"""SELECT stk_code,trade_date,preclose,open,high,low,close,volume,amount
                    FROM kcrp_stock_price WHERE stk_code IN ({marks})
                    AND trade_date>=%s AND trade_date{'<' if live else '<='}%s ORDER BY stk_code,trade_date DESC""",
                    (*symbols, (date.fromisoformat(target)-timedelta(days=60)).isoformat(), target))
                for row in rows:
                    code = str(row['stk_code'])[:6]
                    history = daily.setdefault(code, [])
                    if not live and _iso(row['trade_date']) == target:
                        close, reference = _number(row['close']), _number(row['preclose'])
                        quotes[code] = {**{k:_number(row[k]) for k in ('open','high','low','close','preclose','amount')},
                            'pct': (float(close)/float(reference)-1)*100 if close and reference else None,
                            'snapshot_time': target+' 15:00:00', 'source': 'daily_database'}
                    if len(history) < 20:
                        history.append({"date": _iso(row['trade_date']), "time": _iso(row['trade_date'])[5:],
                                        **{k:_number(row[k]) for k in ('open','high','low','close','volume','amount')}})
                for rows in daily.values():
                    rows.reverse()
                if not live:
                    marks = ','.join(['%s'] * len(codes))
                    rows = query(conn, f"""SELECT stk_code,bar_end_time AS end,
                        open_price AS open,high_price AS high,low_price AS low,
                        latest_price AS close,volume,amount
                        FROM aiia_stock_realtime_minute_snapshot_full
                        WHERE trade_date=%s AND stk_code IN ({marks})
                          AND kline_type='1m' AND period_minutes=1
                          AND is_finalized=1 AND is_fallback=0
                          AND source_snapshot_time=bar_end_time
                        ORDER BY stk_code,bar_end_time""", (target, *codes))
                    by_code = {}
                    for row in rows:
                        by_code.setdefault(str(row['stk_code'])[:6], []).append(row)
                    end = datetime.fromisoformat(target+'T15:00:00').replace(tzinfo=current.tzinfo)
                    for code in codes:
                        bars[code] = today_minute_bars(by_code.get(code, []), end)
                        if not bars[code]:
                            errors[code] = '该日分钟数据缺失'
        if codes and live:
            if quote_loader is None:
                from src.experiments.staged_data_protocol.phase2.realtime_quote_provider import load_realtime_rows
                quote_loader = load_realtime_rows
            minute_loader = minute_loader or load_live_minutes
            with ThreadPoolExecutor(max_workers=min(8, len(codes)+1)) as pool:
                quote_future = pool.submit(quote_loader, {"codes": codes})
                jobs = {pool.submit(minute_loader, code, current):code for code in codes} if requested_minutes else {}
                for job in as_completed(jobs):
                    code = jobs[job]
                    try:
                        bars[code] = job.result()
                    except Exception:
                        errors[code] = "分钟接口暂不可用"
                try:
                    quote_rows, _ = quote_future.result()
                    quotes = {str(r.get('code')):r for r in quote_rows if str(r.get('tradedate')) == today}
                except Exception:
                    quote_error = "实时行情接口暂不可用"
        return dict(trade_date=target, as_of=current.isoformat(), requested_minutes=requested_minutes,
            is_live=live, quote_error=quote_error, minute_source='upchina_1m_api' if live else 'minute_database', stocks=[dict(
                code=c, name=picks[c], bars=bars.get(c,[]), daily_bars=daily.get(c,[]),
                minute_error=errors.get(c),
                quote={k:_number(quotes[c].get(k)) for k in
                       ('close','pct','preclose','open','high','low','amount','volumn','snapshot_time','source')}
                      if c in quotes else None) for c in codes])

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
