"""Side-by-side A11 metrics for amount-gated and ungated Top1."""
from pathlib import Path
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
KEY = ["cohort", "arm"]
DAY_KEY = KEY + ["signal_date"]
MONTH_KEY = KEY + ["month"]

gated = pd.read_csv(ROOT / "a11_core_overall.csv")
ungated = pd.read_csv(ROOT / "a11_no_gate_overall.csv")
result = gated.merge(ungated, on=KEY, validate="one_to_one",
                     suffixes=("_gated", "_ungated"))
for column in ("prediction_days", "top1_picks", "top1_result_known",
               "top1_pass_pct", "top1_acceptable_pct", "top1_error_pct"):
    if not np.allclose(result[f"{column}_gated"], result[f"{column}_ungated"]):
        raise ValueError(f"Model inputs or outcomes differ across gate modes: {column}")
    result[column] = result[f"{column}_gated"]
    result = result.drop(columns=[f"{column}_gated", f"{column}_ungated"])
result = result[[*KEY, "prediction_days", "top1_picks", "top1_result_known",
                 "top1_pass_pct", "top1_acceptable_pct", "top1_error_pct",
                 "sale_trades_gated", "sale_mean_per_trade_pct_gated",
                 "sale_trades_ungated", "sale_mean_per_trade_pct_ungated",
                 "rule_skip_days", "no_0940_exit_days_gated",
                 "no_0940_exit_days_ungated"]]
result.to_csv(ROOT / "a11_cross_gate_overall.csv", index=False)

gated = pd.read_csv(ROOT / "a11_core_monthly.csv")
ungated = pd.read_csv(ROOT / "a11_no_gate_monthly.csv")
monthly = gated.merge(ungated, on=MONTH_KEY, validate="one_to_one",
                      suffixes=("_gated", "_ungated"))
monthly.to_csv(ROOT / "a11_cross_gate_monthly.csv", index=False)

gated = pd.read_csv(ROOT / "a11_core_daily.csv", dtype={"symbol6": str})
ungated = pd.read_csv(ROOT / "a11_no_gate_daily.csv", dtype={"symbol6": str})
daily = gated.merge(ungated, on=DAY_KEY, validate="one_to_one",
                    suffixes=("_gated", "_ungated"))
for column in ("month", "symbol6", "top1_result"):
    if not daily[f"{column}_gated"].fillna("").equals(
            daily[f"{column}_ungated"].fillna("")):
        raise ValueError(f"Daily Top1 differs across gate modes: {column}")
    daily[column] = daily[f"{column}_gated"]
    daily = daily.drop(columns=[f"{column}_gated", f"{column}_ungated"])
daily = daily[[*DAY_KEY, "month", "symbol6", "top1_result", "rule_skip", "gate",
               "sale_return_pct_gated", "sale_return_pct_ungated"]]
daily.to_csv(ROOT / "a11_cross_gate_daily.csv", index=False, float_format="%.8f")
