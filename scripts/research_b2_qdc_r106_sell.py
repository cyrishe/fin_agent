"""Replay R86's D2 morning-7% exit on QDC and verify its close-only account."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP

import duckdb
import numpy as np
import pandas as pd

from scripts.research_b2_daily_only_r100 import read_inputs
from scripts import research_b2_qdc_r104 as r104
from scripts import research_b2_qdc_r106 as r106


def extract_d2(frame: pd.DataFrame) -> pd.DataFrame:
    target = r106.OUT / "qdc_selected_d2_minutes.pkl"
    if target.exists():
        return pd.read_pickle(target)
    wanted = frame[["qdc_symbol", "d2_date"]].drop_duplicates()
    parts = []
    for year in sorted(wanted.d2_date.str[:4].astype(int).unique()):
        con = duckdb.connect()
        con.register("wanted", wanted[wanted.d2_date.str.startswith(str(year))])
        path = (str(r104.QDC / "1分钟" / f"{year}.parquet") if year < 2026
                else str(r104.QDC / "1分钟/2026-*.parquet"))
        bars = con.execute("""
            SELECT m.symbol AS qdc_symbol,
              CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE) AS VARCHAR) AS d2_date,
              CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIMESTAMP) AS time,
              m.open, m.high, m.low, m.close, m.volume, m.amount, m.source
            FROM read_parquet(?) m JOIN wanted w ON m.symbol=w.qdc_symbol
              AND CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai'
                  AS DATE) AS VARCHAR)=w.d2_date
            WHERE CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIME)
              BETWEEN TIME '09:30:00' AND TIME '15:00:00'
        """, [path]).df()
        con.close()
        parts.append(bars)
        print(f"D2 minutes {year}: {len(bars)}", flush=True)
    result = pd.concat(parts, ignore_index=True)
    result.to_pickle(target)
    return result


def add_sell(frame: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    groups = {(s, d): g.sort_values("time") for (s, d), g in raw.groupby(["qdc_symbol", "d2_date"])}
    rows = []
    for row in frame.itertuples(index=False):
        bars, audit = r106.normalize_d0(groups[(row.qdc_symbol, row.d2_date)])
        # QDC rounds tiny positive trades to zero shares in a few minute rows.
        # Use one share only for the executable-next-bar predicate, as in R104.
        rounded = bars.volume.eq(0) & bars.amount.gt(0)
        bars.loc[rounded, "volume"] = 1.0
        daily_ok = bool(np.allclose(
            [bars.open.iloc[0], bars.high.max(), bars.low.min(), bars.close.iloc[-1]],
            [row.qdc_d2_open, row.qdc_d2_high, row.qdc_d2_low, row.qdc_d2_close], atol=.011, rtol=0))
        rate = Decimal(str(row.rate))
        floor = float((Decimal(str(row.qdc_d1_close)) * (1 - rate)).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP))
        hit = bars[(bars.time.dt.strftime("%H:%M").le("11:30")) &
                   ((bars.high / row.qdc_d1_close - 1) > .07)]
        sell = row.qdc_d2_close
        clock = "15:00"
        trigger = ""
        proxy = False
        if len(hit):
            first = hit.iloc[0]
            i = bars.index.get_loc(first.name)
            trigger = first.time.strftime("%H:%M")
            eligible = bars.iloc[i + 1:]
            eligible = eligible[eligible.open.gt(floor + .001) & eligible.volume.gt(0)]
            if len(eligible):
                found = eligible.iloc[0]
                sell = float(found.open)
                clock = (found.time - pd.Timedelta(minutes=1)).strftime("%H:%M")
                proxy = True
        rows.append(dict(stk_code=row.stk_code, d0_date=row.d0_date,
                         d2_floor=floor, d2_bars=len(bars), d2_ohlc_match=daily_ok,
                         d2_volume_ratio=bars.volume.sum() / row.qdc_d2_volume if row.qdc_d2_volume else np.nan,
                         sell7=sell, sell7_time=clock, trigger7=trigger, seven_proxy=proxy,
                         close_floor=row.qdc_d2_close <= floor + .001,
                         close_unverified=row.qdc_d2_close <= floor + .001 or bars.volume.iloc[-1] <= 0,
                         seven_unverified=(not proxy) and (row.qdc_d2_close <= floor + .001 or bars.volume.iloc[-1] <= 0)))
    return frame.merge(pd.DataFrame(rows), on=["stk_code", "d0_date"], validate="one_to_one")


def event_account(frame: pd.DataFrame, calendar: list[str], mode: str,
                  start: str, end: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    cash = 1_000_000.0
    peak = cash
    maxdd = 0.0
    hold = {}
    events = {}
    for row in frame.to_dict("records"):
        buy_clock = (pd.Timestamp(row["entry_at"]) - pd.Timedelta(minutes=1)).strftime("%H:%M")
        events.setdefault(row["d1_date"], []).append((buy_clock, 0, row))
        sell_clock = "15:00" if mode == "close" else row["sell7_time"]
        events.setdefault(row["d2_date"], []).append((sell_clock, 1, row))
    ledger = []
    navs = []
    for day in (day for day in calendar if start <= day <= end):
        for _, kind, row in sorted(events.get(day, []), key=lambda item: (item[0], item[1])):
            key = (row["stk_code"], row["d0_date"])
            if kind == 0:
                cost = cash * (.5 if not hold else 1.0)
                assert cost > 0
                shares = cost / (1.001 * row["entry"])
                cash -= cost
                hold[key] = dict(shares=shares, d1_close=row["qdc_d1_close"], cost=cost)
            else:
                lot = hold.pop(key)
                price = row["qdc_d2_close"] if mode == "close" else row["sell7"]
                proceeds = lot["shares"] * (price * .999 +
                                            float(row.get("d2_cash_dividend", 0) or 0))
                cash += proceeds
                ledger.append(dict(stk_code=row["stk_code"], d0_date=row["d0_date"],
                                   d1_date=row["d1_date"], d2_date=row["d2_date"],
                                   cost=lot["cost"], proceeds=proceeds, pnl=proceeds - lot["cost"],
                                   sell_price=price))
            nav = cash + sum(x["shares"] * x["d1_close"] for x in hold.values())
            peak = max(peak, nav)
            maxdd = max(maxdd, 1 - nav / peak)
        nav = cash + sum(x["shares"] * x["d1_close"] for x in hold.values())
        peak = max(peak, nav)
        maxdd = max(maxdd, 1 - nav / peak)
        navs.append(dict(date=day, nav=nav, cash=cash, drawdown=1 - nav / peak,
                         positions=len(hold)))
    assert not hold
    result = dict(buys=len(ledger), return_pct=(cash / 1_000_000 - 1) * 100,
                  drawdown_pct=maxdd * 100, final_nav=cash)
    return result, pd.DataFrame(ledger), pd.DataFrame(navs)


def main() -> None:
    frame = pd.read_pickle(r106.OUT / "risk_panel.pkl")
    assert frame.news_known.all()
    raw = extract_d2(frame)
    frame = add_sell(frame, raw)
    frame.to_pickle(r106.OUT / "risk_sell_panel.pkl")
    frame[["stk_code", "d0_date", "d2_date", "d2_floor", "d2_bars", "d2_ohlc_match",
           "d2_volume_ratio", "sell7", "sell7_time", "trigger7", "seven_proxy",
           "close_floor", "close_unverified", "seven_unverified"]].to_csv(
               r106.OUT / "sell_decisions.csv", index=False)
    _, calendar, _ = read_inputs()
    expected = pd.read_csv(r106.OUT / "account_close.csv")
    rows = []
    for name, mask in [("V2", pd.Series(True, index=frame.index)),
                       ("Q75", ~frame.q75_veto),
                       ("Q75+news", ~(frame.q75_veto | frame.news_veto))]:
        subset = frame[mask]
        for mode in ["close", "morning7"]:
            for year in range(2022, 2027):
                part = subset[subset.d0_date.str[:4].eq(str(year))]
                if part.empty:
                    continue
                result, _, _ = event_account(part, calendar, mode, f"{year}-01-01", part.d2_date.max())
                rows.append(dict(rule=name, sell=mode, period=str(year), **result))
            result, ledger, navs = event_account(subset, calendar, mode, "2022-01-01", subset.d2_date.max())
            rows.append(dict(rule=name, sell=mode, period="2022-26_continuous", **result))
            ledger.to_csv(r106.OUT / f"ledger_{name}_{mode}.csv", index=False)
            navs.to_csv(r106.OUT / f"nav_{name}_{mode}.csv", index=False)
    report = pd.DataFrame(rows)
    for row in expected.itertuples():
        match = report[(report.rule == row.rule) & (report.sell == "close") &
                       (report.period == row.period)].iloc[0]
        assert abs(match.return_pct - row.return_pct) < 1e-7, (row, match)
    report.to_csv(r106.OUT / "account_both_sell_rules.csv", index=False)
    print(report.to_string(index=False), flush=True)
    print(dict(signals=len(frame), d2_ohlc_mismatch=int((~frame.d2_ohlc_match).sum()),
               morning_triggers=int(frame.trigger7.ne("").sum()),
               floor_close=int(frame.close_floor.sum()),
               floor_close_retained=int(frame.loc[~(frame.q75_veto | frame.news_veto), "close_floor"].sum()),
               unverified_close=int(frame.close_unverified.sum()),
               unverified_morning7=int(frame.seven_unverified.sum())), flush=True)


if __name__ == "__main__":
    main()
