"""Exploratory walk-forward ablation of pre-signal price, flow, margin and industry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder

from scripts.audit_automl_second_high_extreme_factors import ADDITIONAL
from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_four_class import CLASSES, OUT as BASE_OUT, four_class
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


EXTRA = Path("docs/stock_automl_runs/20261010_second_high_factor_audit/candidate_extra_features.csv.gz")
OUT = Path("docs/stock_automl_runs/20261010_second_high_factor_audit")
NUMERIC = (*FEATURES, *ADDITIONAL)


def run() -> dict:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    extra = pd.read_csv(EXTRA, dtype={"symbol6": str})
    data = base.merge(prices[["next_date", "symbol6", "second_high"]],
                      on=["next_date", "symbol6"], validate="one_to_one")
    data = data.merge(extra, on=["signal_date", "next_date", "symbol6"], validate="one_to_one")
    if len(data) != 14292:
        raise ValueError("Extra features do not match exact candidates")
    data["industry_name"] = data.industry_name.fillna("unknown")
    data["second_high_return"] = data.second_high / data.entry_1440-1
    data["class"] = four_class(data.second_high_return)
    dates = sorted(data.signal_date.unique())
    results = []
    for index in range(20, len(dates)):
        train = data[data.signal_date.isin(dates[index-20:index])]
        test = data[data.signal_date.eq(dates[index])]
        processor = ColumnTransformer([
            ("numeric", SimpleImputer(strategy="median", add_indicator=True), list(NUMERIC)),
            ("industry", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
             ["industry_name"])]).set_output(transform="default")
        model = make_pipeline(processor, HistGradientBoostingClassifier(
            max_iter=60, learning_rate=.05, max_leaf_nodes=7,
            min_samples_leaf=100, l2_regularization=5,
            early_stopping=False, random_state=42))
        columns = [*NUMERIC, "industry_name"]
        model.fit(train[columns], train["class"].astype(str))
        probability = model.predict_proba(test[columns])
        classes = list(model.classes_)
        scored = test[["signal_date", "next_date", "symbol6", "name",
                       "second_high_return", "class"]].copy()
        scored["p_ge3"] = probability[:, classes.index("ge3")]
        scored = scored.sort_values(["p_ge3", "symbol6"], ascending=[False, True])
        scored["rank"] = np.arange(1, len(scored)+1)
        results.append(scored)
        print(f"test {dates[index]}: {len(test)} candidates", flush=True)
    scored = pd.concat(results, ignore_index=True)
    old = pd.read_csv(BASE_OUT / "predictions.csv.gz", dtype={"symbol6": str})
    old = old[old.model.eq("small_boosted_tree")]
    rows = []
    for name, frame in (("seven_factor", old), ("seven_plus_Tminus1_and_industry", scored)):
        chosen = frame[frame["rank"].le(2)]
        rows.append({"model": name, "test_days": frame.signal_date.nunique(),
                     "candidates": len(frame), "top2": len(chosen),
                     "top2_ge3": int(chosen["class"].eq("ge3").sum()),
                     "top2_lt0": int(chosen["class"].eq("lt0").sum()),
                     "top2_lt_minus1": int(chosen.second_high_return.lt(-.01).sum()),
                     "top2_mean_return_pct": float(chosen.second_high_return.mean()*100)})
    OUT.mkdir(parents=True, exist_ok=True)
    scored.to_csv(OUT / "extra_feature_predictions.csv.gz", index=False, compression="gzip")
    scored[scored["rank"].le(2)].to_csv(OUT / "extra_feature_daily_top2.csv", index=False)
    summary = {"comparison": rows,
               "feature_set": [*NUMERIC, "industry_name"],
               "method": "Same prior-20-day folds and small four-class boosted tree; numeric median imputation and industry one-hot are fitted within each fold. Rank by P(>=3%).",
               "interpretation": "Feature names were selected after examining these dates; comparison is exploratory and not independent evidence of improvement.",
               "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (DATA, SECOND_HIGHS, EXTRA,
                                              BASE_OUT / "predictions.csv.gz")}}
    (OUT / "extra_feature_comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(run()["comparison"], ensure_ascii=False, indent=2))
