"""As-of 10:00 features and completed-V flag from exact minute closes.

Read-only source extraction. The per-day cache makes the experiment resumable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.benchmark_automl_1440_inference import db_connection, query


ROOT = Path("outputs/stock_automl/deep_v_20261013_d5/model_v1")
SQL = """SELECT LEFT(stk_code,6) AS symbol6,bar_end_time,latest_price,amount,volume,
 is_finalized,is_fallback,source_snapshot_time,fetch_time
 FROM aiia_stock_realtime_minute_snapshot_full
 WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
 AND bar_end_time BETWEEN %s AND %s ORDER BY stk_code,bar_end_time"""


def one_day(frame: pd.DataFrame, day: str, full_symbols: set[str]) -> pd.DataFrame:
    frame = frame[frame.symbol6.isin(full_symbols)].copy()
    frame["clock"] = frame.bar_end_time.astype(str).str[-8:]
    clocks = pd.date_range("09:31", periods=30, freq="min").strftime("%H:%M:%S").tolist()
    frame = frame[frame.clock.isin(clocks)]
    frame = frame.sort_values(["symbol6", "clock"])
    grouped = frame.groupby("symbol6", sort=False)
    valid = grouped.agg(n=("clock", "size"), u=("clock", "nunique"),
                        exact=("is_finalized", lambda s: s.astype(int).sum()),
                        fallback=("is_fallback", lambda s: s.astype(int).sum()))
    symbols = valid.index[(valid.n.eq(30) & valid.u.eq(30) &
                           valid.exact.eq(30) & valid.fallback.eq(0))]
    frame = frame[frame.symbol6.isin(symbols)]
    price = frame.pivot(index="symbol6", columns="clock", values="latest_price").reindex(columns=clocks)
    amount = frame.pivot(index="symbol6", columns="clock", values="amount").reindex(columns=clocks)
    volume = frame.pivot(index="symbol6", columns="clock", values="volume").reindex(columns=clocks)
    p = price.apply(pd.to_numeric, errors="coerce").to_numpy(float)
    a = amount.apply(pd.to_numeric, errors="coerce").to_numpy(float)
    v = volume.apply(pd.to_numeric, errors="coerce").to_numpy(float)
    keep = np.isfinite(p).all(axis=1) & (p > 0).all(axis=1) & np.isfinite(a).all(axis=1)
    p, a, v = p[keep], a[keep], v[keep]
    symbols = price.index.to_numpy()[keep]
    peak = np.maximum.accumulate(p, axis=1)
    draw = 1 - p / peak
    triggered_peak = np.full(len(p), np.nan)
    completed = np.zeros(len(p), dtype=bool)
    for j in range(30):
        hit = (draw[:, j] > 0.05) & ~np.isfinite(triggered_peak)
        triggered_peak[hit] = peak[hit, j]
        if j:
            completed |= np.isfinite(triggered_peak) & (p[:, j] >= 0.99 * triggered_peak)
    # The user's whole-day label also allows the low to precede the high.
    # Such a >5% early range ending near its own high is already a completed
    # morning shape, even without a running-peak drawdown.
    completed |= ((1 - p.min(axis=1) / p.max(axis=1) > 0.05) &
                  (1 - p[:, -1] / p.max(axis=1) <= 0.01))
    rets = np.diff(p, axis=1) / p[:, :-1]
    a1 = np.sum(a[:, :15], axis=1)
    a2 = np.sum(a[:, 15:], axis=1)
    vtotal = np.sum(v, axis=1)
    out = pd.DataFrame({
        "signal_date": day, "symbol6": symbols,
        "p31_30": p[:, 0], "p1000": p[:, -1],
        "early30_return": p[:, -1] / p[:, 0] - 1,
        "early30_range": 1 - p.min(axis=1) / p.max(axis=1),
        "early30_drawdown": 1 - p[:, -1] / peak[:, -1],
        "early30_position": (p[:, -1] - p.min(axis=1)) /
                            np.where(p.max(axis=1) > p.min(axis=1), p.max(axis=1) - p.min(axis=1), np.nan),
        "early30_last10_return": p[:, -1] / p[:, 19] - 1,
        "early30_first10_return": p[:, 9] / p[:, 0] - 1,
        "early30_volatility": np.std(rets, axis=1),
        "early30_worst5": np.min(p[:, 5:] / p[:, :-5] - 1, axis=1),
        "early30_amount": a1 + a2,
        "early30_amount_trend": a2 / np.where(a1 > 0, a1, np.nan),
        "early30_vwap_gap": p[:, -1] / np.where(vtotal > 0, (a1 + a2) / (vtotal * 100), np.nan) - 1,
        "v_completed_by_1000": completed,
        "already_down5_at_1000": draw[:, -1] > 0.05,
    })
    out["latest_fetch_30"] = frame.groupby("symbol6").fetch_time.max().reindex(symbols).to_numpy()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT)
    args = parser.parse_args()
    full = pd.read_csv(args.out / "full_universe.csv.gz", dtype={"symbol6": str})
    cache = args.out / "early30"
    cache.mkdir(parents=True, exist_ok=True)
    load_dotenv(".env")
    with db_connection() as conn:
        for day, rows in full.groupby("signal_date", sort=True):
            path = cache / f"{day}.csv.gz"
            if path.exists():
                continue
            raw = query(conn, SQL, (day, f"{day} 09:31:00", f"{day} 10:00:00"))
            out = one_day(raw, day, set(rows.symbol6))
            if len(out) != len(rows):
                raise ValueError(f"D4 full-day universe and first 30 bars disagree: {day} "
                                 f"{len(out)} != {len(rows)}")
            out.to_csv(path, index=False, compression="gzip")
            print(f"early30 {day}: {len(out)}", flush=True)
        conn.rollback()
    all_days = pd.concat([pd.read_csv(cache / f"{day}.csv.gz", dtype={"symbol6": str})
                          for day in sorted(full.signal_date.unique())], ignore_index=True)
    all_days["v_completed_by_1000"] = (
        all_days.v_completed_by_1000.astype(bool) |
        (all_days.early30_range.gt(0.05) & all_days.early30_drawdown.le(0.01)))
    all_days = all_days.sort_values(["symbol6", "signal_date"])
    all_days["early30_amount_med20"] = all_days.groupby("symbol6").early30_amount.transform(
        lambda s: s.shift(1).rolling(20, min_periods=10).median())
    all_days["early30_amount_ratio20"] = all_days.early30_amount / all_days.early30_amount_med20
    all_days.to_csv(args.out / "early30_features.csv.gz", index=False, compression="gzip")
    print({"rows": len(all_days), "days": all_days.signal_date.nunique()}, flush=True)


if __name__ == "__main__":
    main()
