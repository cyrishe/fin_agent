"""Replay intraday exits for Top1 positions below -3% at 09:40.

Use unadjusted MK one-minute files for 2026 May-July and exact finalized
minute snapshots for September. A trigger uses a completed bar's close and
the next bar's open as the execution-price proxy.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


ROOT = Path("docs/stock_automl_runs")
EARLY = ROOT / "20261011_opening_shape_prototype/exit_predictions.csv"
MAY_JULY = ROOT / "20261011_mk_1m_tradable_st_rolling20/top1_verified.csv"
SEPTEMBER = ROOT / "20261011_aug_oct_independent20/top2_verified.csv"


def picks() -> pd.DataFrame:
    older = pd.read_csv(MAY_JULY, dtype={"symbol6": str})
    newer = pd.read_csv(SEPTEMBER, dtype={"symbol6": str})
    result = pd.concat([
        older.loc[older.selection_rank.eq(1) & older.signal_date.between(
            "2026-05-01", "2026-07-31")],
        newer.loc[newer.selection_rank.eq(1) & newer.signal_date.between(
            "2026-09-01", "2026-09-30")],
    ], ignore_index=True)
    return result[result.actual_0940_return.lt(-0.03)].copy()


def mk_bars(mk_dir: Path, day: str, code: str) -> pd.DataFrame:
    path = mk_dir / f"{day}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    bars = pd.read_csv(path, usecols=["time", "symbol", "open", "close"],
                       dtype={"time": str, "symbol": str})
    bars = bars.loc[bars.symbol.str[:6].eq(code) &
                    bars.time.str[-8:].between("09:40:00", "14:40:00")].copy()
    return bars[["time", "open", "close"]]


def september_bars(db, day: str, code: str) -> pd.DataFrame:
    bars = query(db, """SELECT bar_end_time AS time, open_price AS open,
        latest_price AS close, is_finalized, is_fallback, source_snapshot_time
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND stk_code=%s AND kline_type='1m'
          AND period_minutes=1 AND bar_end_time BETWEEN %s AND %s
        ORDER BY bar_end_time""",
        (day, code, f"{day} 09:40:00", f"{day} 14:40:00"))
    if not (bars.is_finalized.eq(1).all() and bars.is_fallback.eq(0).all() and
            pd.to_datetime(bars.source_snapshot_time).eq(
                pd.to_datetime(bars.time)).all()):
        raise ValueError(f"Non-final minute snapshots: {day} {code}")
    return bars[["time", "open", "close"]]


def evaluate(pick, bars: pd.DataFrame, early: dict) -> dict:
    bars = bars.copy()
    bars["open"] = pd.to_numeric(bars.open, errors="raise")
    bars["close"] = pd.to_numeric(bars.close, errors="raise")
    bars["clock"] = pd.to_datetime(bars.time).dt.strftime("%H:%M:%S")
    bars = bars.sort_values("time").reset_index(drop=True)
    if len(bars) != 211 or bars.clock.iloc[0] != "09:40:00" or bars.clock.iloc[-1] != "14:40:00":
        raise ValueError(f"Incomplete 09:40-14:40 bars: {pick.next_date} {pick.symbol6} {len(bars)}")
    if bars.clock.nunique() != 211 or not (bars.open.gt(0).all() and bars.close.gt(0).all()):
        raise ValueError(f"Invalid minute bars: {pick.next_date} {pick.symbol6}")
    buy = float(pick.signal_price)
    baseline = 100 * (float(bars.close.iloc[0]) / buy - 1)
    if abs(baseline - 100 * float(pick.actual_0940_return)) > 0.002:
        raise ValueError(f"09:40 does not match frozen return: {pick.next_date} {pick.symbol6}")
    result = {"signal_date": pick.signal_date, "next_date": pick.next_date,
              "symbol6": pick.symbol6, "name": pick.name,
              "buy_price": buy, "return_0940_pct": baseline,
              "return_1440_pct": 100 * (float(bars.close.iloc[-1]) / buy - 1)}
    # At 09:40 the loss threshold becomes known; evaluate completed bars
    # beginning at 09:41, and execute at the following bar's open.
    tests = {
        "recover_to_minus1": lambda r: r.close / buy - 1 > -0.01,
        "minute_up1_above_minus3": lambda r: r.close / r.open - 1 > 0.01 and r.close / buy - 1 > -0.03,
        "either": lambda r: r.close / buy - 1 > -0.01 or (
            r.close / r.open - 1 > 0.01 and r.close / buy - 1 > -0.03),
    }
    for label, test in tests.items():
        found = None
        for i in range(1, len(bars)-1):
            if test(bars.iloc[i]):
                found = i
                break
        result[f"{label}_trigger_time"] = bars.clock.iloc[found] if found is not None else ""
        sale_index = found + 1 if found is not None else len(bars)-1
        result[f"{label}_sale_time"] = bars.clock.iloc[sale_index]
        price = bars.open.iloc[sale_index] if found is not None else bars.close.iloc[-1]
        result[f"{label}_return_pct"] = 100 * (float(price) / buy - 1)
        result[f"{label}_delta_pctpt"] = result[f"{label}_return_pct"] - baseline
    for deadline in ("10:00:00", "10:30:00"):
        cutoff = bars.index[bars.clock.eq(deadline)][0]
        trigger = next((i for i in range(1, cutoff + 1)
                        if tests["either"](bars.iloc[i])), None)
        sale_index = trigger + 1 if trigger is not None else cutoff + 1
        label = deadline[:5].replace(":", "")
        result[f"either_by_{label}_trigger_time"] = (
            bars.clock.iloc[trigger] if trigger is not None else "")
        result[f"either_by_{label}_return_pct"] = 100 * (
            float(bars.open.iloc[sale_index]) / buy - 1)
        result[f"either_by_{label}_delta_pctpt"] = (
            result[f"either_by_{label}_return_pct"] - baseline)
    prior = early.get((pick.next_date, pick.symbol6))
    if prior is None:
        raise ValueError(f"Missing opening-exit prediction: {pick.next_date} {pick.symbol6}")
    # README's static model + known T-day features uses p_early_plus_prior.
    result["early_model_action"] = float(prior.p_early_plus_prior) >= 0.65
    result["early_ridge_action"] = bool(prior.ridge_early)
    result["return_0937_pct"] = float(prior.return_0937open_pct)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mk-dir", type=Path, required=True,
                        help="Directory of extracted unadjusted MK day CSVs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    prior = pd.read_csv(EARLY, dtype={"symbol6": str})
    early = {(p.next_date, p.symbol6): p for p in prior.itertuples(index=False)}
    rows = []
    with db_connection(args.env_file) as db:
        for pick in picks().itertuples(index=False):
            bars = (mk_bars(args.mk_dir, pick.next_date, pick.symbol6)
                    if pick.next_date < "2026-08-01" else
                    september_bars(db, pick.next_date, pick.symbol6))
            rows.append(evaluate(pick, bars, early))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output, index=False, float_format="%.8f")
    print(f"wrote {len(rows)} trades to {args.output}")


if __name__ == "__main__":
    main()
