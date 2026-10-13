"""A11 Top1 result and 09:40 execution metrics using saved daily picks."""
from pathlib import Path
import json

import numpy as np
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


daily_gate = pd.read_csv(ROOT / "a11_daily_gate.csv",
                         dtype={"signal_date": str, "symbol6": str}).fillna({"symbol6": ""})
daily = []
for (cohort, arm), picks in daily_gate.groupby(["cohort", "arm"], sort=True):
    scores = pd.read_csv(scores_path(cohort, arm),
                         dtype={"signal_date": str, "symbol6": str},
                         usecols=["signal_date", "symbol6", "signal_price",
                                  "second_high", "close_40"])
    scored = picks.drop(columns="gross_return").merge(
        scores, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    if scored.signal_date.duplicated().any():
        raise ValueError(f"Multiple daily Top1s: {cohort}/{arm}")
    has_pick = scored.symbol6.ne("")
    if scored.loc[has_pick, "signal_price"].isna().any():
        raise ValueError(f"Unmatched Top1: {cohort}/{arm}")
    target = scored.second_high / scored.signal_price - 1
    scored["top1_result"] = np.select(
        [target.ge(.03), target.ge(.01), target.lt(.01)],
        ["达标", "可接受", "错误"], default="")
    sale = (scored.close_40 / scored.signal_price - 1) * 100
    scored["sale_return_pct"] = sale.where(scored.gate.eq("trade"))
    scored["rule_skip"] = scored.gate.isin(["below_median", "missing_amount"])
    scored["month"] = scored.signal_date.str.slice(0, 7)
    daily.append(scored[["cohort", "arm", "signal_date", "month", "symbol6",
                         "top1_result", "sale_return_pct", "rule_skip", "gate"]])

daily = pd.concat(daily, ignore_index=True).sort_values(
    ["cohort", "arm", "signal_date"])
daily.to_csv(ROOT / "a11_core_daily.csv", index=False, float_format="%.8f")

overall, monthly = [], []
for (cohort, arm), group in daily.groupby(["cohort", "arm"], sort=True):
    known = group.top1_result.ne("")
    n = int(known.sum())
    if n == 0:
        raise ValueError(f"No known Top1 results: {cohort}/{arm}")
    sold = group.sale_return_pct.dropna()
    overall.append({"cohort": cohort, "arm": arm,
                    "prediction_days": len(group),
                    "top1_picks": int(group.symbol6.ne("").sum()),
                    "top1_result_known": n,
                    "top1_result_unknown": int(group.symbol6.ne("").sum())-n,
                    "top1_pass_pct": round(float(group.top1_result.eq("达标").sum()/n*100), 3),
                    "top1_acceptable_pct": round(float(group.top1_result.eq("可接受").sum()/n*100), 3),
                    "top1_error_pct": round(float(group.top1_result.eq("错误").sum()/n*100), 3),
                    "sale_trades": len(sold),
                    "sale_mean_per_trade_pct": mean_or_none(sold),
                    "rule_skip_days": int(group.rule_skip.sum()),
                    "no_0940_exit_days": int(group.gate.eq("missing_outcome").sum())})
    for month, block in group.groupby("month", sort=True):
        sales = block.sale_return_pct.dropna()
        monthly.append({"cohort": cohort, "arm": arm, "month": month,
                        "sale_trades": len(sales),
                        "sale_mean_per_trade_pct": mean_or_none(sales),
                        "rule_skip_days": int(block.rule_skip.sum()),
                        "no_0940_exit_days": int(block.gate.eq("missing_outcome").sum())})

pd.DataFrame(overall).to_csv(ROOT / "a11_core_overall.csv", index=False)
pd.DataFrame(monthly).to_csv(ROOT / "a11_core_monthly.csv", index=False)
(ROOT / "a11_core_metric_definition.json").write_text(json.dumps({
    "top1_outcome_basis": "next trading day's 09:31-09:40 second-high / 14:40 signal price - 1",
    "top1_result_denominator": "Top1 picks with known second-high; before amount gate",
    "pass": ">=3%", "acceptable": ">=1% and <3%", "error": "<1%",
    "sale_basis": "next trading day's 09:40 minute close / 14:40 signal price - 1",
    "sale_mean_denominator": "Known 09:40 sales after original amount gate",
    "month_basis": "signal date month",
    "rule_skip": "Top1 amount below preceding 20 signal-time amounts' median or those amounts incomplete",
    "no_0940_exit": "Passed amount gate but no next-day 09:40 price; excluded from sale mean",
}, ensure_ascii=False, indent=2))
