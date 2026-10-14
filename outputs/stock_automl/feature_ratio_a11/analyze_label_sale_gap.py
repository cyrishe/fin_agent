"""Explain A11 target-hit versus actual 09:40 sale on the frozen early cohort."""
from pathlib import Path
import pandas as pd


ROOT = Path(__file__).resolve().parent
BASE = Path("outputs/stock_automl/tree_family_comparison_20261013/feb_jul_hgb_b1_seed42/scores.csv.gz")
NEW = Path("outputs/stock_automl/sklearn_binning_deep_audit_20261013/scores_sklearn_191.csv.gz")


def score_path(version: str, mode: str) -> Path:
    if mode == "ratio":
        return ROOT / f"feb_jul_ratio_sk{version.replace('.', '')}/scores.csv.gz"
    return BASE if version == "1.7.1" else NEW


gates = pd.read_csv(ROOT / "a11_daily_gate.csv", dtype={"symbol6": str})
out = []
for version in ("1.7.1", "1.9.1"):
    for mode in ("none", "ratio"):
        arm = f"sklearn_{version}_{mode}"
        picks = gates.loc[(gates.cohort == "feb_jul") & (gates.arm == arm),
                          ["signal_date", "symbol6", "gate"]]
        scores = pd.read_csv(score_path(version, mode), dtype={"symbol6": str},
                             usecols=["signal_date", "symbol6", "signal_price",
                                      "second_high", "close_40"])
        data = picks.merge(scores, on=["signal_date", "symbol6"], how="left",
                           validate="one_to_one")
        data["high_pct"] = (data.second_high/data.signal_price - 1)*100
        data["sale_pct"] = (data.close_40/data.signal_price - 1)*100
        known = data.high_pct.notna()
        traded = data.gate.eq("trade")
        record = {"version": version, "mode": mode,
                  "top1_known": int(known.sum()),
                  "top1_ge3": int(data.high_pct.ge(3).sum()),
                  "top1_lt1": int(data.high_pct.lt(1).sum()),
                  "ge3_rule_skipped": int((data.high_pct.ge(3) &
                                           data.gate.isin(["below_median", "missing_amount"])).sum()),
                  "sale_trades": int(traded.sum()),
                  "sale_mean_pct": float(data.loc[traded, "sale_pct"].mean()),
                  "high_to_sale_gap_mean_pp": float((data.loc[traded, "high_pct"]-
                                                      data.loc[traded, "sale_pct"]).mean())}
        for label, mask in (("ge3", data.high_pct.ge(3)),
                            ("acceptable", data.high_pct.ge(1) & data.high_pct.lt(3)),
                            ("error", data.high_pct.lt(1))):
            subset = data.loc[traded & mask, "sale_pct"]
            record[f"sold_{label}_count"] = len(subset)
            record[f"sold_{label}_mean_pct"] = float(subset.mean())
        out.append(record)

pd.DataFrame(out).to_csv(ROOT / "a11_label_sale_bridge.csv", index=False,
                         float_format="%.6f")
