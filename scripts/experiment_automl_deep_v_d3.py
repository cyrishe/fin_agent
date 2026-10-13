"""Rolling 20-day out-of-day deep-V classification and all-universe shortlists.

The model is trained only on historical >5% global-range events. At T, it
scores every stock with available as-of features. Event labels are never used
to construct the T shortlist.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path("outputs/stock_automl/deep_v_20261013/model_v1")
BASE = [
    "ret1", "ret5", "ret20", "ma5_gap", "ma20_gap", "ma20_slope5",
    "position20", "close_location", "lower_wick", "amplitude_mean14",
    "range5_count20", "volatility20", "amount_ratio20", "amount_trend5",
    "turnover_pct", "flow_main_pct", "flow_main_mean3",
    "market_median_ret1", "market_down_share", "relative_ret1",
]
OPEN = [
    "open_gap", "early_return", "early_range", "early_drawdown",
    "early_position", "early_amount_ratio20", "early_amount_second_first",
    "opening_relative_ret",
]


def make_model(name: str):
    if name.startswith("logit"):
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             LogisticRegression(C=0.3, max_iter=500, random_state=17))
    return HistGradientBoostingClassifier(
        max_iter=70, max_leaf_nodes=7, max_depth=3, min_samples_leaf=80,
        learning_rate=0.07, l2_regularization=3, random_state=17)


def score_day(day: str, model_name: str, frame: pd.DataFrame,
              train: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, dict]:
    opening = model_name.endswith("open")
    if opening:
        frame = frame[frame.p40.notna()].copy()
        train = train[train.p40.notna()].copy()
    train = train[train.is_event].copy()
    y_train = train.label.eq(1).astype(int)
    if y_train.nunique() != 2:
        raise ValueError(f"single train class {day} {model_name}")
    model = make_model(model_name)
    x_train = train[features].replace([np.inf, -np.inf], np.nan)
    x_test = frame[features].replace([np.inf, -np.inf], np.nan)
    model.fit(x_train, y_train)
    frame = frame[["signal_date", "symbol6", "label", "is_event", "prior_close",
                   "p40", "chronological_drop5"]].copy()
    frame["score"] = model.predict_proba(x_test)[:, 1]
    frame["model"] = model_name
    frame["rank_all"] = frame.score.rank(ascending=False, method="first").astype(int)
    event = frame[frame.is_event].copy()
    y = event.label.eq(1).astype(int)
    topn = max(1, int(np.ceil(len(event) * 0.1)))
    top = event.nlargest(topn, "score")
    metric = {
        "day": day, "model": model_name, "train_rows": len(train),
        "train_positive": int(y_train.sum()), "score_universe": len(frame),
        "event_rows": len(event), "event_positive": int(y.sum()),
        "event_base_rate": float(y.mean()) if len(y) else np.nan,
        "event_auc": roc_auc_score(y, event.score) if y.nunique() == 2 else np.nan,
        "event_ap": average_precision_score(y, event.score) if y.nunique() == 2 else np.nan,
        "event_logloss": log_loss(y, event.score, labels=[0, 1]) if len(y) else np.nan,
        "event_top10_precision": float(top.label.eq(1).mean()) if len(top) else np.nan,
        "top20_events": int(frame.nsmallest(20, "rank_all").is_event.sum()),
        "top20_deepv": int(frame.nsmallest(20, "rank_all").label.eq(1).sum()),
    }
    return frame, metric


def main() -> None:
    panel = pd.read_csv(ROOT / "panel.csv.gz", dtype={"symbol6": str})
    panel["is_event"] = panel.is_event.astype(bool)
    panel["label"] = pd.to_numeric(panel.label, errors="coerce")
    days = sorted(panel.signal_date.unique())
    models = {"logit_prior": BASE, "hgb_prior": BASE,
              "hgb_open": BASE + OPEN}
    results: list[pd.DataFrame] = []
    metrics: list[dict] = []
    for i in range(20, len(days)):
        day = days[i]
        train_days = days[i - 20:i]
        train = panel[panel.signal_date.isin(train_days)]
        test = panel[panel.signal_date.eq(day)]
        for name, features in models.items():
            result, metric = score_day(day, name, test, train, features)
            results.append(result)
            metrics.append(metric)
        print(f"scored {day}, historical events {int(train.is_event.sum())}", flush=True)
    predictions = pd.concat(results, ignore_index=True)
    daily = pd.DataFrame(metrics)
    predictions.to_csv(ROOT / "predictions.csv.gz", index=False, compression="gzip")
    daily.to_csv(ROOT / "daily_metrics.csv", index=False)
    predictions[predictions.rank_all.le(20)].to_csv(ROOT / "top20.csv", index=False)
    print({"sklearn": sklearn.__version__, "days": len(days) - 20,
           "models": list(models), "predictions": len(predictions)}, flush=True)
    print(daily.groupby("model")[["event_auc", "event_ap", "event_top10_precision",
                                  "event_base_rate", "top20_events", "top20_deepv"]].mean().round(4),
          flush=True)


if __name__ == "__main__":
    main()
