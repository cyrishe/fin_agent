"""Sensitivity: assume every D2 floor close cannot fill until a later non-floor close.

This is a deliberately pessimistic execution scenario, not a causal entry filter.
The unchanged B2/Q75/news picks, month pauses, and ordinary exits remain frozen.
"""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from scripts.research_b2_qdc_r106_sell import event_account
from scripts.replay_limit_rebound_cash_r23 import dump, sha
from scripts.research_b2_qdc_r108 import OUT, ROOT

DOC = ROOT / "docs/stock_automl_runs/20261024_b2_qdc_r108"
QDC = Path("/Volumes/ext/minite_k_data/日线")


def after_floor(frame: pd.DataFrame) -> pd.DataFrame:
    affected = frame[frame.close_floor].copy()
    symbols = affected[["qdc_symbol"]].drop_duplicates()
    con = duckdb.connect()
    con.register("wanted", symbols)
    paths = [str(QDC / f"{year}.parquet") for year in range(2014, 2018)]
    daily = con.execute("""
        SELECT d.symbol AS qdc_symbol,
          CAST(CAST(to_timestamp(d.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE) AS VARCHAR) AS day,
          d.open, d.high, d.low, d.close, d.volume
        FROM read_parquet(?) d JOIN wanted w ON d.symbol=w.qdc_symbol
    """, [paths]).df()
    con.close()
    daily = daily.sort_values(["qdc_symbol", "day"])
    daily["previous"] = daily.groupby("qdc_symbol").close.shift()
    daily["floor"] = [float((Decimal(str(pre)) * Decimal("0.9")).quantize(
        Decimal(".01"), rounding=ROUND_HALF_UP)) if pd.notna(pre) else np.nan
        for pre in daily.previous]
    decisions = []
    for row in affected.itertuples():
        later = daily[daily.qdc_symbol.eq(row.qdc_symbol) & daily.day.gt(row.d2_date)]
        executable = later[later.close.gt(later.floor + .001) & later.volume.gt(0)]
        if len(executable):
            first = executable.iloc[0]
            decisions.append(dict(stk_code=row.stk_code, d0_date=row.d0_date,
                                  original_d2=row.d2_date, exit_date=first.day,
                                  exit_close=float(first.close),
                                  delayed_sessions=int(later.day.le(first.day).sum()),
                                  unresolved=False))
        else:
            decisions.append(dict(stk_code=row.stk_code, d0_date=row.d0_date,
                                  original_d2=row.d2_date, exit_date=None,
                                  exit_close=np.nan, delayed_sessions=len(later),
                                  unresolved=True))
    return pd.DataFrame(decisions)


def account(frame: pd.DataFrame, mode: str, delays: pd.DataFrame) -> tuple[float, int]:
    lookup = delays.set_index(["stk_code", "d0_date"]).to_dict("index")
    events = {}
    for row in frame.itertuples():
        key = (row.stk_code, row.d0_date)
        buy_clock = (pd.Timestamp(row.entry_at) - pd.Timedelta(minutes=1)).strftime("%H:%M")
        events.setdefault(row.d1_date, []).append((buy_clock, 0, key, row, None))
        change = lookup.get(key)
        if change and change["unresolved"]:
            raise ValueError(f"unresolved floor {key}")
        exit_date = change["exit_date"] if change else row.d2_date
        sell_price = change["exit_close"] if change else (
            row.qdc_d2_close if mode == "close" else row.sell7)
        sell_clock = "15:00" if mode == "close" or change else row.sell7_time
        events.setdefault(exit_date, []).append((sell_clock, 1, key, row, sell_price))
    cash, hold, skipped = 1_000_000.0, {}, 0
    for day in sorted(events):
        for _, kind, key, row, price in sorted(events[day], key=lambda x: (x[0], x[1])):
            if kind == 0:
                if cash <= 1e-8:
                    skipped += 1
                    continue
                cost = cash * (.5 if not hold else 1.0)
                hold[key] = cost / (1.001 * row.entry)
                cash -= cost
            elif key in hold:
                cash += hold.pop(key) * (price * .999 +
                           float(getattr(row, "d2_cash_dividend", 0) or 0))
    assert not hold
    return (cash / 1_000_000.0 - 1) * 100, skipped


def main() -> None:
    frame = pd.read_pickle(OUT / "risk_sell_news_panel.pkl")
    frame = frame[~(frame.q75_veto | frame.news_veto)].copy()
    delays = after_floor(frame)
    delays.to_csv(DOC / "floor_delayed_exits.csv", index=False)
    assert not delays.unresolved.any(), delays[delays.unresolved].to_dict("records")
    calendar = json.loads((OUT / "calendar.json").read_text())
    rows = []
    for period in ("2014", "2015", "2016", "2014-16_continuous"):
        part = frame if period.endswith("continuous") else frame[
            frame.model_month.str.startswith(period)]
        for mode in ("close", "morning7"):
            base, _, _ = event_account(part, calendar, mode,
                                        "2014-01-01" if period.endswith("continuous") else f"{period}-01-01",
                                        part.d2_date.max())
            no_delay, ordinary_skipped = account(part, mode, delays.iloc[0:0])
            assert ordinary_skipped == 0 and np.isclose(no_delay, base["return_pct"], atol=1e-8)
            scenario, skipped = account(part, mode, delays)
            rows.append(dict(period=period, sell=mode, buys_original=len(part),
                             delayed_floor=int(part.close_floor.sum()),
                             skipped_buys_after_delay=skipped,
                             original_return_pct=base["return_pct"],
                             delayed_return_pct=scenario,
                             change_pp=scenario-base["return_pct"]))
    result = pd.DataFrame(rows)
    result.to_csv(DOC / "floor_delay_sensitivity.csv", index=False)
    clean_rows = []
    for year in ("2014", "2015", "2016"):
        part = frame[frame.model_month.str.startswith(year)]
        clean = part[part.d0_ohlc_match & part.d2_ohlc_match]
        for mode in ("close", "morning7"):
            base = event_account(part, calendar, mode, f"{year}-01-01", part.d2_date.max())[0]
            test = event_account(clean, calendar, mode, f"{year}-01-01", part.d2_date.max())[0]
            clean_rows.append(dict(year=year, sell=mode, original_trades=len(part),
                                   no_ohlc_conflict_trades=len(clean),
                                   original_return_pct=base["return_pct"],
                                   no_ohlc_conflict_return_pct=test["return_pct"]))
    pd.DataFrame(clean_rows).to_csv(DOC / "ohlc_conflict_sensitivity.csv", index=False)
    manifest_path = DOC / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update({name + "_sha256": sha(DOC / filename) for name, filename in [
        ("floor_delayed_exits", "floor_delayed_exits.csv"),
        ("floor_delay_sensitivity", "floor_delay_sensitivity.csv"),
        ("ohlc_conflict_sensitivity", "ohlc_conflict_sensitivity.csv"),
    ]})
    dump(manifest_path, manifest)
    print(result.to_string(index=False), flush=True)
    print(delays.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
