"""T-1 model for the rarer tradable deep-V path: ordered 5% dip and class 1."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.experiment_automl_deep_v_d3 import BASE


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")


def main() -> None:
    panel = pd.read_csv(ROOT / "panel.csv.gz", dtype={"symbol6": str})
    full = pd.read_csv(ROOT / "full_universe.csv.gz", dtype={"symbol6": str})
    full["full240"] = True
    panel = panel.merge(full, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    panel["full240"] = panel.full240.eq(True)
    panel["action"] = panel.label.eq(1) & panel.chronological_drop5.eq(True)
    days = sorted(panel.signal_date.unique())
    models = {
        "hgb_action_prior": lambda: HistGradientBoostingClassifier(
            max_iter=70, max_leaf_nodes=7, max_depth=3, min_samples_leaf=80,
            learning_rate=0.07, l2_regularization=3, random_state=17),
        "logit_action_prior": lambda: make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            LogisticRegression(C=0.3, class_weight="balanced", max_iter=500,
                               random_state=17)),
    }
    predictions = []
    metrics = []
    for i in range(20, len(days)):
        day = days[i]
        train = panel[panel.signal_date.isin(days[i - 20:i]) & panel.full240]
        test = panel[panel.signal_date.eq(day)].copy()
        x_train = train[BASE].replace([np.inf, -np.inf], np.nan)
        x_test = test[BASE].replace([np.inf, -np.inf], np.nan)
        for model_name, factory in models.items():
            model = factory()
            model.fit(x_train, train.action.astype(int))
            out = test[["signal_date", "symbol6", "label", "is_event", "action",
                        "full240", "prior_close", "p40"]].copy()
            out["score"] = model.predict_proba(x_test)[:, 1]
            out["model"] = model_name
            out["rank_all"] = out.score.rank(ascending=False, method="first").astype(int)
            predictions.append(out)
            valid = out[out.full240]
            y = valid.action.astype(int)
            top = out.nsmallest(20, "rank_all")
            metrics.append({"day": day, "model": model_name,
                            "train_rows": len(train),
                            "train_action": int(train.action.sum()),
                            "full240": len(valid), "action_count": int(y.sum()),
                            "action_rate": float(y.mean()),
                            "action_auc": roc_auc_score(y, valid.score)
                            if y.nunique() == 2 else np.nan,
                            "action_ap": average_precision_score(y, valid.score)
                            if y.sum() else np.nan,
                            "top20_full": int(top.full240.sum()),
                            "top20_action": int(top.action.sum()),
                            "top20_deepv": int(top.label.eq(1).sum()),
                            "top20_event": int(top.is_event.sum())})
        print(f"scored action {day}", flush=True)
    pred = pd.concat(predictions, ignore_index=True)
    daily = pd.DataFrame(metrics)
    pred.to_csv(ROOT / "predictions_action.csv.gz", index=False, compression="gzip")
    daily.to_csv(ROOT / "daily_action.csv", index=False)
    pred[pred.rank_all.le(20)].to_csv(ROOT / "top20_action.csv", index=False)
    print(daily.groupby("model")[["action_rate", "action_auc", "action_ap",
                                  "top20_action", "top20_deepv"]].mean().round(4), flush=True)


if __name__ == "__main__":
    main()
