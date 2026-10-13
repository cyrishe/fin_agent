"""Exploratory, walk-forward stability comparison for the close-to-open strategy.

This does not alter the production seven-factor tree or its decision rule.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge

from scripts.automl_st_status import load_st_intervals
from scripts.audit_automl_four_class_decisions import class_priority_fallback
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.experiment_automl_mk_1m_st_policies import POLICIES, make_cohorts
from scripts.prepare_automl_mk_1m_nonst import annotate


ROOT = Path("docs/stock_automl_runs/20261013_temporal_stability")
SOURCES = {
    "2025": Path("outputs/stock_automl/mk_1m_2025_continuous/cohort/trainable_candidates.csv.gz"),
    "2026": Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz"),
    "2026s": Path("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz"),
}
BASELINES = {
    "2025a": Path("outputs/stock_automl/mk_1m_2025_mar_aug/backtest/top2_verified.csv"),
    "2025b": Path("outputs/stock_automl/mk_1m_2025_sep_oct/backtest_aligned/top2_verified.csv"),
    "2026": Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv"),
}
SPECS = (("ridge20", 20, "ridge"), ("ridge60", 60, "ridge"),
         ("logit60", 60, "logit"))


def transformed(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Fit caps and scaling on past observations only; preserve 0/1 factors."""
    a, b = train[list(FEATURES)].to_numpy(float).copy(), test[list(FEATURES)].to_numpy(float).copy()
    for j in (1, 2, 3):  # volume ratio, turnover, free-float market cap
        a[:, j] = np.log1p(np.maximum(a[:, j], 0))
        b[:, j] = np.log1p(np.maximum(b[:, j], 0))
    for j in range(4):
        low, high = np.quantile(a[:, j], [.01, .99])
        a[:, j], b[:, j] = np.clip(a[:, j], low, high), np.clip(b[:, j], low, high)
        mean, std = a[:, j].mean(), a[:, j].std()
        a[:, j], b[:, j] = (a[:, j]-mean)/max(std, 1e-10), (b[:, j]-mean)/max(std, 1e-10)
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Non-finite seven-factor input")
    return a, b


def score(train: pd.DataFrame, test: pd.DataFrame, kind: str) -> tuple[np.ndarray, np.ndarray]:
    x, z = transformed(train, test)
    actual = train.close_40.to_numpy(float) / train.signal_price.to_numpy(float) - 1
    if kind == "ridge":
        # Training winsorization limits the effect of overnight extremes.
        model = Ridge(alpha=100.0, solver="cholesky")
        model.fit(x, np.clip(actual, -.10, .10))
        return model.predict(z), model.coef_
    model = LogisticRegression(C=.02, max_iter=500, solver="lbfgs")
    model.fit(x, actual > 0)
    return model.predict_proba(z)[:, 1], model.coef_[0]


def load_cohorts() -> dict[str, pd.DataFrame]:
    data = {year: pd.read_csv(path, dtype={"symbol6": str}, low_memory=False)
            for year, path in SOURCES.items()}
    with db_connection() as db:  # historical ST state, read only
        intervals = load_st_intervals(db)
    filtered, _ = make_cohorts(data["2025"], intervals)
    data["2025"] = filtered[POLICIES[1]]
    later = annotate(data["2026s"], intervals)
    if not later.historical_status_known.all():
        raise ValueError("Unknown September historical ST state")
    data["2026s"] = later.loc[~later.historical_st].copy()
    if len(data["2026s"]) != 13992:
        raise ValueError("September cohort does not match the audited pool")
    return data


def benchmark() -> pd.DataFrame:
    parts = []
    for period, path in BASELINES.items():
        frame = pd.read_csv(path, dtype={"symbol6": str})
        frame = frame.loc[frame.selection_rank.eq(1),
                          ["signal_date", "symbol6", "actual_0940_return"]].copy()
        frame["model"] = "tree171"
        frame["year"] = period[:4]
        frame["return"] = frame.actual_0940_return
        parts.append(frame[["year", "signal_date", "symbol6", "model", "return"]])
    scored = pd.read_csv("docs/stock_automl_runs/20261010_second_high_four_class_historical_st/predictions.csv.gz",
                         dtype={"symbol6": str})
    tree = class_priority_fallback(scored.loc[scored.model.eq("small_boosted_tree")])
    tree = tree.loc[tree.selection_rank.eq(1), ["signal_date", "symbol6"]].copy()
    prices = pd.read_csv(SOURCES["2026s"], dtype={"symbol6": str},
                         usecols=["signal_date", "symbol6", "signal_price", "close_40"])
    tree = tree.merge(prices, on=["signal_date", "symbol6"], validate="one_to_one")
    tree["return"] = tree.close_40/tree.signal_price-1
    tree["year"], tree["model"] = "2026s", "tree171"
    parts.append(tree[["year", "signal_date", "symbol6", "model", "return"]])
    return pd.concat(parts, ignore_index=True)


