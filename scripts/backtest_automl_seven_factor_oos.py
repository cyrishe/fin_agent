"""Frozen seven-factor, exact 1m out-of-training cash-path simulation."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.backtest_automl_tail_top2_cash import buy_quantity
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, frozen_score, query, verify_model


ROOT = Path("docs/stock_automl_runs/20261009_seven_factor_cash_backtest")
EXACT = Path("docs/stock_automl_runs/20261008_sina_15m_july/exact_top5_two_models.csv")
OCT8 = ROOT / "20261008_exact_candidates.csv"
TRAIN_START, TRAIN_END = "2026-08-25", "2026-09-21"
MODEL_NAME = "精确训练七因子_去当前价高于均线"
MORNING_MINUTES = tuple(range(571, 581))


def selections(exact_path=EXACT, oct8_path=OCT8):
    old = pd.read_csv(exact_path, dtype={"symbol6": str})
    old = old[(old.model == MODEL_NAME) & old["rank"].le(2)].copy()
    old = old[["signal_date", "next_date", "symbol6", "name", "rank", "score", "class"]]
    if old.signal_date.nunique() != 20 or len(old) != 40:
        raise ValueError("Archived exact out-of-training selections changed")
    if old.signal_date.between(TRAIN_START, TRAIN_END).any():
        raise ValueError("Training dates leaked into the cash backtest")
    model, validation = verify_model()
    current = pd.read_csv(oct8_path, dtype={"symbol6": str})
    if current.symbol6.duplicated().any() or len(current) != 178:
        raise ValueError("October 8 exact candidate archive changed")
    if not (current.entry_finalized.eq(1) & current.entry_fallback.eq(0)).all():
        raise ValueError("October 8 entry bars are not exact")
    current["score"] = frozen_score(model, current[list(FEATURES)])
    current = current.sort_values(["score", "symbol6"], ascending=[False, True]).head(2).copy()
    current["rank"] = (1, 2)
    current["signal_date"] = "2026-10-08"
    current["next_date"] = "2026-10-09"
    current["class"] = pd.NA
    rows = pd.concat([old, current[old.columns]], ignore_index=True)
    if rows.duplicated(["signal_date", "symbol6"]).any() or not rows.groupby("signal_date").size().eq(2).all():
        raise ValueError("Expected exactly two distinct selections per day")
    return rows, validation


def database_bars(rows, env_file):
    dates = sorted(set(rows.signal_date) | (set(rows.next_date) - {"2026-10-09"}))
    symbols = sorted(set(rows.symbol6))
    placeholders = ",".join(["%s"] * len(symbols))
    conn = db_connection(env_file)
    try:
        pieces = []
        for day in dates:
            times = ([f"{day} 14:50:00"] if day in set(rows.signal_date) else [])
            if day in set(rows.next_date):
                times += [f"{day} 09:{m-540:02}:00" for m in MORNING_MINUTES]
            for stamp in times:
                part = query(conn, "SELECT trade_date, stk_code, bar_end_time, open_price, high_price, latest_price, "
                             "is_fallback, is_finalized, source_snapshot_time "
                             "FROM aiia_stock_realtime_minute_snapshot_full "
                             "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                             f"AND bar_end_time=%s AND stk_code IN ({placeholders})",
                             (day, stamp, *symbols))
                if not part.empty:
                    pieces.append(part)
    finally:
        conn.rollback()
        conn.close()
    bars = pd.concat(pieces, ignore_index=True)
    bars["symbol6"] = bars.stk_code.str[:6]
    bars["day"] = bars.trade_date.astype(str).str[:10]
    bars["minute"] = pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M")
    if bars.duplicated(["day", "symbol6", "minute"]).any():
        raise ValueError("Duplicate database minute bars")
    if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
            pd.to_datetime(bars.bar_end_time).eq(pd.to_datetime(bars.source_snapshot_time))).all():
        raise ValueError("Database minute bars are not exact and finalized")
    return bars


def oct9_api_bars(rows):
    output = []
    for symbol in rows.loc[rows.signal_date.eq("2026-10-08"), "symbol6"].unique():
        raw = request_bars(symbol, 0, 120)
        chosen = {int(b["sttDateTime"]["shtTime"]): b for b in raw
                  if int(b["sttDateTime"]["iDate"]) == 20261009 and
                  int(b["sttDateTime"]["shtTime"]) in MORNING_MINUTES}
        if set(chosen) != set(MORNING_MINUTES):
            raise ValueError(f"October 9 morning is not complete for {symbol}")
        for minute, bar in chosen.items():
            output.append({"day": "2026-10-09", "symbol6": symbol,
                           "minute": f"09:{minute-540:02}",
                           "open_price": float(bar["fOpen"]),
                           "high_price": float(bar["fHigh"]),
                           "latest_price": float(bar["fClose"])})
    return pd.DataFrame(output)


def label_oct8_after_ranking(rows, bars):
    """Join next-morning outcomes only after model selections are frozen."""
    rows = rows.copy()
    for index, row in rows[rows.signal_date.eq("2026-10-08")].iterrows():
        entry = bars[(bars.day == row.signal_date) & (bars.symbol6 == row.symbol6) &
                     (bars.minute == "14:50")]
        morning = bars[(bars.day == row.next_date) & (bars.symbol6 == row.symbol6) &
                       bars.minute.isin([f"09:{minute:02}" for minute in range(31, 41)])]
        if len(entry) != 1 or len(morning) != 10 or morning.minute.nunique() != 10:
            raise ValueError("Cannot label October 8 without ten exact morning bars")
        gain = (Decimal(str(morning.high_price.max())) /
                Decimal(str(entry.iloc[0].latest_price)) - 1)
        rows.loc[index, "class"] = (1 if gain > Decimal("0.01") else
                                    -1 if gain < Decimal("0.005") else 0)
    return rows


def sell_observation(entry: Decimal, morning: pd.DataFrame):
    """Use the first observed price strictly above +1%, never an intrabar high."""
    morning = morning.sort_values("minute")
    opening = Decimal(str(morning.iloc[0].open_price))
    if opening > entry * Decimal("1.01"):
        return opening, "09:31开盘", True
    for bar in morning.head(3).itertuples():
        close = Decimal(str(bar.latest_price))
        if close > entry * Decimal("1.01"):
            return close, bar.minute + "收盘", True
    return Decimal(str(morning.iloc[-1].latest_price)), "09:40收盘", False


def simulate(rows, bars, starting_cash=Decimal("100000")):
    equity = starting_cash
    peak = equity
    worst = Decimal(0)
    trades, daily = [], []
    for day, group in rows.groupby("signal_date", sort=True):
        before = equity
        for row in group.sort_values("rank").itertuples():
            entry_rows = bars[(bars.day == day) & (bars.symbol6 == row.symbol6) &
                              (bars.minute == "14:50")]
            morning = bars[(bars.day == row.next_date) & (bars.symbol6 == row.symbol6) &
                           bars.minute.isin([f"09:{m-540:02}" for m in MORNING_MINUTES])]
            if len(entry_rows) != 1 or len(morning) != 10 or morning.minute.nunique() != 10:
                raise ValueError(f"Missing exact entry or ten morning bars: {day} {row.symbol6}")
            entry = Decimal(str(entry_rows.iloc[0].latest_price))
            sell, sell_time, triggered = sell_observation(entry, morning)
            budget = before * (Decimal("0.6") if row.rank == 1 else Decimal("0.4"))
            shares = buy_quantity(budget, entry, row.symbol6)
            pnl = (sell - entry) * shares
            equity += pnl
            trades.append({"信号日": day, "卖出日": row.next_date, "名次": int(row.rank),
                           "代码": row.symbol6, "名称": row.name, "模型分数": round(float(row.score), 8),
                           "买价_14时50分": float(entry), "股数": shares,
                           "分配资金": float(budget), "买入金额": float(entry * shares),
                           "实际买入": int(shares > 0),
                           "次晨开盘价": float(morning.sort_values("minute").iloc[0].open_price),
                           "前三分钟触发": int(triggered), "卖出时点": sell_time,
                           "卖价": float(sell), "该股盈亏": float(pnl),
                           "该股收益率": float(sell / entry - 1)})
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
        daily.append({"信号日": day, "卖出日": group.iloc[0].next_date,
                      "期初资金": float(before), "当日盈亏": float(equity-before),
                      "期末资金": float(equity), "当日收益率": float(equity/before-1)})
    summary = {"signal_days": len(daily), "stocks": len(trades),
               "executed_stocks": sum(t["实际买入"] for t in trades),
               "unaffordable_stocks": sum(not t["实际买入"] for t in trades),
               "morning_triggered": sum(t["前三分钟触发"] and t["实际买入"] for t in trades),
               "starting_cash": float(starting_cash), "ending_cash": float(equity),
               "return": float(equity/starting_cash-1), "max_daily_drawdown": float(worst)}
    return summary, pd.DataFrame(daily), pd.DataFrame(trades)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    rows, validation = selections()
    db = database_bars(rows, args.env_file)
    api = oct9_api_bars(rows)
    db["source"] = "kingdomai_full_1m"
    api["source"] = "kLineData_1m"
    bars = pd.concat([db, api], ignore_index=True)
    needed = set()
    for row in rows.itertuples():
        needed.add((row.signal_date, row.symbol6, "14:50"))
        needed.update((row.next_date, row.symbol6, f"09:{minute-540:02}")
                      for minute in MORNING_MINUTES)
    bars = bars[[key in needed for key in zip(bars.day, bars.symbol6, bars.minute)]].copy()
    rows = label_oct8_after_ranking(rows, bars)
    summary, daily, trades = simulate(rows, bars)
    summary.update({"model": str(Path("docs/stock_automl_runs/20261008_sina_15m_july/exact_trained_seven_factor.joblib")),
                    "training_signal_period": [TRAIN_START, TRAIN_END],
                    "test_signal_period": [rows.signal_date.min(), rows.signal_date.max()],
                    "test_exit_period": [rows.next_date.min(), rows.next_date.max()],
                    "test_dates": sorted(rows.signal_date.unique().tolist()),
                    "model_validation": validation,
                    "top2_label_counts": {str(label): int(rows["class"].eq(label).sum())
                                          for label in (1, 0, -1)},
                    "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in (EXACT, OCT8)}})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bar_path = args.output_dir / "selected_1m_bars.csv"
    bars[["day", "symbol6", "minute", "open_price", "high_price", "latest_price", "source"]].sort_values(
        ["day", "symbol6", "minute"]).to_csv(bar_path, index=False)
    summary["selected_1m_bars_sha256"] = hashlib.sha256(bar_path.read_bytes()).hexdigest()
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    daily.to_csv(args.output_dir / "daily.csv", index=False)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    print(json.dumps({k: summary[k] for k in ("signal_days", "stocks", "morning_triggered",
                                               "ending_cash", "return", "max_daily_drawdown")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
