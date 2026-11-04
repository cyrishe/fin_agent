"""Read-only census of 2026 sealed limit-up streaks and short cooling gaps."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


START = "2026-01-01"
END = "2026-10-13"
SOURCE_START = "2025-12-25"  # Identify streaks already in progress on January's first day.
OUT = Path("outputs/stock_automl/limit_rebound_20261013")


def fetch() -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "daily_source.csv.gz"
    if path.exists():
        return pd.read_csv(path, dtype={"stk_code": str}, parse_dates=["trade_date"])
    chunks = []
    with db_connection() as conn:
        for month in pd.period_range(SOURCE_START[:7], END[:7], freq="M"):
            lo = max(SOURCE_START, str(month.start_time.date()))
            hi = min(END, str(month.end_time.date()))
            frame = query(conn, "SELECT trade_date,stk_code,preclose,close,high,volume,"
                          "is_limit_price,update_time FROM kcrp_stock_price "
                          "WHERE trade_date BETWEEN %s AND %s", (lo, hi))
            chunks.append(frame)
            print(f"{month}: {len(frame)} rows", flush=True)
        conn.rollback()
    daily = pd.concat(chunks, ignore_index=True)
    daily.to_csv(path, index=False, compression="gzip")
    return daily


def analyze(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily.trade_date).dt.strftime("%Y-%m-%d")
    daily["symbol"] = daily.stk_code.str[:6]
    daily = daily[daily.symbol.str.startswith(("0", "3", "6", "9"))]
    if daily.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate stock-date rows")
    for col in ("preclose", "close", "high", "volume", "is_limit_price"):
        daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily["up"] = daily.is_limit_price.eq(1) & daily.close.gt(daily.preclose)
    daily["ret"] = daily.close.div(daily.preclose).sub(1)
    calendar = sorted(daily.trade_date.unique())
    day_index = {day: i for i, day in enumerate(calendar)}
    events = []
    for symbol, group in daily.groupby("symbol", sort=False):
        by_day = {row.trade_date: row for row in group.itertuples(index=False)}
        for row in group.loc[group.up].itertuples(index=False):
            day = row.trade_date
            if not START <= day <= END:
                continue
            start_i = day_index[day]
            prior = by_day.get(calendar[start_i - 1]) if start_i else None
            if prior is not None and prior.up:
                continue
            end_i = start_i
            while end_i + 1 < len(calendar):
                nxt = by_day.get(calendar[end_i + 1])
                if nxt is None or not nxt.up:
                    break
                end_i += 1
            result = {"symbol": symbol, "first_limit_date": day,
                      "streak_end_date": calendar[end_i],
                      "streak_length": end_i - start_i + 1,
                      "cooling_days": None, "rebound_date": None,
                      "rebound_close_return": None,
                      "rebound_limit_up": False,
                      "ge7_nonlimit_date": None, "ge7_nonlimit_cooling_days": None,
                      "ge7_nonlimit_close_return": None,
                      "outcome": "none"}
            for gap in range(1, 4):
                target_i = end_i + gap + 1
                if target_i >= len(calendar):
                    result["outcome"] = "right_censored"
                    break
                cooling = by_day.get(calendar[end_i + gap])
                target = by_day.get(calendar[target_i])
                if cooling is None or target is None:
                    result["outcome"] = "missing_quote"
                    break
                if cooling.up:
                    raise AssertionError("Streak was not maximal")
                if target.up:
                    result.update(cooling_days=gap, rebound_date=calendar[target_i],
                                  rebound_close_return=float(target.ret),
                                  rebound_limit_up=True, outcome="limit_up")
                    break
                if target.ret >= .07 and result["ge7_nonlimit_date"] is None:
                    result.update(ge7_nonlimit_date=calendar[target_i],
                                  ge7_nonlimit_cooling_days=gap,
                                  ge7_nonlimit_close_return=float(target.ret))
            events.append(result)
    result = pd.DataFrame(events)
    return result, {}


if __name__ == "__main__":
    daily = fetch()
    events, _ = analyze(daily)
    with db_connection() as conn:
        names = query(conn, "SELECT LEFT(stk_code,6) AS symbol,stk_name "
                      "FROM kcrp_stock_baseinfo")
        conn.rollback()
    names = names.drop_duplicates("symbol")
    events = events.merge(names, on="symbol", how="left", validate="many_to_one")
    events.to_csv(OUT / "streak_events.csv", index=False)
    targets = events[events.outcome.isin(("none", "limit_up")) &
                     (events.outcome.eq("limit_up") | events.ge7_nonlimit_date.notna())]
    targets.to_csv(OUT / "target_streaks.csv", index=False)
    observed = events[events.outcome.isin(("none", "limit_up"))]
    daily["trade_date"] = pd.to_datetime(daily.trade_date).dt.strftime("%Y-%m-%d")
    for col in ("preclose", "close", "is_limit_price"):
        daily[col] = pd.to_numeric(daily[col], errors="coerce")
    annual = daily[daily.trade_date.between(START, END)]
    up = annual[annual.is_limit_price.eq(1) & annual.close.gt(annual.preclose)]
    monthly = []
    for month, group in events.groupby(events.first_limit_date.str[:7]):
        known = group[group.outcome.isin(("none", "limit_up"))]
        monthly.append({"month": month, "streaks": len(group),
                        "observed": len(known),
                        "rebound_limit": int(known.outcome.eq("limit_up").sum()),
                        "ge7_nonlimit_only": int((known.ge7_nonlimit_date.notna() &
                                                  known.outcome.ne("limit_up")).sum()),
                        "right_censored": int(group.outcome.eq("right_censored").sum()),
                        "missing_quote": int(group.outcome.eq("missing_quote").sum())})
    pd.DataFrame(monthly).to_csv(OUT / "monthly.csv", index=False)
    summary = {"source": "kingdomai.kcrp_stock_price", "start": START, "end": END,
               "market_trading_days": int(annual.trade_date.nunique()),
               "limit_up_stock_days": len(up), "limit_up_unique_stocks": int(up.stk_code.nunique()),
               "first_board_streaks": len(events),
               "observed_streaks": len(observed),
               "right_censored": int(events.outcome.eq("right_censored").sum()),
               "missing_quote": int(events.outcome.eq("missing_quote").sum()),
               "rebound_limit_streaks": int(observed.outcome.eq("limit_up").sum()),
               "rebound_limit_unique_stocks": int(observed.loc[observed.outcome.eq("limit_up"), "symbol"].nunique()),
               "ge7_nonlimit_only_streaks": int((observed.ge7_nonlimit_date.notna() &
                                                observed.outcome.ne("limit_up")).sum()),
               "rebound_limit_by_cooling_days":
                   {str(int(k)): int(v) for k, v in observed.loc[observed.outcome.eq("limit_up"),
                      "cooling_days"].value_counts().sort_index().items()}}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(events.outcome.value_counts(dropna=False).to_string())
