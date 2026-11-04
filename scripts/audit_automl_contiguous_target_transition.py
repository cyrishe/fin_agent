"""Reconcile archived 24/40 high-price result with nine-close 14/40 result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


OLD = Path("docs/stock_automl_runs/20261008_sina_15m_july/exact_top5_two_models.csv")
NEW = Path("docs/stock_automl_runs/20261009_contiguous_close9/daily_top5.csv")
OUT = Path("docs/stock_automl_runs/20261009_contiguous_close9")


def classify(values):
    return np.select([values > .01, values < .005], [1, -1], default=0).astype(int)


def main(env_file: Path):
    archived = pd.read_csv(OLD, dtype={"symbol6": str})
    archived = archived[(archived.model.eq("精确训练七因子_去当前价高于均线")) &
                        archived["rank"].le(2)].copy()
    current = pd.read_csv(NEW, dtype={"symbol6": str})
    current = current[(current.model.eq("原连续七因子_旧目标")) &
                      current.partition.eq("非训练20天") & current.top2.eq(1)].copy()
    pairs = archived.merge(current, on=["signal_date", "symbol6", "rank"],
                           how="outer", validate="one_to_one", indicator=True,
                           suffixes=("_old", "_new"))
    if (len(pairs) != 40 or not pairs._merge.eq("both").all() or
            (pairs.score_old-pairs.score_new).abs().max() > 1e-12):
        raise ValueError("The archived and current model rankings differ")
    conn = db_connection(env_file)
    fetched = []
    try:
        for row in pairs.itertuples():
            signal = query(conn, "SELECT latest_price, is_finalized, is_fallback, "
                           "source_snapshot_time, bar_end_time FROM "
                           "aiia_stock_realtime_minute_snapshot_full "
                           "WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' "
                           "AND period_minutes=1 AND bar_end_time=%s",
                           (row.signal_date, row.symbol6, f"{row.signal_date} 14:50:00"))
            morning = query(conn, "SELECT bar_end_time, high_price, latest_price, "
                            "is_finalized, is_fallback, source_snapshot_time FROM "
                            "aiia_stock_realtime_minute_snapshot_full "
                            "WHERE trade_date=%s AND stk_code=%s AND kline_type='1m' "
                            "AND period_minutes=1 AND bar_end_time BETWEEN %s AND %s "
                            "ORDER BY bar_end_time",
                            (row.next_date_old, row.symbol6,
                             f"{row.next_date_old} 09:31:00",
                             f"{row.next_date_old} 09:40:00"))
            if len(signal) != 1 or len(morning) != 10:
                raise ValueError(f"Incomplete bars for {row.signal_date} {row.symbol6}")
            for bars in (signal, morning):
                if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
                        pd.to_datetime(bars.source_snapshot_time).eq(
                            pd.to_datetime(bars.bar_end_time))).all():
                    raise ValueError(f"Non-exact bar for {row.signal_date} {row.symbol6}")
            high10 = pd.to_numeric(morning.high_price).max()
            high9 = pd.to_numeric(morning.high_price.iloc[1:]).max()
            close9 = pd.to_numeric(morning.latest_price.iloc[1:]).max()
            entry1450 = float(signal.latest_price.iloc[0])
            entry1440 = float(row.entry_1440)
            old_return = high10 / entry1450 - 1
            new_return = close9 / entry1440 - 1
            if abs(old_return-row.target_return) > 1e-8 or abs(
                    new_return-row.max_close9_return) > 1e-8:
                raise ValueError(f"Saved target differs from exact bars: {row.signal_date} {row.symbol6}")
            fetched.append({"signal_date": row.signal_date, "next_date": row.next_date_old,
                            "symbol6": row.symbol6, "name": row.name_old, "rank": row.rank,
                            "score": row.score_old, "entry_1450": entry1450,
                            "entry_1440": entry1440, "high10": high10, "high9": high9,
                            "close9": close9, "high10_vs_1450": old_return,
                            "high10_vs_1440": high10/entry1440-1,
                            "high9_vs_1440": high9/entry1440-1,
                            "close9_vs_1440": new_return})
    finally:
        conn.rollback()
        conn.close()
    detail = pd.DataFrame(fetched).sort_values(["signal_date", "rank"])
    stages = ("high10_vs_1450", "high10_vs_1440", "high9_vs_1440", "close9_vs_1440")
    counts = {}
    for stage in stages:
        labels = classify(detail[stage])
        detail[stage + "_class"] = labels
        counts[stage] = {str(label): int((labels == label).sum()) for label in (1, 0, -1)}
    changes = {}
    for before, after in zip(stages, stages[1:]):
        old_class = detail[before + "_class"]
        new_class = detail[after + "_class"]
        changes[f"{before} -> {after}"] = {
            "positive_lost": int(((old_class == 1) & (new_class != 1)).sum()),
            "positive_gained": int(((old_class != 1) & (new_class == 1)).sum()),
            "critical_gained": int(((old_class != -1) & (new_class == -1)).sum()),
            "critical_cleared": int(((old_class == -1) & (new_class != -1)).sum())}
    OUT.mkdir(parents=True, exist_ok=True)
    detail.to_csv(OUT / "old_new_target_40_stock_reconciliation.csv", index=False)
    summary = {"same_stock_date_rank": 40, "max_score_difference": 0.0,
               "stages": counts, "changes": changes,
               "old_label_cross_new_label": pd.crosstab(
                   detail[stages[0] + "_class"], detail[stages[-1] + "_class"]
               ).to_dict(),
               "maximum_1440_to_1450_entry_price_change": float((
                   detail.entry_1450/detail.entry_1440-1).abs().max())}
    (OUT / "old_new_target_reconciliation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    main(args.env_file)
