from __future__ import annotations

from decimal import Decimal
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, mean_squared_error, roc_auc_score
from src.backtest import (BacktestConfig, BacktestEngine, Bar, BpsExecutionModel,
                          InMemoryMarketData, Instrument, ScheduledTargetStrategy)


def selected(frame, task, spec, *, policy=None):
    """Apply a development-frozen decision rule without consulting outcomes."""
    threshold = (policy["threshold"] if policy is not None else
                 spec.probability_threshold if task == "classification" else spec.regression_threshold)
    picks = frame[frame.prediction >= threshold]
    # Legacy callers keep their uncapped signal metrics. A saved policy has one
    # daily selection rule shared by development, holdout evaluation and inference.
    if policy is not None and policy.get("top_k") is not None:
        picks = picks.sort_values(["date", "prediction", "symbol"], ascending=[True, False, True])
        picks = picks.groupby("date", sort=False).head(policy["top_k"])
    return picks


def selection_metrics(frame, task, spec, *, policy=None):
    """Describe target-event precision; correlated rows are not independent trials."""
    picks = selected(frame, task, spec, policy=policy)
    n, total = len(picks), len(frame)
    positive = int((frame.forward_return > spec.target_return).sum())
    tp = int((picks.forward_return > spec.target_return).sum())
    fp, fn = n - tp, positive - tp
    precision = tp / n if n else None
    base_rate = positive / total if total else None
    cost = 2 * (spec.commission_rate + spec.slippage_rate) + spec.sell_tax_rate
    date_precision = picks.assign(target_event=picks.forward_return > spec.target_return).groupby("date").target_event.mean()
    by_month = []
    months = frame.date.dt.to_period("M")
    pick_months = picks.date.dt.to_period("M")
    for month, group in frame.groupby(months):
        chosen = picks[pick_months == month]
        count = len(chosen)
        by_month.append({
            "month": str(month), "rows": len(group), "n": count,
            "signal_dates": int(chosen.date.nunique()),
            "target_precision": float((chosen.forward_return > spec.target_return).mean()) if count else None,
            "unconditional_up_rate": float((group.forward_return > spec.target_return).mean()),
            "actual_down_count": int((chosen.forward_return < 0).sum()),
            "mean_return": float(chosen.forward_return.mean()) if count else None,
            "win_rate_after_cost": float((chosen.forward_return > cost).mean()) if count else None,
        })
    active_month_precision = [m["target_precision"] for m in by_month if m["n"]]
    return {
        "signal_count": n, "signal_dates": int(picks.date.nunique()),
        "coverage": n / total if total else 0.0,
        "positive_rows": positive, "negative_rows": total - positive,
        "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "true_negative": total - positive - fp,
        "target_precision": precision, "target_recall": tp / positive if positive else None,
        "actual_down_count": int((picks.forward_return < 0).sum()),
        "actual_down_rate": float((picks.forward_return < 0).mean()) if n else None,
        "precision_lift": precision / base_rate if precision is not None and base_rate else None,
        "unconditional_up_rate": base_rate,
        "date_macro_precision": float(date_precision.mean()) if n else None,
        "date_precision_lower_quartile": float(date_precision.quantile(.25)) if n else None,
        "worst_active_month_precision": min(active_month_precision) if active_month_precision else None,
        "by_month": by_month,
        "precision_evidence_note": (
            "Date and month summaries are descriptive robustness checks, not independent-trial "
            "confidence bounds. Stocks on a date and overlapping holding periods can be correlated."
        ),
    }


