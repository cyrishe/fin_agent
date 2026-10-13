"""Build an as-of feature panel for the D1 intraday deep-V sample.

Database reads are cached by month/day under ignored outputs. The T-day label
is only joined after all features have been constructed from T-1 and 09:40.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.benchmark_automl_1440_inference import db_connection, query


ROOT = Path("outputs/stock_automl/deep_v_20261013")
OUT = ROOT / "model_v1"
MONTHS = (("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"),
          ("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-12"))
PRICE_COLUMNS = "trade_date,stk_code,preclose,open,high,low,close,adjpreclose,adjhigh,adjlow,adjclose,volume,amount,turn_ratio,update_time"
FLOW_COLUMNS = "trade_date,stk_code,main_net_buy_value_ratio,huge_net_buy_value_ratio,main_net_buy_value,medium_buy_value,medium_sell_value,small_buy_value,small_sell_value,update_time"
EARLY_SQL = """SELECT LEFT(stk_code,6) AS symbol6, COUNT(*) AS bars10,
 MAX(CASE WHEN TIME(bar_end_time)='09:31:00' THEN latest_price END) AS p31,
 MAX(CASE WHEN TIME(bar_end_time)='09:35:00' THEN latest_price END) AS p35,
 MAX(CASE WHEN TIME(bar_end_time)='09:40:00' THEN latest_price END) AS p40,
 MIN(latest_price) AS low10,MAX(latest_price) AS high10,
 SUM(amount) AS amount10,SUM(volume) AS volume10,
 SUM(CASE WHEN TIME(bar_end_time)<='09:35:00' THEN amount ELSE 0 END) AS amount5a,
 SUM(CASE WHEN TIME(bar_end_time)>'09:35:00' THEN amount ELSE 0 END) AS amount5b,
 SUM(is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time) AS exact10,
 MAX(fetch_time) AS latest_fetch
 FROM aiia_stock_realtime_minute_snapshot_full
 WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
 AND bar_end_time BETWEEN %s AND %s GROUP BY stk_code"""


def fetch_sources(out: Path, days: list[str], fetch_early: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source = out / "source"
    source.mkdir(parents=True, exist_ok=True)
    load_dotenv(".env")
    with db_connection() as conn:
        for start, end in MONTHS:
            month = start[:7]
            for name, table, columns in (("price", "kcrp_stock_price", PRICE_COLUMNS),
                                         ("flow", "kcrp_stock_moneyflow", FLOW_COLUMNS)):
                path = source / f"{name}_{month}.csv.gz"
                if not path.exists():
                    frame = query(conn, f"SELECT {columns} FROM {table} "
                                  "WHERE trade_date BETWEEN %s AND %s", (start, end))
                    frame.to_csv(path, index=False, compression="gzip")
                    print(f"fetched {name} {month}: {len(frame)}", flush=True)
        for day in days if fetch_early else []:
            path = source / f"early_{day}.csv.gz"
            if not path.exists():
                frame = query(conn, EARLY_SQL,
                              (day, f"{day} 09:31:00", f"{day} 09:40:00"))
                frame.insert(0, "day", day)
                frame.to_csv(path, index=False, compression="gzip")
                print(f"fetched early {day}: {len(frame)}", flush=True)
        conn.rollback()
    price = pd.concat([pd.read_csv(source / f"price_{start[:7]}.csv.gz",
                                   dtype={"stk_code": str}) for start, _ in MONTHS],
                      ignore_index=True)
    flow = pd.concat([pd.read_csv(source / f"flow_{start[:7]}.csv.gz",
                                  dtype={"stk_code": str}) for start, _ in MONTHS],
                     ignore_index=True)
    early = (pd.concat([pd.read_csv(source / f"early_{day}.csv.gz",
                                    dtype={"symbol6": str}) for day in days],
                       ignore_index=True) if fetch_early else
             pd.DataFrame(columns=["day", "symbol6", "bars10", "p31", "p35", "p40",
                                   "low10", "high10", "amount10", "volume10",
                                   "amount5a", "amount5b", "exact10", "latest_fetch"]))
    return price, flow, early


def make_panel(price: pd.DataFrame, flow: pd.DataFrame, early: pd.DataFrame,
               events: pd.DataFrame, days: list[str],
               require_20_history: bool = True) -> pd.DataFrame:
    price = price.copy()
    price["symbol6"] = price.stk_code.str[:6]
    price = price[price.symbol6.str.startswith(("0", "3", "6"))].copy()
    price["date"] = price.trade_date.astype(str).str[:10]
    price = price.sort_values(["symbol6", "date"]).reset_index(drop=True)
    if price.duplicated(["symbol6", "date"]).any():
        raise ValueError("duplicate daily price stock-date")
    calendar = sorted(price.date.unique())
    next_day = dict(zip(calendar[:-1], calendar[1:]))
    price["signal_date"] = price.date.map(next_day)
    cutoff = pd.to_datetime(price.signal_date.astype(str) + " 09:40:00", errors="coerce")
    price["price_ready"] = pd.to_datetime(price.update_time, errors="coerce").le(cutoff)
    for column in ("preclose", "open", "high", "low", "close", "adjpreclose",
                   "adjhigh", "adjlow", "adjclose", "volume", "amount", "turn_ratio"):
        price[column] = pd.to_numeric(price[column], errors="coerce").where(price.price_ready)

    flow = flow.copy()
    flow["symbol6"] = flow.stk_code.str[:6]
    flow["date"] = flow.trade_date.astype(str).str[:10]
    flow = flow[flow.symbol6.str.startswith(("0", "3", "6"))].copy()
    if flow.duplicated(["symbol6", "date"]).any():
        raise ValueError("duplicate daily flow stock-date")
    flow["signal_date"] = flow.date.map(next_day)
    flow_cutoff = pd.to_datetime(flow.signal_date.astype(str) + " 09:40:00", errors="coerce")
    flow["flow_ready"] = pd.to_datetime(flow.update_time, errors="coerce").le(flow_cutoff)
    flow_cols = ("main_net_buy_value_ratio", "huge_net_buy_value_ratio",
                 "main_net_buy_value", "medium_buy_value", "medium_sell_value",
                 "small_buy_value", "small_sell_value")
    for column in flow_cols:
        flow[column] = pd.to_numeric(flow[column], errors="coerce").where(flow.flow_ready)
    price = price.merge(flow[["symbol6", "date", *flow_cols]],
                        on=["symbol6", "date"], how="left", validate="one_to_one")

    groups = price.groupby("symbol6", sort=False)
    close = groups.adjclose
    price["ret1"] = close.pct_change(fill_method=None)
    price["ret5"] = close.pct_change(5, fill_method=None)
    price["ret20"] = close.pct_change(20, fill_method=None)
    for n in (5, 10, 20):
        price[f"ma{n}"] = close.transform(lambda s: s.rolling(n, min_periods=n).mean())
    price["ma5_gap"] = price.adjclose / price.ma5 - 1
    price["ma20_gap"] = price.adjclose / price.ma20 - 1
    price["ma20_slope5"] = price.ma20 / groups.ma20.shift(5) - 1
    price["ma_stack"] = (price.ma5 > price.ma10) & (price.ma10 > price.ma20)
    low20 = groups.adjlow.transform(lambda s: s.rolling(20, min_periods=20).min())
    high20 = groups.adjhigh.transform(lambda s: s.rolling(20, min_periods=20).max())
    price["position20"] = (price.adjclose - low20) / (high20 - low20).replace(0, np.nan)
    daily_range = (price.high - price.low).replace(0, np.nan)
    price["close_location"] = (price.close - price.low) / daily_range
    price["lower_wick"] = (price[["open", "close"]].min(axis=1) - price.low) / daily_range
    price["day_body"] = price.close / price.open - 1
    price["amplitude_pct"] = (price.high - price.low) / price.preclose * 100
    price["amplitude_mean14"] = groups.amplitude_pct.transform(
        lambda s: s.rolling(14, min_periods=14).mean())
    price["range5_count20"] = groups.amplitude_pct.transform(
        lambda s: s.gt(5).rolling(20, min_periods=20).sum())
    price["volatility20"] = groups.ret1.transform(
        lambda s: s.rolling(20, min_periods=20).std())
    median20 = groups.amount.transform(lambda s: s.rolling(20, min_periods=20).median())
    mean5 = groups.amount.transform(lambda s: s.rolling(5, min_periods=5).mean())
    price["amount_ratio20"] = price.amount / median20.replace(0, np.nan)
    price["amount_trend5"] = mean5 / median20.replace(0, np.nan)
    price["turnover_pct"] = price.turn_ratio
    price["flow_main_pct"] = price.main_net_buy_value_ratio
    price["flow_huge_pct"] = price.huge_net_buy_value_ratio
    price["flow_main_mean3"] = groups.main_net_buy_value_ratio.transform(
        lambda s: s.rolling(3, min_periods=3).mean())
    price["flow_big_small_gap"] = (
        (price.main_net_buy_value -
         (price.medium_buy_value - price.medium_sell_value) -
         (price.small_buy_value - price.small_sell_value)) /
        price.amount.replace(0, np.nan) * 100
    )
    price["flow_price_divergence"] = price.flow_main_pct * np.sign(-price.ret1)
    market = price.groupby("date").agg(
        market_median_ret1=("ret1", "median"),
        market_down_share=("ret1", lambda s: s.lt(0).mean()),
        market_amount_median_ratio=("amount_ratio20", "median"))
    price = price.join(market, on="date")
    price["relative_ret1"] = price.ret1 - price.market_median_ret1
    price["prior_close"] = price.close

    keep = ("signal_date", "symbol6", "prior_close", "ret1", "ret5", "ret20",
            "ma5_gap", "ma20_gap", "ma20_slope5", "ma_stack", "position20",
            "close_location", "lower_wick", "day_body", "amplitude_mean14",
            "range5_count20", "volatility20", "amount_ratio20", "amount_trend5",
            "turnover_pct", "flow_main_pct", "flow_huge_pct", "flow_main_mean3",
            "flow_big_small_gap", "flow_price_divergence", "market_median_ret1",
            "market_down_share", "market_amount_median_ratio", "relative_ret1",
            "amount", "price_ready")
    panel = price.loc[price.signal_date.isin(days), keep].copy()
    panel = panel.rename(columns={"amount": "prior_amount"})
    panel["ma_stack"] = panel.ma_stack.astype(float)
    valid_prior = panel.price_ready & panel.prior_close.gt(0)
    if require_20_history:
        valid_prior &= panel.ma20_gap.notna() & panel.amplitude_mean14.notna()
    panel = panel[valid_prior].copy()

    early = early.copy()
    early = early.rename(columns={"day": "signal_date"})
    for column in ("bars10", "p31", "p35", "p40", "low10", "high10", "amount10",
                   "volume10", "amount5a", "amount5b", "exact10"):
        early[column] = pd.to_numeric(early[column], errors="coerce")
    early = early[early.bars10.eq(10) & early.exact10.eq(10) &
                  early.p31.gt(0) & early.p40.gt(0)].copy()
    if early.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("duplicate early stock-date")
    early = early.sort_values(["symbol6", "signal_date"])
    early["amount10_med20"] = early.groupby("symbol6").amount10.transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).median())
    panel = panel.merge(early, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    panel["open_gap"] = panel.p31 / panel.prior_close - 1
    panel["early_return"] = panel.p40 / panel.p31 - 1
    panel["early_range"] = 1 - panel.low10 / panel.high10
    panel["early_drawdown"] = 1 - panel.p40 / panel.high10
    panel["early_position"] = (panel.p40 - panel.low10) / (panel.high10 - panel.low10).replace(0, np.nan)
    panel["early_amount_ratio20"] = panel.amount10 / panel.amount10_med20.replace(0, np.nan)
    panel["early_amount_second_first"] = panel.amount5b / panel.amount5a.replace(0, np.nan)
    panel["early_vwap_gap"] = panel.p40 / (panel.amount10 / (panel.volume10 * 100)) - 1
    panel["early_return_vs_prior"] = panel.p40 / panel.prior_close - 1
    early_market = panel[panel.p40.notna()].groupby("signal_date").agg(
        opening_market_median=("early_return_vs_prior", "median"),
        opening_market_down_share=("early_return_vs_prior", lambda s: s.lt(0).mean()))
    panel = panel.join(early_market, on="signal_date")
    panel["opening_relative_ret"] = panel.early_return_vs_prior - panel.opening_market_median

    events = events.copy()
    events["symbol6"] = events.symbol.str[:6]
    events = events[events.symbol6.str.startswith(("0", "3", "6"))]
    events = events[["day", "symbol6", "class", "global_range", "chronological_drop5"]]
    events = events.rename(columns={"day": "signal_date", "class": "label"})
    panel = panel.merge(events, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    panel["is_event"] = panel.label.notna()
    return panel.sort_values(["signal_date", "symbol6"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--data-root", type=Path, default=ROOT)
    parser.add_argument("--skip-early", action="store_true",
                        help="T-1 only panel; no 09:40 source query")
    parser.add_argument("--allow-short-history", action="store_true",
                        help="score stocks with a valid T-1 close even if 20-day features are missing")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    daily = pd.read_csv(args.data_root / "daily_coverage.csv")
    days = daily.loc[daily.full_exact_stocks.gt(0), "day"].astype(str).tolist()
    events = pd.read_csv(args.data_root / "all_events.csv.gz", dtype={"symbol": str})
    price, flow, early = fetch_sources(args.out, days, fetch_early=not args.skip_early)
    panel = make_panel(price, flow, early, events, days,
                       require_20_history=not args.allow_short_history)
    path = args.out / "panel.csv.gz"
    panel.to_csv(path, index=False, compression="gzip")
    print({"rows": len(panel), "days": panel.signal_date.nunique(),
           "events": int(panel.is_event.sum()), "opening_ready": int(panel.p40.notna().sum()),
           "path": str(path)}, flush=True)


if __name__ == "__main__":
    main()
