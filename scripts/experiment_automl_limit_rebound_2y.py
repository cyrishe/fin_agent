"""Forward-only two-year cooling study with size and industry ablations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from scripts.build_automl_limit_rebound_features import KEY
from scripts.enrich_automl_limit_rebound_industry import ADDED
from scripts.experiment_automl_limit_rebound_model import (
    FEATURE_SETS, block_bootstrap_top5, evaluate, event_keys, make_model,
    topk_by_day,
)


SOURCE = Path("outputs/stock_automl/limit_rebound_2y_r1")
OUT = Path("outputs/stock_automl/limit_rebound_2y_model_r1")
BASE = FEATURE_SETS["shape_flow_market"]
SIZE = ("log_float_mv_proxy",)
INDUSTRY = tuple(name for name in ADDED if name not in ("industry_member_count",))
SETS = {"base": BASE, "base_size": BASE + SIZE,
        "base_industry": BASE + INDUSTRY,
        "base_size_industry": BASE + SIZE + INDUSTRY}
TARGET = "label_next_limit_up"
SECONDARY = "label_next_limit_or_ge7"


def split(panel: pd.DataFrame):
    train = panel[panel.asof_date.between("2024-10-01", "2026-03-30")].copy()
    valid = panel[panel.asof_date.between("2026-04-01", "2026-06-29")].copy()
    test = panel[panel.asof_date.between("2026-07-01", "2026-10-12")].copy()
    train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(valid))]
    refit = panel[panel.asof_date.between("2024-10-01", "2026-06-29")].copy()
    refit = refit[~pd.MultiIndex.from_frame(refit[["symbol", "first_limit_date"]]).isin(event_keys(test))]
    if event_keys(train) & event_keys(valid) or event_keys(refit) & event_keys(test):
        raise AssertionError("An event crosses split sides")
    return train, valid, refit, test


def daily_topk(frame: pd.DataFrame, score: np.ndarray, k: int, target: str) -> dict:
    picks = topk_by_day(frame, score, target, k)
    base = frame.groupby("asof_date")[target].mean()
    expected = float(picks.asof_date.map(base).sum())
    return {"days": int(picks.asof_date.nunique()), "picks": len(picks),
            "hits": int(picks[target].sum()), "precision": float(picks[target].mean()) if len(picks) else None,
            "same_day_random_expected_hits": expected,
            "lift": float(picks[target].sum() / expected) if expected else None}


def policy_results(frame: pd.DataFrame, score: np.ndarray, validation_top_score_cut: float,
                   target: str) -> dict:
    result = {f"top{k}": daily_topk(frame, score, k, target) for k in (1, 3, 5, 10)}
    top3 = topk_by_day(frame, score, target, 3)
    top3 = top3[top3.groupby("asof_date").score.transform("max").ge(validation_top_score_cut)]
    base = frame.groupby("asof_date")[target].mean()
    expected = float(top3.asof_date.map(base).sum())
    result["top3_high_score_day"] = {
        "threshold_from_validation": validation_top_score_cut,
        "days": int(top3.asof_date.nunique()), "picks": len(top3),
        "hits": int(top3[target].sum()),
        "precision": float(top3[target].mean()) if len(top3) else None,
        "same_day_random_expected_hits": expected,
        "lift": float(top3[target].sum() / expected) if expected else None,
    }
    # Contextual rule is predefined. It may abstain when fewer than three hot candidates exist.
    if "industry_return_5_rank" in frame:
        hot = frame[frame.industry_return_5_rank.ge(.75) &
                    frame.industry_limit_up_count.ge(2)]
        hot_score = score[frame.industry_return_5_rank.ge(.75).to_numpy() &
                          frame.industry_limit_up_count.ge(2).to_numpy()]
        if len(hot):
            picks = topk_by_day(hot, hot_score, target, 3)
            # Compare with same-day random draws from the full decision universe.
            expected = float(picks.asof_date.map(base).sum())
            result["top3_hot_industry"] = {
                "days": int(picks.asof_date.nunique()), "picks": len(picks),
                "hits": int(picks[target].sum()), "precision": float(picks[target].mean()),
                "same_day_random_expected_hits_full_universe": expected,
                "lift_vs_full_universe": float(picks[target].sum() / expected) if expected else None,
            }
    return result


def same_day_random_test(frame: pd.DataFrame, score: np.ndarray, target: str,
                         repeats: int = 10000) -> dict:
    """Random Top 5 within each day's unchanged candidate pool."""
    observed = int(topk_by_day(frame, score, target, 5)[target].sum())
    groups = [g[target].to_numpy(dtype=int) for _, g in frame.groupby("asof_date")]
    rng = np.random.default_rng(20261013)
    null = np.empty(repeats, dtype=int)
    for repeat in range(repeats):
        null[repeat] = sum(int(rng.choice(y, size=min(5, len(y)), replace=False).sum())
                           for y in groups)
    return {"observed_hits": observed, "null_mean_hits": float(null.mean()),
            "one_sided_p": float((1 + np.count_nonzero(null >= observed)) / (repeats + 1)),
            "repeats": repeats}


