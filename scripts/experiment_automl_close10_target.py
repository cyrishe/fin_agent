"""Test the 09:31–09:40 minute-close target against the nine-close target."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from scripts.backtest_automl_tail_top2_cash import buy_quantity
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, frozen_score, query, verify_model
from scripts.experiment_automl_close9_models import COMPACT, DATA
from scripts.experiment_automl_contiguous_close9 import fit, metrics, ranked


OUT = Path("docs/stock_automl_runs/20261009_close10_target")
FIRST = OUT / "exact_0931_closes.csv.gz"
TRAIN_START, TRAIN_END = "2026-08-25", "2026-09-21"


def fetch_first_closes(rows, env_file):
    conn = db_connection(env_file)
    pieces = []
    try:
        for next_day, group in rows.groupby("next_date", sort=True):
            codes = sorted(group.symbol6.unique().tolist())
            placeholders = ",".join(["%s"] * len(codes))
            bars = query(conn, "SELECT stk_code, latest_price, bar_end_time, "
                         "source_snapshot_time, is_finalized, is_fallback FROM "
                         "aiia_stock_realtime_minute_snapshot_full "
                         "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                         f"AND bar_end_time=%s AND stk_code IN ({placeholders})",
                         (next_day, f"{next_day} 09:31:00", *codes))
            if len(bars) != len(codes):
                raise ValueError(f"Missing 09:31 close on {next_day}: {len(bars)}/{len(codes)}")
            if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
                    pd.to_datetime(bars.source_snapshot_time).eq(
                        pd.to_datetime(bars.bar_end_time))).all():
                raise ValueError(f"Non-exact 09:31 bar on {next_day}")
            bars["next_date"] = next_day
            bars["symbol6"] = bars.stk_code.str[:6]
            bars = bars.rename(columns={"latest_price": "close_31"})
            pieces.append(bars[["next_date", "symbol6", "close_31"]])
    finally:
        conn.rollback()
        conn.close()
    first = pd.concat(pieces, ignore_index=True)
    if len(first) != len(rows) or first.duplicated(["next_date", "symbol6"]).any():
        raise ValueError("First-minute price count differs from candidate pool")
    OUT.mkdir(parents=True, exist_ok=True)
    first.to_csv(FIRST, index=False, compression="gzip")
    return first


def cash_simulation(scored, include_first, delay_first_fill=False):
    equity = Decimal("100000")
    trades = []
    first_minute = 31 if include_first else 32
    for date, day in scored.groupby("signal_date", sort=True):
        start_cash = equity
        top = day[day["rank"].le(2)].sort_values("rank")
        if len(top) != 2:
            raise ValueError(f"Expected two picks on {date}")
        for row in top.itertuples():
            entry = Decimal(str(row.entry_1440))
            budget = start_cash * (Decimal("0.6") if row.rank == 1 else Decimal("0.4"))
            quantity = buy_quantity(budget, entry, row.symbol6)
            prices = [(minute, Decimal(str(getattr(row, f"close_{minute:02}"))))
                      for minute in range(first_minute, 41)]
            chosen = next(((minute, price) for minute, price in prices
                           if price > entry * Decimal("1.01")), prices[-1])
            first_minute_trigger = chosen[0] == 31
            if delay_first_fill and first_minute_trigger:
                chosen = (32, Decimal(str(row.close_32)))
            pnl = (chosen[1] - entry) * quantity
            equity += pnl
            trades.append({"signal_date": date, "symbol6": row.symbol6,
                           "rank": row.rank, "shares": quantity,
                           "first_minute_trigger": int(first_minute_trigger),
                           "exit_minute": f"09:{chosen[0]:02}",
                           "exit_price": float(chosen[1]), "pnl": float(pnl)})
    return {"ending_cash": float(equity), "return": float(equity/Decimal("100000")-1),
            "bought": sum(row["shares"] > 0 for row in trades),
            "first_minute_exits": sum(row["shares"] > 0 and
                                      row["first_minute_trigger"] for row in trades)}, trades


def main(env_file, rebuild_first):
    rows = pd.read_csv(DATA, dtype={"symbol6": str})
    first = (fetch_first_closes(rows, env_file) if rebuild_first or not FIRST.exists()
             else pd.read_csv(FIRST, dtype={"symbol6": str}))
    rows = rows.merge(first, on=["next_date", "symbol6"], validate="one_to_one")
    rows["close_31"] = pd.to_numeric(rows.close_31, errors="raise")
    if len(rows) != 14292 or not rows.close_31.gt(0).all():
        raise ValueError("Incomplete first-minute closes")
    close_columns = [f"close_{minute:02}" for minute in range(31, 41)]
    rows["max_close10_return"] = rows[close_columns].max(axis=1) / rows.entry_1440 - 1
    rows["close10_class"] = np.select(
        [rows.max_close10_return.gt(.01), rows.max_close10_return.lt(.005)],
        [1, -1], default=0)
    train = rows[rows.signal_date.between(TRAIN_START, TRAIN_END)].copy()
    outside = rows[~rows.signal_date.between(TRAIN_START, TRAIN_END)].copy()
    if (len(train), len(outside), train.signal_date.nunique(),
            outside.signal_date.nunique()) != (7488, 6804, 20, 20):
        raise ValueError("Continuous training split changed")
    old, _ = verify_model()
    # Same training dates and logistic settings as the frozen model; only target changed.
    fitting_train = train.assign(target_class=train.close10_class)
    prior = Path("docs/stock_automl_runs/20261009_contiguous_close9")
    models = {"原冻结七因子_旧目标": (FEATURES, old, True),
              "已冻结连续七因子_九分钟目标": (
                  FEATURES, joblib.load(prior / "新连续七因子_新目标.joblib"), False),
              "已冻结连续五因子_九分钟目标": (
                  COMPACT, joblib.load(prior / "新连续五因子_新目标.joblib"), False),
              "新连续七因子_含首分钟收盘目标": (FEATURES, fit(fitting_train, FEATURES), False),
              "新连续五因子_含首分钟收盘目标": (COMPACT, fit(fitting_train, COMPACT), False)}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (_, model, frozen) in models.items():
        if name.startswith("新连续"):
            joblib.dump(model, OUT / f"{name}.joblib")
    summary, details, trades = [], [], []
    for name, (columns, model, frozen) in models.items():
        for partition, population in {
            "训练20天": train,
            "非训练20天": outside,
            "训练前14天": outside[outside.signal_date.lt(TRAIN_START)],
            "训练后6天": outside[outside.signal_date.gt(TRAIN_END)],
        }.items():
            scored = ranked(model, columns, population, frozen)
            for target_name, target_col in (("九分钟不含首根", "target_class"),
                                            ("十分钟含首根", "close10_class")):
                labeled = scored.copy()
                labeled["target_class"] = labeled[target_col]
                for n in (2, 5):
                    summary.append({"model": name, "partition": partition,
                                    "target": target_name, **metrics(labeled, n)})
                if partition != "训练20天":
                    cash, trade_rows = cash_simulation(labeled, target_name == "十分钟含首根")
                    summary[-2]["cash_ending_100k"] = cash["ending_cash"]
                    summary[-2]["cash_return"] = cash["return"]
                    summary[-2]["bought"] = cash["bought"]
                    summary[-2]["first_minute_exits"] = cash["first_minute_exits"]
                    if target_name == "十分钟含首根":
                        delayed, _ = cash_simulation(labeled, True, delay_first_fill=True)
                        summary[-2]["cash_if_0931_filled_at_0932"] = delayed["ending_cash"]
                    for trade in trade_rows:
                        trades.append({"model": name, "partition": partition,
                                       "target": target_name, **trade})
            if partition == "非训练20天":
                top5 = scored[scored["rank"].le(5)].copy()
                top5["model"] = name
                top5["old_class"] = top5.target_class
                top5["new_class"] = top5.close10_class
                details.append(top5[["model", "signal_date", "next_date", "rank", "symbol6",
                                     "name", "score", "entry_1440", "close_31",
                                     "max_close9_return", "max_close10_return",
                                     "old_class", "new_class", *FEATURES]])
    summary_frame = pd.DataFrame(summary)
    summary_frame.to_csv(OUT / "ranking_and_cash_comparison.csv", index=False)
    pd.concat(details).to_csv(OUT / "daily_top5.csv", index=False)
    pd.DataFrame(trades).to_csv(OUT / "cash_trade_details.csv", index=False)
    report = {"first_minute_bars": len(first),
              "all_candidate_target_counts": {
                  "nine_closes": {str(k): int(rows.target_class.eq(k).sum()) for k in (1, 0, -1)},
                  "ten_closes": {str(k): int(rows.close10_class.eq(k).sum()) for k in (1, 0, -1)}},
              "new_positive_from_first_minute": int(((rows.close10_class == 1) &
                                                      (rows.target_class != 1)).sum()),
              "training_dates": sorted(train.signal_date.unique().tolist()),
              "out_of_training_dates": sorted(outside.signal_date.unique().tolist()),
              "execution_caveat": "Selling exactly at the 09:31 close after observing that close is optimistic; these are price-path scenarios, not executable returns.",
              "source_sha256": {"nine_close_candidates": hashlib.sha256(DATA.read_bytes()).hexdigest(),
                                "exact_0931_closes": hashlib.sha256(FIRST.read_bytes()).hexdigest()}}
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(summary_frame[summary_frame.top_n.eq(2)].to_string(index=False))
    print(json.dumps({key: report[key] for key in ("all_candidate_target_counts",
                                                  "new_positive_from_first_minute")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--rebuild-first", action="store_true")
    args = parser.parse_args()
    main(args.env_file, args.rebuild_first)
