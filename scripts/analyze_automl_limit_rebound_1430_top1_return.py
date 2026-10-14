"""Audit 14:40-eligible Top1 returns against same-day candidate pools."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scripts.experiment_automl_limit_rebound_1430_r7 import OUT
from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price, normal_limit_rate
from scripts.export_automl_limit_rebound_1430_trade_review import daily_source


def main() -> dict:
    parts = []
    for split in ("train", "valid", "test"):
        frame = pd.read_csv(OUT / f"review_next3_{split}.csv.gz",
                            dtype={"symbol": str, "d1_date": str})
        frame["split"] = split
        parts.append(frame)
    panel = pd.concat(parts, ignore_index=True)
    minute = pd.read_csv(OUT / "review_all_minute_1440.csv.gz",
                         dtype={"symbol": str, "d1_date": str})
    panel = panel.merge(minute[["symbol", "d1_date", "buy_1440"]],
                        on=["symbol", "d1_date"], validate="many_to_one")
    daily, calendar = daily_source(set(panel.symbol))
    day = daily[["symbol", "trade_date", "preclose"]].rename(
        columns={"trade_date": "d1_date"})
    panel = panel.merge(day, on=["symbol", "d1_date"], validate="many_to_one")
    panel["cap"] = [cap_price(price, normal_limit_rate(symbol))
                    for price, symbol in zip(panel.preclose, panel.symbol)]
    panel = panel[panel.buy_1440.notna() & panel.buy_1440.lt(panel.cap - .005)].copy()
    assert len(panel) == 8104 and not panel.duplicated(["split", "symbol", "d1_date"]).any()
    index = {date: pos for pos, date in enumerate(calendar)}
    close = daily.set_index(["symbol", "trade_date"])["close"]
    for horizon in range(1, 6):
        dates = [calendar[index[date] + horizon] if index[date] + horizon < len(calendar)
                 else None for date in panel.d1_date]
        sell = close.reindex(pd.MultiIndex.from_arrays([panel.symbol, dates])).to_numpy(float)
        panel[f"return_d{horizon}"] = (sell / panel.buy_1440 - 1) * 100
    panel = panel.sort_values(["split", "d1_date", "score", "symbol"],
                              ascending=[True, True, False, True])
    panel["rank"] = panel.groupby(["split", "d1_date"]).cumcount() + 1
    pool = panel.groupby(["split", "d1_date"])[[f"return_d{h}" for h in range(1, 6)]].mean()
    top = panel[panel["rank"].eq(1)].copy().set_index(["split", "d1_date"])
    daily_result = top[["symbol", "score", "label_next3", "buy_1440"] +
                       [f"return_d{h}" for h in range(1, 6)]].copy()
    for horizon in range(1, 6):
        key = f"return_d{horizon}"
        daily_result[f"pool_d{horizon}"] = pool[key]
        daily_result[f"excess_d{horizon}"] = daily_result[key] - pool[key]
    daily_result.to_csv(OUT / "review_top1_daily_return_audit.csv", float_format="%.10g")
    rng = np.random.default_rng(20261014)
    result = {"entry": "D1 14:40 minute close, hypothetical", "sets": {}}
    for split, rows in daily_result.groupby(level=0):
        excess = rows.excess_d4.to_numpy()
        draws = rng.choice(excess, size=(20_000, len(excess)), replace=True).mean(axis=1)
        result["sets"][split] = {
            "days": len(rows), "top1_hits": int(rows.label_next3.sum()),
            "top1_mean_return_pct": {f"D{h}": float(rows[f"return_d{h}"].mean())
                                     for h in range(1, 6)},
            "pool_daily_mean_return_pct": {f"D{h}": float(rows[f"pool_d{h}"].mean())
                                           for h in range(1, 6)},
            "top1_minus_pool_d4_pct": float(excess.mean()),
            "top1_minus_pool_d4_day_bootstrap_95_pct": np.quantile(draws, [.025, .975]).tolist(),
            "top1_d4_median_pct": float(rows.return_d4.median()),
            "top1_d4_positive_days": int(rows.return_d4.gt(0).sum()),
            "top1_d4_by_label_pct": {str(label): float(group.return_d4.mean())
                                     for label, group in rows.groupby("label_next3")},
        }
    for split in ("valid", "test"):
        scores = pd.read_csv(OUT / f"next3_d0_only_{split}.csv.gz",
                             dtype={"symbol": str, "d1_date": str})
        subset = panel[panel.split.eq(split)].drop(columns="score").merge(
            scores[["symbol", "d1_date", "score"]],
            on=["symbol", "d1_date"], validate="one_to_one")
        top_d0 = subset.sort_values(["d1_date", "score", "symbol"],
                                    ascending=[True, False, True]).groupby("d1_date").head(1)
        result["sets"][split]["d0_only_top1_hits"] = int(top_d0.label_next3.sum())
        result["sets"][split]["d0_only_top1_d4_return_pct"] = float(top_d0.return_d4.mean())
    (OUT / "review_top1_return_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