def run() -> None:
    cohorts = load_cohorts()
    baseline = benchmark()
    picks, folds, coefficients = [], [], []
    for year, rows in cohorts.items():
        rows = rows.copy()
        rows["actual"] = rows.close_40 / rows.signal_price - 1
        rows["kept_for_perturbation"] = [
            int.from_bytes(hashlib.blake2b(f"{date}:{symbol}".encode(), digest_size=8).digest(), "big") / 2**64 >= .02
            for date, symbol in zip(rows.signal_date, rows.symbol6)]
        dates = sorted(rows.signal_date.unique())
        available = set(baseline.loc[baseline.year.eq(year), "signal_date"])
        for i, day in enumerate(dates):
            if day not in available or i < 20:
                continue
            test = rows.loc[rows.signal_date.eq(day)].copy()
            pool = test.actual.dropna()
            if pool.empty:
                continue
            for name, window, kind in SPECS:
                if i < window:
                    continue
                history = rows.loc[rows.signal_date.isin(dates[i-window:i]) & rows.actual.notna()].copy()
                if history.signal_date.nunique() != window or not history.next_date.le(day).all():
                    raise ValueError(f"Immature labels for {day}")
                if test[list(FEATURES)].isna().any().any():
                    raise ValueError(f"Incomplete features for {day}")
                normal, coef = score(history, test, kind)
                reduced, _ = score(history.loc[history.kept_for_perturbation], test, kind)
                if name == "ridge20":
                    coefficients.append({"year": year, "signal_date": day,
                                         **dict(zip(FEATURES, map(float, coef)))})
                # Stable identifier tie-break; no post-event information in selection.
                order = np.lexsort((test.symbol6.to_numpy(), -normal))
                alternate = np.lexsort((test.symbol6.to_numpy(), -reduced))
                choice = test.iloc[order[0]]
                picks.append({"year": year, "signal_date": day, "symbol6": choice.symbol6,
                              "model": name, "return": float(choice.actual),
                              "pool_mean": float(pool.mean()),
                              "top1_same_after_2pct_delete": bool(order[0] == alternate[0]),
                              "top5_overlap": len(set(order[:5]) & set(alternate[:5])) / 5,
                              "score": float(normal[order[0]])})
                folds.append({"year": year, "signal_date": day, "model": name,
                              "train_rows": len(history), "test_rows": len(test),
                              "perturbed_rows": int((~history.kept_for_perturbation).sum()),
                              "train_first": dates[i-window], "train_last": dates[i-1]})
    selected = pd.DataFrame(picks)
    trees = baseline.merge(selected.loc[selected.model.eq("ridge20"),
                                    ["year", "signal_date", "pool_mean"]],
                           on=["year", "signal_date"], how="left", validate="one_to_one")
    picks_all = pd.concat([selected, trees], ignore_index=True)
    picks_all["month"] = picks_all.signal_date.str[:7]
    all_dates = set(zip(selected.loc[selected.model.eq("ridge60"), "year"],
                        selected.loc[selected.model.eq("ridge60"), "signal_date"]))
    common = picks_all[[a in all_dates for a in zip(picks_all.year, picks_all.signal_date)]].copy()
    metrics = []
    for (year, model), group in common.groupby(["year", "model"]):
        valid = group.dropna(subset=["return"])
        monthly = valid.groupby("month")["return"].mean()
        metrics.append({"year": year, "model": model, "days": len(group),
                        "known": len(valid), "mean_pct": valid["return"].mean()*100,
                        "median_pct": valid["return"].median()*100,
                        "win_rate": float(valid["return"].gt(0).mean()),
                        "pool_lift_pct": (valid["return"]-valid.pool_mean).mean()*100,
                        "positive_months": int(monthly.gt(0).sum()),
                        "months": len(monthly), "worst_month_pct": monthly.min()*100,
                        "same_top1_2pct": float(valid.top1_same_after_2pct_delete.mean())
                        if model != "tree171" else None,
                        "top5_overlap_2pct": float(valid.top5_overlap.mean())
                        if model != "tree171" else None})
    ROOT.mkdir(parents=True, exist_ok=True)
    selected.to_csv(ROOT / "research_picks.csv", index=False)
    pd.DataFrame(folds).to_csv(ROOT / "folds.csv", index=False)
    pd.DataFrame(coefficients).to_csv(ROOT / "ridge20_coefficients.csv", index=False)
    common.to_csv(ROOT / "common_dates.csv", index=False)
    pd.DataFrame(metrics).to_csv(ROOT / "metrics.csv", index=False)
    monthly = common.groupby(["year", "model", "month"]).agg(
        days=("return", "count"), mean_return=("return", "mean"),
        pool_mean=("pool_mean", "mean")).reset_index()
    monthly.to_csv(ROOT / "monthly.csv", index=False)
    dates20 = set(zip(selected.loc[selected.model.eq("ridge20"), "year"],
                      selected.loc[selected.model.eq("ridge20"), "signal_date"]))
    full20 = picks_all[[a in dates20 for a in zip(picks_all.year, picks_all.signal_date)]].copy()
    full20 = full20[full20.model.isin(("ridge20", "tree171"))]
    full20.to_csv(ROOT / "all_20day_dates.csv", index=False)
    brief = []
    for (year, model), group in full20.groupby(["year", "model"]):
        valid = group.dropna(subset=["return"])
        r = valid["return"]
        month = valid.groupby("month")["return"].mean()
        brief.append({"period": year, "model": model, "days": len(group),
                      "known": len(valid), "mean_pct": r.mean()*100,
                      "median_pct": r.median()*100,
                      "cap5_mean_pct": r.clip(-.05, .05).mean()*100,
                      "exclude_top3_mean_pct": r.drop(r.nlargest(3).index).mean()*100,
                      "pool_lift_pct": (r-valid.pool_mean).mean()*100,
                      "win_rate": r.gt(0).mean(),
                      "positive_months": int(month.gt(0).sum()), "months": len(month),
                      "worst_month_pct": month.min()*100,
                      "top1_same_after_2pct_delete": valid.top1_same_after_2pct_delete.mean()
                      if model == "ridge20" else None})
    pd.DataFrame(brief).to_csv(ROOT / "metrics_20day.csv", index=False)
    full20.groupby(["year", "model", "month"])["return"].agg(
        ["count", "mean"]).reset_index().to_csv(ROOT / "monthly_20day.csv", index=False)
    raw_later = pd.read_csv(SOURCES["2026s"], dtype={"symbol6": str})
    sep_dates = sorted(raw_later.signal_date.unique())
    sensitivity = []
    for i in range(20, len(sep_dates)):
        day = sep_dates[i]
        train = raw_later[raw_later.signal_date.isin(sep_dates[i-20:i]) & raw_later.close_40.notna()]
        test = raw_later[raw_later.signal_date.eq(day)]
        scores, _ = score(train, test, "ridge")
        position = np.lexsort((test.symbol6.to_numpy(), -scores))[0]
        chosen = test.iloc[position]
        sensitivity.append({"signal_date": day, "symbol6": chosen.symbol6,
                            "return": chosen.close_40/chosen.signal_price-1})
    pd.DataFrame(sensitivity).to_csv(ROOT / "september_including_nonlimit_st.csv", index=False)
    manifest = {"sources": {year: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                            for year, path in SOURCES.items()},
                "baselines": {key: str(path) for key, path in BASELINES.items()},
                "tree_note": "Saved historical daily Top1; no tree retraining in this study",
                "perturbation": "outcome-independent fixed 2% hash deletion from each training window",
                "target": "T+1 09:40 minute close / T 14:40 signal price - 1",
                "model_specs": [list(spec) for spec in SPECS]}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(pd.DataFrame(metrics).to_string(index=False))
    print(pd.DataFrame(brief).to_string(index=False))


if __name__ == "__main__":
    run()
