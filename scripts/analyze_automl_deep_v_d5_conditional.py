"""Audit deep-V recovery conditional on an executable >5% minute-close dip.

Reuses D5 out-of-fold ranks and cached full-day Top100 minute paths. The
pre-open policy may trigger any time after 09:31; the 10:00 policies may
trigger only after 10:00. Both enter at the next minute close and exit at
15:00. No model is refitted here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("outputs/stock_automl/deep_v_20261013_d5/model_v1")


def first_dip(frame: pd.DataFrame) -> dict:
    frame = frame.sort_values("bar_end_time")
    prices = pd.to_numeric(frame.latest_price, errors="coerce").to_numpy(float)
    times = frame.bar_end_time.astype(str).str[-8:].to_numpy()
    valid = (len(prices) == 240 and len(set(times)) == 240 and
             times[0] == "09:31:00" and times[-1] == "15:00:00" and
             np.isfinite(prices).all() and (prices > 0).all())
    if not valid:
        return {"path_valid_audit": False, "day_trigger": False}
    peak = np.maximum.accumulate(prices)
    hits = np.flatnonzero(1 - prices / peak > 0.05)
    hits = hits[hits + 1 < len(prices) - 1]
    if not len(hits):
        return {"path_valid_audit": True, "day_trigger": False}
    i = int(hits[0])
    return {"path_valid_audit": True, "day_trigger": True,
            "day_trigger_time": times[i], "day_entry_time": times[i + 1],
            "day_entry": float(prices[i + 1]),
            "day_exit": float(prices[-1]),
            "day_gross_return": float(prices[-1] / prices[i + 1] - 1)}


def main() -> None:
    top = pd.read_csv(ROOT / "two_stage_trades.csv.gz", dtype={"symbol6": str})
    top = top.sort_values(["signal_date", "stage1_rank"])
    records = []
    for day in sorted(top.signal_date.unique()):
        path = ROOT / "top100_paths" / f"{day}.csv.gz"
        bars = pd.read_csv(path, dtype={"symbol6": str})
        for symbol, frame in bars.groupby("symbol6", sort=False):
            records.append({"signal_date": day, "symbol6": symbol, **first_dip(frame)})
    outcomes = pd.DataFrame(records)
    if outcomes.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("duplicate cached minute path")
    top = top.merge(outcomes, on=["signal_date", "symbol6"], how="left",
                    validate="one_to_one")
    top["path_valid_audit"] = top.path_valid_audit.eq(True)
    top["day_trigger"] = top.day_trigger.eq(True)
    if not top.path_valid_audit.eq(top.path_valid).all():
        raise ValueError("cached path validity disagrees with D5")
    if not top.loc[top.stage2_eligible, "path_valid_audit"].all():
        raise ValueError("eligible 10:00 candidate has an invalid path")
    if not (top.loc[top.stage2_eligible, "late_success"].eq(
            top.loc[top.stage2_eligible, "late_trigger"] &
            top.loc[top.stage2_eligible, "label"].eq(1))).all():
        raise ValueError("10:00 target does not match trigger and close label")

    rows = []
    daily = []
    for policy, rank, pool, trigger, gross, ks in (
        ("preopen", "stage1_rank", top, "day_trigger", "day_gross_return", (1, 5, 10, 20, 100)),
        ("prior_at_1000", "stage1_rank", top[top.stage2_eligible],
         "late_trigger", "gross_return", (1, 5, 10, 20, 100)),
        ("morning_at_1000", "stage2_rank", top[top.stage2_eligible],
         "late_trigger", "gross_return", (1, 5, 10, 20, 100)),
    ):
        for k in ks:
            picks = pd.concat([g.nsmallest(k, rank) for _, g in pool.groupby("signal_date")])
            valid = picks[picks.path_valid_audit]
            trades = valid[valid[trigger]]
            success = trades.label.eq(1)
            returns = trades[gross].astype(float)
            rows.append({
                "policy": policy, "topk": k, "selected": len(picks),
                "valid_paths": len(valid), "triggered": len(trades),
                "trigger_rate": len(trades) / len(valid),
                "deepv_after_trigger": int(success.sum()),
                "conditional_deepv_rate": float(success.mean()) if len(trades) else np.nan,
                "positive_trades": int(returns.gt(0).sum()),
                "positive_trade_rate": float(returns.gt(0).mean()) if len(trades) else np.nan,
                "negative_trades": int(returns.lt(0).sum()),
                "zero_trades": int(returns.eq(0).sum()),
                "mean_trade_return": float(returns.mean()) if len(trades) else np.nan,
            })
            for day, group in picks.groupby("signal_date"):
                v = group[group.path_valid_audit]
                t = v[v[trigger]]
                daily.append({"policy": policy, "topk": k, "day": day,
                              "selected": len(group), "valid_paths": len(v),
                              "triggered": len(t), "deepv_after_trigger": int(t.label.eq(1).sum()),
                              "positive_trades": int(t[gross].gt(0).sum())})
    pd.DataFrame(rows).to_csv(ROOT / "trigger_conditional_topk.csv", index=False)
    pd.DataFrame(daily).to_csv(ROOT / "trigger_conditional_daily.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
