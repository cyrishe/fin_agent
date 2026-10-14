"""Read-only audit of B1 binary flags and signal-time continuous refinements."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from outputs.stock_automl.tree_family_comparison_20261013.run_tree_families import DATASETS


OUT = Path(__file__).resolve().parent
FLAGS = ("volume_4of5_increasing", "ma_bull_5_10_20",
         "all_intraday_lows_above_ma")


def longest_increasing_subsequence(values):
    """Strict LIS length over the five chronological prior daily volumes."""
    states = []
    for j in range(5):
        if j == 0:
            states.append(np.ones(len(values), dtype=np.int8))
        else:
            earlier = [np.where(values[:, i] < values[:, j], states[i], 0)
                       for i in range(j)]
            states.append((np.maximum.reduce(earlier) + 1).astype(np.int8))
    return np.maximum.reduce(states)


def outcome_summary(frame):
    ge3 = frame.loc[frame.ge3]
    return {
        "rows": int(len(frame)),
        "ge3_rate": float(frame.ge3.mean()) if len(frame) else None,
        "second_high_mean_return_pct": float(frame.second_high_return.mean()*100)
            if len(frame) else None,
        "morning_mean_return_pct": float(frame.morning_return.mean()*100)
            if len(frame) else None,
        "second_high_minus_0940_pct": float((frame.second_high_return-
                                              frame.morning_return).mean()*100)
            if len(frame) else None,
        "morning_positive_rate": float(frame.morning_return.gt(0).mean())
            if len(frame) else None,
        "ge3_and_0940_nonpositive_rows": int(ge3.morning_return.le(0).sum()),
    }


def load_cohort(cohort):
    source = DATASETS[cohort]
    rows = pd.read_csv(source["path"], dtype={"signal_date": str,
                                               "symbol6": str})
    days = source["dates"][source["first"]:-1]
    frame = rows.loc[rows.signal_date.isin(days) & rows.second_high.notna()].copy()
    if sorted(frame.signal_date.unique()) != days:
        raise ValueError(f"Missing evaluation day in {cohort}")
    for flag in FLAGS:
        values = set(frame[flag].dropna().unique())
        if values != {0, 1}:
            raise ValueError(f"Unexpected {flag} values: {values}")
    frame["second_high_return"] = frame.second_high / frame.signal_price - 1
    frame["ge3"] = frame.second_high_return.ge(.03)
    frame["morning_return"] = frame.close_40 / frame.signal_price - 1
    return frame


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"purpose": "descriptive audit, no feature selection or trading backtest",
              "cohorts": {}, "continuous_proxy": {}}
    state_rows = []
    bin_rows = []
    decile_rows = []
    for cohort in DATASETS:
        frame = load_cohort(cohort)
        item = {"labeled_candidates": len(frame),
                "signal_days": int(frame.signal_date.nunique()),
                "first": str(frame.signal_date.min()),
                "last": str(frame.signal_date.max()),
                "baseline": outcome_summary(frame), "flags": {},
                "joint_states": {}}
        for flag in FLAGS:
            daily = frame.groupby("signal_date")[flag].mean()
            item["flags"][flag] = {
                "one_fraction": float(frame[flag].mean()),
                "daily_one_fraction_q10_q50_q90": daily.quantile([.1,.5,.9]).tolist(),
                "zero": outcome_summary(frame.loc[frame[flag].eq(0)]),
                "one": outcome_summary(frame.loc[frame[flag].eq(1)]),
            }
        frame["state"] = frame[list(FLAGS)].astype(int).astype(str).agg("".join, axis=1)
        for state, group in frame.groupby("state"):
            info = outcome_summary(group)
            item["joint_states"][state] = info
            state_rows.append({"cohort": cohort, "state": state, **info})
        report["cohorts"][cohort] = item
        if cohort != "feb_jul":
            continue
        # The frozen recent mother table retained only the three flags.
        volumes = frame[[f"volume_tminus{i}" for i in range(5,0,-1)]].to_numpy(float)
        if not np.isfinite(volumes).all() or not (volumes > 0).all():
            raise ValueError("Prior five daily volumes are incomplete")
        frame["volume_lis_length"] = longest_increasing_subsequence(volumes)
        frame["volume_log_slope_per_day"] = (np.log(volumes) *
                                             np.arange(-2, 3)).sum(axis=1) / 10
        frame["ma_bull_margin_pct"] = (
            np.minimum(frame.ma5-frame.ma10, frame.ma10-frame.ma20) /
            frame.prior_close * 100)
        frame["low_above_ma_margin_pct"] = (
            (frame.min_low_so_far-frame[["ma5", "ma10", "ma20"]].max(axis=1)) /
            frame.prior_close * 100)
        if not np.array_equal(frame.volume_lis_length.ge(4).to_numpy(),
                              frame.volume_4of5_increasing.eq(1).to_numpy()):
            raise ValueError("Volume LIS does not reproduce binary flag")
        if not np.array_equal(frame.ma_bull_margin_pct.gt(0).to_numpy(),
                              frame.ma_bull_5_10_20.eq(1).to_numpy()):
            raise ValueError("MA margin does not reproduce binary flag")
        if not np.array_equal(frame.low_above_ma_margin_pct.gt(0).to_numpy(),
                              frame.all_intraday_lows_above_ma.eq(1).to_numpy()):
            raise ValueError("Low margin does not reproduce binary flag")
        for column in ("volume_lis_length", "volume_log_slope_per_day",
                       "ma_bull_margin_pct", "low_above_ma_margin_pct"):
            value = frame[column]
            report["continuous_proxy"][column] = {
                "min": float(value.min()),
                "q01_q10_q50_q90_q99": value.quantile([.01,.1,.5,.9,.99]).tolist(),
                "max": float(value.max()),
                "unique_values": int(value.nunique()),
            }
        for column in ("volume_lis_length",):
            for label, group in frame.groupby(column):
                bin_rows.append({"feature": column, "bin": str(label),
                                 **outcome_summary(group)})
        edges = [-np.inf,-3,-1,-.3,0,.3,1,3,np.inf]
        for column in ("ma_bull_margin_pct", "low_above_ma_margin_pct"):
            binned = pd.cut(frame[column], edges, right=True)
            for label, group in frame.groupby(binned, observed=True):
                bin_rows.append({"feature": column, "bin": str(label),
                                 **outcome_summary(group)})
            deciles = pd.qcut(frame[column], 10, duplicates="drop")
            for index, (label, group) in enumerate(frame.groupby(deciles,
                                                                  observed=True), 1):
                decile_rows.append({"feature": column, "decile": index,
                                    "bin": str(label),
                                    "min_pct": float(group[column].min()),
                                    "max_pct": float(group[column].max()),
                                    **outcome_summary(group)})
        slope_bins = pd.qcut(frame.volume_log_slope_per_day, 8,
                             duplicates="drop")
        for label, group in frame.groupby(slope_bins, observed=True):
            bin_rows.append({"feature": "volume_log_slope_per_day",
                             "bin": str(label), **outcome_summary(group)})
    pd.DataFrame(state_rows).to_csv(OUT/"joint_states.csv", index=False)
    pd.DataFrame(bin_rows).to_csv(OUT/"continuous_bins.csv", index=False)
    pd.DataFrame(decile_rows).to_csv(OUT/"margin_deciles.csv", index=False)
    (OUT/"summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({cohort: {"n": x["labeled_candidates"],
                                "one_fraction": {k: v["one_fraction"] for k,v in
                                                 x["flags"].items()}}
                      for cohort,x in report["cohorts"].items()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
