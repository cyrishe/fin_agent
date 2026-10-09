"""Show every 09:31–09:40 close for the rolling regression daily top two."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_close9_models import DATA


OUT = Path("docs/stock_automl_runs/20261009_second_high_regression")
PICKS = OUT / "daily_top2_open_review.csv"
FIRST = Path("docs/stock_automl_runs/20261009_close10_target/exact_0931_closes.csv.gz")
EXTREMA = Path("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz")


def main():
    picks = pd.read_csv(PICKS, dtype={"symbol6": str})
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    first = pd.read_csv(FIRST, dtype={"symbol6": str})
    extrema = pd.read_csv(EXTREMA, dtype={"symbol6": str})
    closes = [f"close_{minute}" for minute in range(31, 41)]
    joined = (picks.merge(base[["next_date", "symbol6", *closes[1:]]],
                          on=["next_date", "symbol6"], validate="one_to_one")
              .merge(first, on=["next_date", "symbol6"], validate="one_to_one")
              .merge(extrema[["next_date", "symbol6", "max_close"]],
                     on=["next_date", "symbol6"], validate="one_to_one"))
    if len(joined) != 40 or joined.signal_date.nunique() != 20:
        raise ValueError("Expected 40 daily-top-two stocks on 20 dates")
    if not np.allclose(joined[closes].max(axis=1), joined.max_close):
        raise ValueError("Minute closes differ from exact archived ten-bar maxima")
    returns = joined[closes].div(joined.entry_1440, axis=0).sub(1)
    output = joined[["signal_date", "next_date", "rank", "symbol6", "name",
                     "predicted_pct", "entry_1440", "open_return"]].copy()
    for minute, column in zip(range(31, 41), closes):
        output[f"09:{minute:02}"] = returns[column]
    output["十分钟最高收盘涨幅"] = returns.max(axis=1)
    output["09:40较最高收盘回落百分点"] = (
        output["十分钟最高收盘涨幅"]-returns["close_40"])*100
    output = output.sort_values(["signal_date", "rank"]).reset_index(drop=True)
    output.to_csv(OUT / "daily_top2_minute_close_returns.csv", index=False)
    by_minute = pd.DataFrame([
        {"minute": f"09:{minute:02}", "stocks": len(returns),
         "mean_return_pct": float(returns[column].mean()*100),
         "median_return_pct": float(returns[column].median()*100),
         "above_1pct": int(returns[column].gt(.01).sum()),
         "below_zero": int(returns[column].lt(0).sum())}
        for minute, column in zip(range(31, 41), closes)])
    by_minute.to_csv(OUT / "minute_close_progression.csv", index=False)
    peak = returns.max(axis=1)
    last = returns["close_40"]
    first_hits = []
    for path in returns.to_numpy():
        hits = np.flatnonzero(path > .01)
        if not len(hits):
            continue
        index = int(hits[0])
        later = path[index+1:]
        first_hits.append((path[index], float(later.max()) if len(later) else None,
                           path[-1]))
    summary = {
        "stocks": len(output), "days": output.signal_date.nunique(),
        "price_definition": "Each exact 1m bar close at 09:31–09:40 divided by the previous signal day's 14:40 entry price minus one",
        "max_minute_close_above_1pct": int(peak.gt(.01).sum()),
        "close_0940_above_1pct": int(last.gt(.01).sum()),
        "max_above_1pct_but_0940_not": int((peak.gt(.01) & last.le(.01)).sum()),
        "first_close_above_1pct": int(returns.close_31.gt(.01).sum()),
        "first_close_above_1pct_and_0940_higher": int((
            returns.close_31.gt(.01) & last.gt(returns.close_31)).sum()),
        "first_hit_close_above_1pct": len(first_hits),
        "later_close_higher_than_first_hit": int(sum(
            later is not None and later > first
            for first, later, final in first_hits)),
        "0940_higher_than_first_hit": int(sum(final > first
                                               for first, later, final in first_hits)),
        "0940_not_above_1pct_after_first_hit": int(sum(final <= .01
                                                         for first, later, final in first_hits)),
        "peak_to_0940_drop_median_pct_points": float((peak-last).median()*100),
        "peak_to_0940_drop_over_1pp": int(((peak-last)*100).gt(1).sum()),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (PICKS, DATA, FIRST, EXTREMA)}}
    (OUT / "minute_close_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
