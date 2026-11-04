"""Chronological, decision-day model comparison for the R1 cooling cohort."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


SOURCE = Path("outputs/stock_automl/limit_rebound_features_r1")
OUT = Path("outputs/stock_automl/limit_rebound_model_r1")
KEY = ["symbol", "first_limit_date", "streak_end_date", "asof_date", "cooling_day"]

# Chosen from financial meaning before reading validation or holdout scores.
SHAPE = (
    "cooling_day", "streak_length", "pre5_return", "first_open_gap",
    "first_range", "first_one_price_proxy", "streak_return", "last_board_range",
    "day_return", "day_open_gap", "day_high_return", "day_low_return",
    "day_range", "day_close_position", "day_close_from_streak_end",
    "path_min_low_from_end", "path_recovery_from_low", "path_up_close_days",
)
FLOW = (
    "first_volume_ratio_pre5", "last_board_turnover_pct",
    "streak_last_first_volume_ratio", "day_volume_ratio_last_board",
    "day_amount_ratio_pre5", "day_turnover_pct", "path_volume_contract_days",
    "path_total_turnover_pct",
)
MARKET = (
    "market_limit_up_count", "market_limit_up_count_change", "market_ge7_count",
    "market_up_fraction", "market_median_return",
)
FEATURE_SETS = {"shape": SHAPE, "shape_flow": SHAPE + FLOW,
                "shape_flow_market": SHAPE + FLOW + MARKET}
MODEL_NAMES = ("logistic", "shallow_boost")


def load_panel(source: Path = SOURCE) -> pd.DataFrame:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    if features.duplicated(KEY).any() or labels.duplicated(KEY).any():
        raise ValueError("Duplicate decision-day key")
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    panel = panel[panel.label_next_observed.eq(True)].copy()
    panel["label_next_limit_up"] = panel.label_next_limit_up.astype(int)
    panel["label_next_limit_or_ge7"] = panel.label_next_limit_or_ge7.astype(int)
    for featureset in FEATURE_SETS.values():
        if missing := set(featureset) - set(panel.columns):
            raise ValueError(f"Missing features: {sorted(missing)}")
    return panel.sort_values(["asof_date", "symbol", "first_limit_date"]).reset_index(drop=True)


def event_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return set(zip(frame.symbol, frame.first_limit_date))


def temporal_split(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """One market-day embargo; purge earlier rows of any later-split event."""
    train = panel[panel.asof_date.le("2026-06-29")].copy()
    valid = panel[panel.asof_date.between("2026-07-01", "2026-08-28")].copy()
    test = panel[panel.asof_date.between("2026-09-01", "2026-10-13")].copy()
    train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(valid))]
    final_train = panel[panel.asof_date.le("2026-08-28")].copy()
    final_train = final_train[~pd.MultiIndex.from_frame(final_train[["symbol", "first_limit_date"]]).isin(event_keys(test))]
    if event_keys(train) & event_keys(valid) or event_keys(final_train) & event_keys(test):
        raise AssertionError("Event crosses a temporal split")
    if train.asof_date.max() >= valid.asof_date.min() or final_train.asof_date.max() >= test.asof_date.min():
        raise AssertionError("Temporal split is not forward-only")
    return train, valid, final_train, test


def make_model(name: str):
    if name == "logistic":
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                             StandardScaler(), LogisticRegression(C=.3, max_iter=1000,
                                                                   random_state=20261013))
    if name == "shallow_boost":
        return HistGradientBoostingClassifier(max_iter=120, learning_rate=.05,
                                              max_leaf_nodes=15, max_depth=3,
                                              min_samples_leaf=100, l2_regularization=5,
                                              early_stopping=False, random_state=20261013)
    raise ValueError(name)


def topk_by_day(frame: pd.DataFrame, score: np.ndarray, target: str, k: int = 5) -> pd.DataFrame:
    ranked = frame[[*KEY, target]].copy()
    ranked["score"] = score
    ranked = ranked.sort_values(["asof_date", "score", "symbol", "first_limit_date"],
                                ascending=[True, False, True, True])
    ranked["rank"] = ranked.groupby("asof_date").cumcount() + 1
    return ranked[ranked["rank"].le(k)].copy()


def evaluate(frame: pd.DataFrame, score: np.ndarray, target: str) -> dict:
    y = frame[target].to_numpy(dtype=int)
    top = topk_by_day(frame, score, target)
    daily_base = frame.groupby("asof_date")[target].mean()
    expected = float(top.asof_date.map(daily_base).sum())
    return {
        "n": len(y), "positive": int(y.sum()), "prevalence": float(y.mean()),
        "average_precision": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)) if len(np.unique(y)) == 2 else None,
        "brier": float(brier_score_loss(y, score)),
        "top5_days": int(top.asof_date.nunique()), "top5_picks": len(top),
        "top5_hits": int(top[target].sum()), "top5_precision": float(top[target].mean()),
        "top5_random_expected_hits": expected,
        "top5_lift": float(top[target].sum() / expected) if expected else None,
    }


def block_bootstrap_top5(frame: pd.DataFrame, score: np.ndarray, target: str,
                         repeats: int = 2000) -> dict:
    """Exploratory interval resampling trading days, not individual stock rows."""
    top = topk_by_day(frame, score, target)
    hits = top.groupby("asof_date")[target].sum()
    counts = top.groupby("asof_date").size()
    base = frame.groupby("asof_date")[target].mean()
    expected = counts * base
    rng = np.random.default_rng(20261013)
    pick = rng.integers(0, len(hits), size=(repeats, len(hits)))
    sampled_hits = hits.to_numpy()[pick].sum(axis=1)
    sampled_counts = counts.to_numpy()[pick].sum(axis=1)
    sampled_expected = expected.to_numpy()[pick].sum(axis=1)
    precision = sampled_hits / sampled_counts
    lift = sampled_hits / sampled_expected
    return {"top5_precision_95pct_day_bootstrap": np.quantile(precision, [.025, .975]).tolist(),
            "top5_lift_95pct_day_bootstrap": np.quantile(lift, [.025, .975]).tolist()}


def file_sha(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def run(source: Path = SOURCE, out: Path = OUT) -> dict:
    panel = load_panel(source)
    train, valid, final_train, test = temporal_split(panel)
    target = "label_next_limit_up"
    candidates = []
    for set_name, columns in FEATURE_SETS.items():
        for model_name in MODEL_NAMES:
            model = make_model(model_name)
            model.fit(train[list(columns)], train[target])
            score = model.predict_proba(valid[list(columns)])[:, 1]
            candidates.append({"feature_set": set_name, "model": model_name,
                               "feature_count": len(columns), **evaluate(valid, score, target)})
    candidates.sort(key=lambda row: (-row["average_precision"], row["brier"],
                                     row["feature_count"], row["model"]))
    winner = candidates[0]
    columns = FEATURE_SETS[winner["feature_set"]]
    model = make_model(winner["model"])
    model.fit(final_train[list(columns)], final_train[target])
    score = model.predict_proba(test[list(columns)])[:, 1]
    test_metrics = evaluate(test, score, target)
    test_metrics.update(block_bootstrap_top5(test, score, target))
    per_cooling = {str(day): evaluate(group, score[test.cooling_day.eq(day).to_numpy()], target)
                   for day, group in test.groupby("cooling_day")}
    # The same selected design is fit anew for the broader, predeclared secondary target.
    secondary = "label_next_limit_or_ge7"
    secondary_model = make_model(winner["model"])
    secondary_model.fit(final_train[list(columns)], final_train[secondary])
    secondary_score = secondary_model.predict_proba(test[list(columns)])[:, 1]
    secondary_metrics = evaluate(test, secondary_score, secondary)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out / "next_limit_model.joblib")
    joblib.dump(secondary_model, out / "next_limit_or_ge7_model.joblib")
    pd.DataFrame(candidates).to_csv(out / "validation_candidates.csv", index=False)
    predictions = test[[*KEY, target, secondary]].copy()
    predictions["p_next_limit_up"] = score
    predictions["p_next_limit_or_ge7"] = secondary_score
    predictions.to_csv(out / "holdout_predictions.csv.gz", index=False, compression="gzip")
    topk_by_day(test, score, target).to_csv(out / "holdout_daily_top5.csv", index=False)
    calibration = pd.DataFrame({"score": score, "actual": test[target].to_numpy()})
    calibration["decile"] = pd.qcut(calibration.score.rank(method="first"), 10,
                                    labels=False) + 1
    calibration = calibration.groupby("decile").agg(
        n=("actual", "size"), mean_predicted=("score", "mean"),
        observed_rate=("actual", "mean"), hits=("actual", "sum")).reset_index()
    calibration.to_csv(out / "holdout_calibration.csv", index=False)
    test_metrics["top_decile_mean_predicted"] = float(calibration.mean_predicted.iloc[-1])
    test_metrics["top_decile_observed_rate"] = float(calibration.observed_rate.iloc[-1])
    summary = {
        "objective": "next market-day sealed limit-up after an observed cooling-day close",
        "secondary_objective": "next market-day sealed limit-up or non-limit close return >=7%",
        "selected_by": "validation average precision; test untouched until selection",
        "selected": {"feature_set": winner["feature_set"], "model": winner["model"],
                     "features": list(columns), "validation": winner},
        "split": {"train": [train.asof_date.min(), train.asof_date.max(), len(train)],
                  "validation": [valid.asof_date.min(), valid.asof_date.max(), len(valid)],
                  "final_fit": [final_train.asof_date.min(), final_train.asof_date.max(), len(final_train)],
                  "holdout": [test.asof_date.min(), test.asof_date.max(), len(test)],
                  "embargo": "2026-06-30 and 2026-08-31 decision dates; cross-split event rows purged"},
        "holdout": test_metrics, "holdout_by_cooling_day": per_cooling,
        "secondary_holdout": secondary_metrics,
        "baselines": {"holdout_prevalence": float(test[target].mean()),
                      "constant_train_probability": float(final_train[target].mean()),
                      "constant_holdout_brier": float(brier_score_loss(
                          test[target], np.full(len(test), final_train[target].mean()))),
                      "day_high_return_holdout_ap": float(average_precision_score(
                          test[target], test.day_high_return.fillna(-1))),
                      "day_high_return_holdout_auc": float(roc_auc_score(
                          test[target], test.day_high_return.fillna(-1)))},
        "provenance": {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                       "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                       "script_sha256": file_sha(Path(__file__)),
                       "features_sha256": file_sha(source / "features.csv.gz"),
                       "labels_sha256": file_sha(source / "labels.csv.gz"),
                       "sklearn_version": sklearn.__version__},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"selected": summary["selected"], "holdout": test_metrics,
                      "secondary_holdout": secondary_metrics}, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
