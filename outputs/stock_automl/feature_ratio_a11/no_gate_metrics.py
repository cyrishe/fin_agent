"""A11 Top1 and 09:40 results when the amount gate is disabled."""
from pathlib import Path
import pandas as pd


ROOT = Path(__file__).resolve().parent
TREE = Path("outputs/stock_automl/tree_family_comparison_20261013")
AUDIT = Path("outputs/stock_automl/sklearn_binning_deep_audit_20261013")


def scores_path(cohort: str, arm: str) -> Path:
    if arm == "sklearn_1.7.1_none":
        return TREE / f"{cohort}_hgb_b1_seed42/scores.csv.gz"
    if arm == "sklearn_1.9.1_none":
        name = ("scores_sklearn_191.csv.gz" if cohort == "feb_jul"
                else "scores_aug_oct_sklearn_191.csv.gz")
        return AUDIT / name
    version, mode = arm.removeprefix("sklearn_").split("_", 1)
    return ROOT / f"{cohort}_{mode}_sk{version.replace('.', '')}/scores.csv.gz"


def mean_or_none(values: pd.Series):
    return round(float(values.mean()), 5) if len(values) else None


gated = pd.read_csv(ROOT / "a11_core_daily.csv",
                    dtype={"signal_date": str, "symbol6": str}).fillna({"symbol6": ""})
all_daily = []
for (cohort, arm), picks in gated.groupby(["cohort", "arm"], sort=True):
    scores = pd.read_csv(scores_path(cohort, arm),
                         dtype={"signal_date": str, "symbol6": str},
                         usecols=["signal_date", "symbol6", "signal_price", "close_40"])
    frame = picks[["cohort", "arm", "signal_date", "month", "symbol6",
                   "top1_result"]].merge(
        scores, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    if frame.loc[frame.symbol6.ne(""), "signal_price"].isna().any():
        raise ValueError(f"Unmatched Top1: {cohort}/{arm}")
    frame["sale_return_pct"] = (frame.close_40/frame.signal_price - 1)*100
    all_daily.append(frame[["cohort", "arm", "signal_date", "month", "symbol6",
                            "top1_result", "sale_return_pct"]])

daily = pd.concat(all_daily, ignore_index=True).sort_values(
    ["cohort", "arm", "signal_date"])
daily.to_csv(ROOT / "a11_no_gate_daily.csv", index=False, float_format="%.8f")

overall, monthly = [], []
for (cohort, arm), group in daily.groupby(["cohort", "arm"], sort=True):
    top1 = group.symbol6.ne("")
    known = group.top1_result.notna() & group.top1_result.ne("")
    n = int(known.sum())
    sold = group.sale_return_pct.dropna()
    overall.append({"cohort": cohort, "arm": arm,
                    "prediction_days": len(group), "top1_picks": int(top1.sum()),
                    "top1_result_known": n,
                    "top1_pass_pct": round(float(group.top1_result.eq("达标").sum()/n*100), 3),
                    "top1_acceptable_pct": round(float(group.top1_result.eq("可接受").sum()/n*100), 3),
                    "top1_error_pct": round(float(group.top1_result.eq("错误").sum()/n*100), 3),
                    "sale_trades": len(sold),
                    "sale_mean_per_trade_pct": mean_or_none(sold),
                    "no_0940_exit_days": int(top1.sum()-len(sold))})
    for month, block in group.groupby("month", sort=True):
        sales = block.sale_return_pct.dropna()
        monthly.append({"cohort": cohort, "arm": arm, "month": month,
                        "sale_trades": len(sales),
                        "sale_mean_per_trade_pct": mean_or_none(sales),
                        "no_0940_exit_days": int(block.symbol6.ne("").sum()-len(sales))})

pd.DataFrame(overall).to_csv(ROOT / "a11_no_gate_overall.csv", index=False)
pd.DataFrame(monthly).to_csv(ROOT / "a11_no_gate_monthly.csv", index=False)