def prediction_metrics(frame, task, spec, baseline, *, policy=None):
    if frame.empty:
        raise ValueError("empty evaluation sample")
    y = (frame.forward_return > spec.target_return).astype(int)
    picks = selected(frame, task, spec, policy=policy)
    cost = 2 * (spec.commission_rate + spec.slippage_rate) + spec.sell_tax_rate
    wins = int((picks.forward_return > cost).sum())
    n = len(picks)
    p = wins / n if n else None
    interval = None
    if n:
        z = 1.96
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        spread = z * np.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
        interval = [float(center - spread), float(center + spread)]
    metrics = {"rows": len(frame), "companies": frame.symbol.nunique(), "signal_count": n,
               "signal_win_rate_after_cost": p, "descriptive_wilson_interval": interval,
               "mean_signal_net_return": float(picks.forward_return.mean() - cost) if n else None,
               "unconditional_up_rate": float(y.mean()),
               "prediction_range": {"min": float(frame.prediction.min()), "max": float(frame.prediction.max()),
                                    "mean": float(frame.prediction.mean())}, "calibration": []}
    metrics.update(selection_metrics(frame, task, spec, policy=policy))
    metrics["descriptive_wilson_interval_note"] = (
        "Legacy row-level after-cost win-rate summary; correlated signals violate the independent "
        "Bernoulli assumption, so this is not a valid confidence bound for strategy selection."
    )
    if task == "classification":
        loss = brier_score_loss(y, frame.prediction)
        base_loss = brier_score_loss(y, np.full(len(y), baseline))
        metrics.update(brier=float(loss), baseline_brier=float(base_loss),
                       roc_auc=float(roc_auc_score(y, frame.prediction)) if y.nunique() > 1 else None)
        for lo in np.arange(0, 1, .1):
            b = frame[(frame.prediction >= lo) & (frame.prediction < lo + .1)]
            if len(b):
                metrics["calibration"].append({"lower": round(float(lo), 1), "n": len(b),
                    "predicted": float(b.prediction.mean()), "observed": float((b.forward_return > spec.target_return).mean())})
    else:
        loss = mean_squared_error(frame.forward_return, frame.prediction)
        base_loss = mean_squared_error(frame.forward_return, np.full(len(frame), baseline))
        correlation = frame.prediction.corr(frame.forward_return, method="spearman")
        metrics.update(rmse=float(np.sqrt(loss)), baseline_rmse=float(np.sqrt(base_loss)),
                       rank_ic=float(correlation) if np.isfinite(correlation) else None)
    metrics["skill_vs_constant"] = float(1 - loss / base_loss) if base_loss > 0 else 0.0
    metrics["by_industry"] = [{"industry": str(name), "n": len(g),
                               "mean_return": float(g.forward_return.mean())}
                              for name, g in picks.groupby("industry")] if "industry" in picks else []
    return metrics


def backtest_predictions(predictions, panel, candidate, spec, *, benchmark=False, policy=None):
    """Non-overlapping cohorts, next-open fills, existing cash ledger and daily marking."""
    if predictions.empty:
        raise ValueError("no predictions for backtest")
    start, end = predictions.date.min(), predictions.label_end.max()
    symbols = sorted(predictions.symbol.unique())
    frame = panel[panel.symbol.isin(symbols) & panel.date.between(start, end)].copy()
    calendar = sorted(frame.date.unique())
    schedule = {}
    # Signals rebalance once per holding horizon. Liquidate after the final scored cohort.
    for day in calendar[::candidate["horizon"]]:
        if day > predictions.date.max():
            schedule[pd.Timestamp(day).date()] = {}
            break
        current = predictions[predictions.date == day]
        picks = current if benchmark else selected(current, candidate["task"], spec, policy=policy)
        top_k = policy.get("top_k", spec.top_k) if policy is not None else spec.top_k
        picks = picks.sort_values(["prediction", "symbol"], ascending=[False, True]).head(top_k) if not benchmark else picks
        weight = (Decimal("0.98") / len(picks)) if len(picks) else Decimal(0)
        schedule[pd.Timestamp(day).date()] = {s: weight for s in picks.symbol}
    valid = frame.dropna(subset=["adjopen", "adjhigh", "adjlow", "adjclose"])
    bars = []
    for _, group in valid.groupby("symbol"):
        # Constant rebasing keeps adjusted returns while avoiding arbitrary historical adjustment scales.
        scale = float(group.iloc[0].close / group.iloc[0].adjclose)
        for row in group.itertuples():
            # OHLC only cannot establish opening queue availability. No invented limit fills.
            flat = row.high == row.low
            change = row.close / row.preclose - 1 if hasattr(row, "preclose") and row.preclose > 0 else 0
            bars.append(Bar(date=row.date.date(), symbol=row.symbol,
                            open=row.adjopen * scale, high=row.adjhigh * scale,
                            low=row.adjlow * scale, close=row.adjclose * scale,
                            tradable=bool(row.volume > 0), can_buy=not(flat and change > .045),
                            can_sell=not(flat and change < -.045)))
    data = InMemoryMarketData(bars, instruments=[Instrument(s, lot_size=1) for s in symbols],
                              calendar=[pd.Timestamp(d).date() for d in calendar], source_name="automl_adjusted_research")
    result = BacktestEngine().run(data=data, strategy=ScheduledTargetStrategy(schedule),
        config=BacktestConfig(universe=symbols, initial_cash=spec.initial_cash),
        execution_model=BpsExecutionModel(commission_rate=spec.commission_rate,
            sell_tax_rate=spec.sell_tax_rate, slippage_rate=spec.slippage_rate))
    return result.to_dict()
