"""Bounded on-demand indicators over collected bars; never call a quote vendor."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
import hashlib
import json
import re
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.stock_indicator_store import market_connection
from src.services.technical_indicator_calculator import (
    RECURSIVE_TECHNICAL_FEATURES, STANDARD_TECHNICAL_FEATURES,
    calculate_standard_technical_features,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
MINUTE_REVISION = "technical_minute_session_v2"
FINITE_FEATURES = tuple(f for f in STANDARD_TECHNICAL_FEATURES
                        if f not in RECURSIVE_TECHNICAL_FEATURES and f != "volatility20")
SESSION_RECURSIVE = tuple(f for f in STANDARD_TECHNICAL_FEATURES if f in RECURSIVE_TECHNICAL_FEATURES)
MINUTE_FEATURES = (*FINITE_FEATURES, *("session_" + f for f in SESSION_RECURSIVE),
                   "session_vwap", "session_volume_shares", "session_amount", "session_return_from_open")
PERIODS = (1, 3, 5, 10, 15, 30, 60)


def session_grid(trade_date: date, period: int) -> pd.DatetimeIndex:
    """Shanghai completed-bar ends, with the lunch break removed."""
    if isinstance(period, bool) or period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    ends = []
    for start in (time(9, 30), time(13)):
        anchor = datetime.combine(trade_date, start)
        ends.extend(anchor + timedelta(minutes=offset) for offset in range(period, 121, period))
    return pd.DatetimeIndex(ends)


@lru_cache(maxsize=128)
def _calculate_series(payload: str) -> tuple:
    source = json.loads(payload)
    frame = pd.DataFrame(source["rows"], columns=["bar_end_time", "close", "high", "low", "volume", "open", "amount"])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("bar_end_time")))
    frame = frame.astype(float)
    # Missing intervals occupy their real positions; no forward-filled prices.
    grid = session_grid(date.fromisoformat(source["trade_date"]), source["period"])
    frame = frame.reindex(grid[grid <= frame.index[-1]])
    result = calculate_standard_technical_features(frame)
    values = result[list(FINITE_FEATURES)].copy()
    for name in SESSION_RECURSIVE:
        values["session_" + name] = result[name]
    # Session totals must cover every interval, including the opening interval.
    volume = frame.volume.cumsum(skipna=False)
    amount = frame.amount.cumsum(skipna=False)
    values["session_volume_shares"] = volume
    values["session_amount"] = amount
    values["session_vwap"] = amount / volume.where(volume > 0)
    values["session_return_from_open"] = (frame.close / frame.open.iloc[0] - 1
        if frame.open.iloc[0] > 0 else float("nan"))
    # Immutable cache entries: callers cannot mutate another user's history.
    return tuple((row[0].isoformat() + "+08:00", *(
        None if pd.isna(value) else float(value) for value in row[1:]))
        for row in values[list(MINUTE_FEATURES)].itertuples(name=None))


@lru_cache(maxsize=128)
def _calculate(payload: str) -> tuple:
    return _calculate_series(payload)[-1][1:]


def _encode(rows, trade_date, period):
    encoded = []
    for row in rows:
        values = [None if row.get(name) is None else float(row[name])
                  for name in ("close", "high", "low", "volume", "open", "amount")]
        if values[3] is not None:
            values[3] *= 100  # source lots -> shares
        encoded.append([row["bar_end_time"].isoformat(), *values])
    return json.dumps({"trade_date": trade_date.isoformat(), "period": period,
                       "rows": encoded}, separators=(",", ":"), allow_nan=False)


def aggregate_minute_bars(rows: list[dict], *, trade_date: date, period: int,
                          as_of: datetime, include_partial: bool = False) -> list[dict]:
    """Derive requested-period bars from complete 1m facts, never from snapshots."""
    cutoff = as_of.astimezone(SHANGHAI).replace(tzinfo=None)
    one_minute_grid = session_grid(trade_date, 1)
    available_grid = one_minute_grid[one_minute_grid <= cutoff]
    valid_times = set(available_grid)
    accepted = [r for r in rows if r["bar_end_time"] in valid_times
                and r["is_finalized"] and r["source_bar_count"] == 1]
    by_time = {r["bar_end_time"]: r for r in accepted}
    if len(by_time) != len(accepted):
        raise ValueError("minute timestamps must be unique")
    for r in accepted:
        for field in ("close", "high", "low", "open"):
            if r.get(field) is not None and r[field] <= 0:
                raise ValueError("available minute prices must be positive")
        if r.get("volume") is not None and r["volume"] < 0:
            raise ValueError("minute volume must be nonnegative")
        if all(r.get(f) is not None for f in ("close", "high", "low")) and not r["low"] <= r["close"] <= r["high"]:
            raise ValueError("minute high/low must enclose the same-basis close")
    result = []
    for i, end in enumerate(session_grid(trade_date, period)):
        expected = one_minute_grid[i * period:(i + 1) * period]
        elapsed = expected[expected <= cutoff]
        complete = len(elapsed) == period
        if not len(elapsed) or (not complete and not include_partial):
            continue
        # A partial bar must be a contiguous prefix through the latest received minute.
        # Its real input end, not its future period end, determines freshness.
        part = [by_time[t] for t in elapsed if t in by_time]
        if not part or (complete and len(part) != period):
            continue
        if [r["bar_end_time"] for r in part] != list(elapsed[:len(part)]):
            continue
        def total(field):
            values = [r.get(field) for r in part]
            return sum(values) if all(v is not None for v in values) else None
        result.append({"bar_end_time": end.to_pydatetime(), "input_end_time": part[-1]["bar_end_time"],
                       "open": part[0].get("open"), "close": part[-1]["close"],
                       "high": max(r["high"] for r in part) if all(r.get("high") is not None for r in part) else None,
                       "low": min(r["low"] for r in part) if all(r.get("low") is not None for r in part) else None,
                       "volume": total("volume"), "amount": total("amount"),
                       "source_bar_count": len(part), "is_finalized": complete})
    return result


def compute_minute_snapshot(rows: list[dict], *, code: str, trade_date: date,
                            period: int, as_of: datetime, include_partial: bool = False,
                            max_lag_seconds: int | None = None) -> dict:
    if as_of.tzinfo is None:
        raise ValueError("as_of requires a timezone")
    cutoff = as_of.astimezone(SHANGHAI).replace(tzinfo=None)
    grid = session_grid(trade_date, period)
    expected_grid = grid[grid <= cutoff]
    if include_partial:
        elapsed_minutes = session_grid(trade_date, 1)
        elapsed_minutes = elapsed_minutes[elapsed_minutes <= cutoff]
        if len(elapsed_minutes):
            expected_grid = grid[grid <= grid[grid >= elapsed_minutes[-1]][0]]
    valid_ends = set(expected_grid.to_pydatetime())
    eligible = sorted((r for r in rows if r["bar_end_time"] in valid_ends
                       and ((r["is_finalized"] and r["source_bar_count"] == period)
                            or (include_partial and not r["is_finalized"]
                                and r.get("input_end_time", r["bar_end_time"]) <= cutoff
                                and r["bar_end_time"] == expected_grid[-1]
                                and 0 < r["source_bar_count"] < period))),
                      key=lambda r: r["bar_end_time"])
    if len({r["bar_end_time"] for r in eligible}) != len(eligible):
        raise ValueError("minute timestamps must be unique")
    base = {"code": code, "trade_date": trade_date.isoformat(), "period_minutes": period,
            "formula_revision": MINUTE_REVISION, "price_basis": "unadjusted",
            "source": "aiia_stock_realtime_minute_snapshot",
            "expected_bar_end": (elapsed_minutes[-1] if include_partial else expected_grid[-1]).isoformat() + "+08:00" if len(expected_grid) else None,
            "as_of": as_of.astimezone(SHANGHAI).isoformat(), "input_bar_count": len(eligible),
            "missing_bar_count": len(expected_grid) - len(eligible),
            "calculation_anchor": datetime.combine(trade_date, time(9, 30), SHANGHAI).isoformat(),
            "is_finalized": bool(eligible[-1]["is_finalized"]) if eligible else None,
            "bar_end_time": eligible[-1]["bar_end_time"].isoformat() + "+08:00" if eligible else None,
            "unavailable_reason": None}
    if not eligible:
        return {**base, "data_as_of": None, "lag_seconds": None, "input_fingerprint": None,
                "unavailable_reason": "No complete source minutes in the requested session.",
                **dict.fromkeys(MINUTE_FEATURES)}
    payload = _encode(eligible, trade_date, period)
    values = _calculate(payload)
    latest = eligible[-1].get("input_end_time", eligible[-1]["bar_end_time"])
    expected = elapsed_minutes[-1] if include_partial else expected_grid[-1]
    lag = int((expected.to_pydatetime() - latest).total_seconds())
    if max_lag_seconds is not None and lag > max_lag_seconds:
        values = (None,) * len(MINUTE_FEATURES)
        base["unavailable_reason"] = f"Source is {lag}s behind the requested market time; maximum allowed is {max_lag_seconds}s."
    return {**base, "data_as_of": latest.isoformat() + "+08:00",
            "lag_seconds": lag,
            "input_fingerprint": hashlib.sha256(payload.encode()).hexdigest(),
            **dict(zip(MINUTE_FEATURES, values))}


def _read_inputs(*, codes, period, as_of, include_partial, max_lag_seconds,
                 connection_factory=market_connection, cache_root=None):
    if isinstance(codes, str) or not 1 <= len(codes) <= 20:
        raise ValueError("provide 1..20 full stock codes")
    selected = sorted(set(codes))
    if any(not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) for code in selected):
        raise ValueError("stock codes require an exchange suffix")
    current = as_of or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        raise ValueError("as_of requires a timezone")
    current = current.astimezone(SHANGHAI)
    session_grid(current.date(), period)  # Validate before I/O.
    if not isinstance(include_partial, bool):
        raise ValueError("include_partial must be a boolean")
    if max_lag_seconds is not None and (type(max_lag_seconds) is not int or max_lag_seconds < 0):
        raise ValueError("max_lag_seconds must be a nonnegative integer")
    with connection_factory() as conn, conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(selected))
        cur.execute(f"SELECT stk_code FROM kcrp_stock_baseinfo WHERE stk_code IN ({placeholders})", tuple(selected))
        if {r["stk_code"] for r in cur.fetchall()} != set(selected):
            raise ValueError("some codes are not present in the stock master")
        cur.execute("SELECT MAX(calendar_date) AS trade_date FROM aiia_trade_calendar WHERE market_code='CN_A' AND is_trade_day=1 AND calendar_date<=%s", (current.date(),))
        trade_date = cur.fetchone()["trade_date"]
        if trade_date is None:
            raise ValueError("no preceding trading date in calendar")
        def load(selected_codes):
            marks = ",".join(["%s"] * len(selected_codes))
            cur.execute(f"""SELECT stk_code,bar_end_time,latest_price AS close,high_price AS high,
                low_price AS low,open_price AS open,volume,amount,source_bar_count,is_finalized
                FROM aiia_stock_realtime_minute_snapshot
                WHERE stk_code IN ({marks}) AND kline_type=%s AND trade_date=%s
                  AND period_minutes=%s AND bar_end_time<=%s ORDER BY stk_code,bar_end_time""",
                (*[c.split(".")[0] for c in selected_codes], "1m", trade_date, 1,
                 datetime.combine(trade_date, time(15))))
            lookup = {c.split(".")[0]: c for c in selected_codes}
            data = {code: [] for code in selected_codes}
            for row in cur.fetchall():
                data[lookup[row["stk_code"]]].append(row)
            return data
        if connection_factory is market_connection or cache_root is not None:
            from src.services.stock_minute_cache import cached_source_rows
            grouped, hits = cached_source_rows(selected, trade_date, load, root=cache_root)
        else:
            grouped, hits = load(selected), 0
    return selected, current, trade_date, grouped, hits


def read_minute_indicators(*, codes, period: int = 1, as_of: datetime | None = None,
                           include_partial: bool = False, max_lag_seconds: int | None = None,
                           connection_factory=market_connection, cache_root=None) -> dict:
    selected, current, trade_date, grouped, hits = _read_inputs(codes=codes, period=period,
        as_of=as_of, include_partial=include_partial, max_lag_seconds=max_lag_seconds,
        connection_factory=connection_factory, cache_root=cache_root)
    records = [compute_minute_snapshot(aggregate_minute_bars(grouped[code],
                trade_date=trade_date, period=period, as_of=current, include_partial=include_partial), code=code,
                trade_date=trade_date, period=period, as_of=current, include_partial=include_partial,
                max_lag_seconds=max_lag_seconds) for code in selected]
    return {"rows": records, "row_count": len(records), "formula_revision": MINUTE_REVISION,
            "calculation_scope": "same trading day anchored at 09:30; recursive fields use session_ prefix; optional partial period built from complete 1m facts",
            "external_requests": 0, "source_cache_hits": hits}


def compute_minute_series(rows, *, code, trade_date, period, as_of, since=None,
                          max_lag_seconds=None):
    """Completed session bars, retaining holes and pre-window calculation history."""
    if as_of.tzinfo is None:
        raise ValueError("as_of requires a timezone")
    cutoff = as_of.astimezone(SHANGHAI).replace(tzinfo=None)
    if since is not None:
        if since.tzinfo is None or since.astimezone(SHANGHAI).date() != trade_date:
            raise ValueError("since must be timezone-aware and in the same trading session")
        if since > as_of:
            raise ValueError("since must not be later than as_of")
    complete = [r for r in rows if r["is_finalized"] and r["source_bar_count"] == period
                and r["bar_end_time"] <= cutoff]
    snapshot = compute_minute_snapshot(complete, code=code, trade_date=trade_date,
        period=period, as_of=as_of, max_lag_seconds=max_lag_seconds)
    if not complete or snapshot["unavailable_reason"]:
        return [], snapshot
    complete = sorted(complete, key=lambda r: r["bar_end_time"])
    by_time = {r["bar_end_time"].isoformat() + "+08:00": r for r in complete}
    result = []
    for stamp, *values in _calculate_series(_encode(complete, trade_date, period)):
        if since is not None and datetime.fromisoformat(stamp) <= since:
            continue
        bar = by_time.get(stamp, {})
        result.append({"code": code, "trade_date": trade_date.isoformat(),
            "period_minutes": period, "bar_end_time": stamp,
            "is_finalized": bool(bar), "formula_revision": MINUTE_REVISION,
            **{name: float(bar[name]) if bar.get(name) is not None else None
               for name in ("close", "open", "high", "low", "amount")},
            **dict(zip(MINUTE_FEATURES, values))})
    return result, snapshot


def read_minute_window(*, codes, period=5, as_of=None, since=None, max_lag_seconds=None,
                       connection_factory=market_connection, cache_root=None):
    """Read a bounded completed-bar interval; never fill holes or use future bars."""
    selected, current, day, grouped, hits = _read_inputs(codes=codes, period=period,
        as_of=as_of, include_partial=False, max_lag_seconds=max_lag_seconds,
        connection_factory=connection_factory, cache_root=cache_root)
    rows, snapshots = [], []
    for code in selected:
        bars = aggregate_minute_bars(grouped[code], trade_date=day, period=period, as_of=current)
        history, snapshot = compute_minute_series(bars, code=code, trade_date=day,
            period=period, as_of=current, since=since, max_lag_seconds=max_lag_seconds)
        rows.extend(history)
        snapshots.append(snapshot)
    return {"rows": rows, "row_count": len(rows), "snapshots": snapshots,
            "formula_revision": MINUTE_REVISION, "external_requests": 0,
            "source_cache_hits": hits}