def run(source: Path = SOURCE, out: Path = OUT) -> dict:
    features = pd.read_csv(source / "features_industry.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    if features.duplicated(KEY).any() or labels.duplicated(KEY).any():
        raise ValueError("Duplicate stock-event-decision key")
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    panel = panel[panel.label_next_observed.eq(True)].copy()
    for target in (TARGET, SECONDARY):
        panel[target] = panel[target].astype(int)
    for columns in SETS.values():
        if missing := set(columns) - set(panel):
            raise ValueError(f"Missing columns {sorted(missing)}")
    train, valid, refit, test = split(panel)
    candidates = []
    # All designs fixed before validating. Test data play no part in selection.
    for set_name, columns in SETS.items():
        for model_name in ("logistic", "shallow_boost"):
            model = make_model(model_name)
            model.fit(train[list(columns)], train[TARGET])
            score = model.predict_proba(valid[list(columns)])[:, 1]
            candidates.append({"feature_set": set_name, "model": model_name,
                               "feature_count": len(columns), **evaluate(valid, score, TARGET)})
    candidates.sort(key=lambda item: (-item["average_precision"], item["brier"], item["feature_count"]))
    winner = candidates[0]
    columns = SETS[winner["feature_set"]]
    validation_model = make_model(winner["model"])
    validation_model.fit(train[list(columns)], train[TARGET])
    validation_score = validation_model.predict_proba(valid[list(columns)])[:, 1]
    validation_day_top = pd.DataFrame({"date": valid.asof_date, "score": validation_score}).groupby("date").score.max()
    high_score_cut = float(validation_day_top.quantile(.75))
    model = make_model(winner["model"])
    model.fit(refit[list(columns)], refit[TARGET])
    score = model.predict_proba(test[list(columns)])[:, 1]
    secondary_model = make_model(winner["model"])
    secondary_model.fit(refit[list(columns)], refit[SECONDARY])
    secondary_score = secondary_model.predict_proba(test[list(columns)])[:, 1]
    holdout = evaluate(test, score, TARGET)
    holdout.update(block_bootstrap_top5(test, score, TARGET))
    # This fixed comparator measures whether added columns improve over the old design.
    base_model = make_model(winner["model"])
    base_model.fit(refit[list(BASE)], refit[TARGET])
    base_score = base_model.predict_proba(test[list(BASE)])[:, 1]
    base_metrics = evaluate(test, base_score, TARGET)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(candidates).to_csv(out / "validation_candidates.csv", index=False)
    predictions = test[[*KEY, TARGET, SECONDARY]].copy()
    predictions["score"] = score
    predictions["secondary_score"] = secondary_score
    predictions["base_score"] = base_score
    predictions.to_csv(out / "holdout_predictions.csv.gz", index=False, compression="gzip")
    joblib.dump(model, out / "selected_model.joblib")
    summary = {
        "selected_on_validation_ap": winner,
        "features": list(columns),
        "split": {"train": [train.asof_date.min(), train.asof_date.max(), len(train)],
                  "validation": [valid.asof_date.min(), valid.asof_date.max(), len(valid)],
                  "refit": [refit.asof_date.min(), refit.asof_date.max(), len(refit)],
                  "test": [test.asof_date.min(), test.asof_date.max(), len(test)],
                  "embargo": "2026-03-31 and 2026-06-30; cross-split events purged"},
        "holdout": holdout,
        "holdout_secondary": evaluate(test, secondary_score, SECONDARY),
        "holdout_fixed_base_comparator": base_metrics,
        "policies": policy_results(test, score, high_score_cut, TARGET),
        "same_day_random_test": same_day_random_test(test, score, TARGET),
        "holdout_by_month": {
            month: evaluate(group, score[test.asof_date.str[:7].eq(month).to_numpy()], TARGET)
            for month, group in test.groupby(test.asof_date.str[:7])},
        "holdout_by_cooling_day": {
            str(day): evaluate(group, score[test.cooling_day.eq(day).to_numpy()], TARGET)
            for day, group in test.groupby("cooling_day")},
        "source": {"features_sha256": hashlib.sha256((source / "features_industry.csv.gz").read_bytes()).hexdigest(),
                   "labels_sha256": hashlib.sha256((source / "labels.csv.gz").read_bytes()).hexdigest(),
                   "sklearn_version": sklearn.__version__,
                   "generated_at_utc": datetime.now(timezone.utc).isoformat()},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"selected": winner, "holdout": holdout,
                      "base_comparator": base_metrics, "policies": summary["policies"],
                      "random_test": summary["same_day_random_test"]},
                     ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    run()
