"""Select an executable decision threshold using development OOF predictions only."""
from __future__ import annotations

import numpy as np

from .evaluation import selection_metrics


def decision_policy_rank(policy):
    """Conservative, descriptive ordering shared by threshold and model selection."""
    metrics, checks = policy["metrics"], policy["checks"]
    value = lambda key: metrics[key] if metrics[key] is not None else -1.0
    return (policy["eligible"], checks["enough_signals"] and checks["enough_signal_dates"],
            value("date_precision_lower_quartile"), value("worst_active_month_precision"),
            value("date_macro_precision"), value("target_precision"),
            -(metrics["actual_down_rate"] if metrics["actual_down_rate"] is not None else 1.0),
            metrics["signal_dates"], metrics["signal_count"], -policy["threshold"])


def choose_decision_policy(oof_predictions, task, spec):
    """Freeze a threshold before holdout scoring; callers own chronological OOF construction.

    This helper deliberately accepts no test set. Its reported precision has been
    used to choose the threshold and is therefore development evidence, not an
    unbiased estimate of future precision. Empty-signal folds need no special
    treatment: minimum support is measured across all OOF rows and signal dates.
    """
    if oof_predictions.empty:
        raise ValueError("threshold selection requires development OOF predictions")
    if not np.isfinite(oof_predictions[["prediction", "forward_return"]].to_numpy()).all():
        raise ValueError("development predictions and outcomes must be finite")
    scores = oof_predictions.prediction.to_numpy()
    base = spec.probability_threshold if task == "classification" else spec.regression_threshold
    thresholds = [float(base)]
    if getattr(spec, "optimize_threshold", False):
        unique = np.unique(scores)
        if len(unique) <= 40:
            thresholds.extend(unique.tolist())
        else:
            quantiles = np.unique(np.r_[np.linspace(0, 1, 21), [.9, .95, .97, .98, .99, .995, .999]])
            thresholds.extend(np.quantile(scores, quantiles).tolist())
        # An explicit empty-selection candidate makes abstention visible in the
        # research evidence; it can never satisfy minimum support.
        thresholds.append(float(np.nextafter(scores.max(), np.inf)))
    minimum_precision = getattr(spec, "min_precision", None)
    if minimum_precision is None:
        minimum_precision = spec.min_win_rate
    minimum_dates = getattr(spec, "min_signal_dates", 5)
    trials = []
    for threshold in sorted(set(thresholds)):
        policy = {"threshold": threshold, "top_k": spec.top_k}
        metrics = selection_metrics(oof_predictions, task, spec, policy=policy)
        checks = {
            "enough_signals": metrics["signal_count"] >= spec.min_signals,
            "enough_signal_dates": metrics["signal_dates"] >= minimum_dates,
            "target_precision_at_least_minimum": metrics["target_precision"] is not None
                and metrics["target_precision"] >= minimum_precision,
            "date_macro_precision_at_least_minimum": metrics["date_macro_precision"] is not None
                and metrics["date_macro_precision"] >= minimum_precision,
        }
        trials.append({**policy, "eligible": all(checks.values()), "checks": checks, "metrics": metrics})

    chosen = max(trials, key=decision_policy_rank)
    return {**chosen, "task": task, "target_return": spec.target_return,
            "selection_source": "development_oof", "search_trials": len(trials),
            "selection_note": (
                "Threshold and daily top_k were selected using development OOF predictions only. "
                "Date-block summaries are descriptive, not confidence bounds; final untouched "
                "time holdout evidence is required. No-signal periods are allowed."
            ),
            "threshold_trials": [{"threshold": t["threshold"], "eligible": t["eligible"],
                "signal_count": t["metrics"]["signal_count"], "signal_dates": t["metrics"]["signal_dates"],
                "target_precision": t["metrics"]["target_precision"],
                "date_macro_precision": t["metrics"]["date_macro_precision"]} for t in trials]}
