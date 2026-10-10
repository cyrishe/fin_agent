"""Check the replay's selected 14:40 prices and observed one-minute trading."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("outputs/stock_automl/mk_archive/mk/none")
OUT = Path("docs/stock_automl_runs/20261010_mk_1m_rolling_backtest")


def run(source: Path = SOURCE, output: Path = OUT) -> dict:
    picks = pd.read_csv(output / "top1_verified.csv", dtype={"symbol6": str})
    parts = []
    for day, group in picks.groupby("signal_date"):
        bars = pd.read_csv(source / f"{day}.csv",
                           usecols=["time", "symbol", "open", "high", "low",
                                    "close", "volume"], dtype={"symbol": str})
        bars = bars[bars.time.str.endswith("14:40:00")].copy()
        bars["symbol6"] = bars.symbol.str[:6]
        bars = bars[["symbol6", "open", "high", "low", "close", "volume"]]
        parts.append(group.merge(bars, on="symbol6", how="left", validate="many_to_one"))
    checked = pd.concat(parts, ignore_index=True)
    if (checked.close.isna().any() or
            not np.allclose(checked.close, checked.signal_price, rtol=0, atol=1e-8)):
        raise ValueError("Selected price differs from the raw 14:40 close")
    checked["name_contains_st"] = checked.name.str.contains("ST", case=False, na=False)
    checked["flat_bar"] = checked[["open", "high", "low", "close"]].nunique(axis=1).eq(1)
    checked["zero_volume"] = checked.volume.eq(0)
    checked["near_five_pct"] = checked.signal_return.between(.048, .052)
    checked["flat_zero_near_five_pct"] = (checked.flat_bar & checked.zero_volume &
                                          checked.near_five_pct)
    checked.to_csv(output / "top1_entry_1440_audit.csv", index=False)
    summary = {}
    for arm, group in checked.groupby("arm"):
        flagged = group.flat_zero_near_five_pct
        summary[arm] = {
            "selected": len(group),
            "name_contains_st": int(group.name_contains_st.sum()),
            "zero_volume_1440": int(group.zero_volume.sum()),
            "flat_zero_near_five_pct": int(flagged.sum()),
            "ge3_sale_in_flagged": int((flagged & group.actual_0940_class.eq("ge3")).sum()),
            "mean_0940_return_pct_unflagged": float(
                group.loc[~flagged, "actual_0940_return"].mean() * 100),
        }
    (output / "entry_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
