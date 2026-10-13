"""Describe B1 factor ranges and the proposed training-fold clipping exposure."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from scripts.run_automl_four_class_pipeline import (
    select_inference_candidates, select_training_samples,
)
from outputs.stock_automl.tree_family_comparison_20261013.run_tree_families import DATASETS


OUT = Path(__file__).resolve().parent
FOUR = ("signal_return", "volume_ratio", "turnover_so_far_pct",
        "float_mv_100m_cny")
TAIL = FOUR[1:]


def summary(values):
    return {"min": float(values.min()),
            "q01": float(values.quantile(.01)),
            "median": float(values.median()),
            "q99": float(values.quantile(.99)),
            "q999": float(values.quantile(.999)),
            "max": float(values.max()),
            "nonfinite": int((~np.isfinite(values)).sum())}


def main():
    report = {}
    for cohort, source in DATASETS.items():
        rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                                   "symbol6": str})
        dates = source["dates"]
        evaluated = rows.loc[rows.signal_date.isin(
            dates[source["first"]:-1]) & rows.second_high.notna()]
        item = {"labeled_candidate_rows": len(evaluated),
                "factors": {name: summary(evaluated[name]) for name in FOUR},
                "prediction_candidate_rows": 0,
                "clip_q01_q99_affected_prediction_rows": {name: 0 for name in TAIL},
                "outside_training_minmax_prediction_rows": {name: 0 for name in FOUR}}
        if cohort == "feb_jul":
            if not evaluated[["ma5", "ma10", "ma20", "min_low_so_far"]].gt(0).all().all():
                raise ValueError("Invalid MA or low values")
            ma_ratio = np.minimum(evaluated.ma5/evaluated.ma10-1,
                                  evaluated.ma10/evaluated.ma20-1)
            low_ratio = (evaluated.min_low_so_far /
                         evaluated[["ma5", "ma10", "ma20"]].max(axis=1)-1)
            if not np.array_equal(ma_ratio.gt(0).to_numpy(),
                                  evaluated.ma_bull_5_10_20.eq(1).to_numpy()):
                raise ValueError("MA sign mismatch")
            if not np.array_equal(low_ratio.gt(0).to_numpy(),
                                  evaluated.all_intraday_lows_above_ma.eq(1).to_numpy()):
                raise ValueError("Low sign mismatch")
            item["ratios"] = {"ma_bull_ratio": summary(ma_ratio),
                              "intraday_low_over_highest_ma": summary(low_ratio)}
        for index in range(source["first"], len(dates)-1):
            train = select_training_samples(rows, dates[index-20:index])
            current = select_inference_candidates(rows, dates[index])
            item["prediction_candidate_rows"] += len(current)
            for name in FOUR:
                item["outside_training_minmax_prediction_rows"][name] += int((
                    current[name].lt(train[name].min()) |
                    current[name].gt(train[name].max())).sum())
            for name in TAIL:
                low, high = train[name].quantile([.01,.99])
                item["clip_q01_q99_affected_prediction_rows"][name] += int((
                    current[name].lt(low) | current[name].gt(high)).sum())
        report[cohort] = item
    (OUT/"feature_ranges.json").write_text(json.dumps(report, ensure_ascii=False,
                                                  indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
