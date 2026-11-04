"""Score all available held-out 1m dates and July 15m proxy dates with fixed models."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.experiment_automl_tail_three_class import BINARY, COMPACT_FEATURES, read_samples


def fitted(training: pd.DataFrame, features: tuple[str, ...]):
    numeric = [f for f in features if f not in BINARY]
    binary = [f for f in features if f in BINARY]
    model = make_pipeline(ColumnTransformer([
        ("numeric", StandardScaler(), numeric), ("binary", "passthrough", binary)]),
        LogisticRegression(C=.2, max_iter=2000))
    model.fit(training[list(features)], training["class"].ne(-1).astype(int))
    return model


def outcome(rows: pd.DataFrame) -> dict:
    n = len(rows)
    strong = int(rows["class"].eq(1).sum())
    neutral = int(rows["class"].eq(0).sum())
    critical = int(rows["class"].eq(-1).sum())
    return {"days": int(rows.signal_date.nunique()), "stocks": n,
            "strong": strong, "neutral": neutral, "critical": critical,
            "strong_rate": strong/n if n else None,
            "pass_rate": (strong+neutral)/n if n else None,
            "critical_rate": critical/n if n else None}


def evaluate(expanded: Path, sep30: Path, aug05: Path, aug24: Path, july: Path,
             frozen_model: Path, seven_model: Path, output: Path) -> dict:
    training_all = pd.concat([read_samples(expanded), read_samples(sep30)], ignore_index=True)
    training = training_all[training_all.signal_date.between("2026-08-25", "2026-09-21")].copy()
    if len(training) != 7488 or training.signal_date.nunique() != 20:
        raise ValueError("Fixed exact 20-day training sample changed")
    exact = pd.concat([training_all, read_samples(aug05), read_samples(aug24)],
                      ignore_index=True)
    if exact.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate exact stock-day across data files")
    exact = exact[~exact.signal_date.isin(training.signal_date.unique())].copy()
    proxy = pd.read_csv(july, dtype={"symbol6": str})
    proxy["signal_date"] = pd.to_datetime(proxy.signal_date)
    proxy["next_date"] = pd.to_datetime(proxy.next_date)
    if proxy.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate July stock-day")
    if proxy.signal_date.nunique() != 23 or proxy.groupby("signal_date").size().min() < 5:
        raise ValueError("Expected all 23 July trading days with at least five candidates")
    if not proxy.signal_date.between("2026-07-01", "2026-07-31").all():
        raise ValueError("July proxy file has out-of-range signal dates")
    if set(training.signal_date.unique()) & set(proxy.signal_date.unique()):
        raise ValueError("Training and proxy test dates overlap")
    if set(training.signal_date.unique()) & set(exact.signal_date.unique()):
        raise ValueError("Training and exact test dates overlap")
    if set(exact.signal_date.unique()) & set(proxy.signal_date.unique()):
        raise ValueError("Exact and proxy test dates overlap")
    if exact.groupby("signal_date").size().min() < 5:
        raise ValueError("Incomplete exact test day")
    for f in COMPACT_FEATURES:
        proxy[f] = pd.to_numeric(proxy[f], errors="raise")
        if proxy[f].isna().any():
            raise ValueError(f"Missing proxy feature {f}")
    if not proxy["class"].isin([-1, 0, 1]).all():
        raise ValueError("Invalid July label")
    plans = {"固定八因子": tuple(COMPACT_FEATURES),
             "精确训练七因子_去当前价高于均线": tuple(f for f in COMPACT_FEATURES
                                                 if f != "price_above_all_ma")}
    if not frozen_model.exists() or not seven_model.exists():
        raise FileNotFoundError("Exact-trained frozen model artifact missing")
    old_model = joblib.load(frozen_model)
    new_model = joblib.load(seven_model)
    reports = {}
    output.mkdir(parents=True, exist_ok=True)
    output_columns = ["model", "signal_date", "next_date", "symbol6", "name", "rank",
                      "score", "class", "target_return", "signal_return",
                      "volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny",
                      "volume_4of5_increasing", "ma_bull_5_10_20",
                      "price_above_all_ma", "all_intraday_lows_above_ma"]
    for source, rows, target, filename in (
        ("exact_1m", exact, "target_next_high10_return", "exact_top5_two_models.csv"),
        ("july_proxy_15m", proxy, "target_next_high15_return", "july_top5_two_models.csv"),
    ):
        details = []
        source_reports = []
        for name, features in plans.items():
            selected = rows.copy()
            model = old_model if name == "固定八因子" else new_model
            selected["score"] = model.predict_proba(selected[list(features)])[:, 1]
            selected = selected.sort_values(["signal_date", "score", "symbol6"],
                                            ascending=[True, False, True])
            selected["rank"] = selected.groupby("signal_date").cumcount() + 1
            selected["model"] = name
            detail = selected[selected["rank"].le(5)].copy()
            detail["target_return"] = detail[target]
            details.append(detail)
            daily = {}
            for date, group in selected.groupby("signal_date"):
                daily[date.date().isoformat()] = {"candidate_pool": outcome(group),
                                                 "top2": outcome(group[group["rank"].le(2)]),
                                                 "top5": outcome(group[group["rank"].le(5)])}
            source_reports.append({"model": name, "features": list(features),
                                   "training_days": 20, "training_rows": len(training),
                                   "candidate_pool": outcome(selected),
                                   "top1": outcome(selected[selected["rank"].eq(1)]),
                                   "top2": outcome(selected[selected["rank"].le(2)]),
                                   "top5": outcome(selected[selected["rank"].le(5)]),
                                   "by_rank": {str(rank): outcome(selected[selected["rank"].eq(rank)])
                                               for rank in range(1, 6)},
                                   "daily": daily})
        pd.concat(details, ignore_index=True)[output_columns].to_csv(
            output / filename, index=False)
        reports[source] = {"signal_dates": [d.date().isoformat() for d in sorted(rows.signal_date.unique())],
                           "models": source_reports}
    report = {"training_signal_dates": [d.date().isoformat() for d in sorted(training.signal_date.unique())],
              "train_label": "T+1 first 10m high / T 14:50 proxy - 1",
              "exact_test_label": "T+1 first 10m high / T 14:50 proxy - 1",
              "july_proxy_label": "T+1 first 15m high / T 14:45 proxy - 1",
              "score_calibrated": False, "sources": reports,
              "frozen_model3_sha256": hashlib.sha256(frozen_model.read_bytes()).hexdigest(),
              "seven_factor_model_sha256": hashlib.sha256(seven_model.read_bytes()).hexdigest(),
              "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (expanded, sep30, aug05, aug24, july)}}
    (output / "evaluation_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({source: {"days": len(value["signal_dates"]), "models": [
        {k: x[k] for k in ("model", "candidate_pool", "top2", "top5")}
        for x in value["models"]]} for source, value in reports.items()},
        ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expanded", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/trainable_1440.csv"))
    p.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    p.add_argument("--aug05", type=Path, default=Path(
        "outputs/stock_automl/tail_aug05_aug06_exact/trainable_1440.csv"))
    p.add_argument("--aug24", type=Path, default=Path(
        "outputs/stock_automl/tail_aug24_exact/trainable_1440.csv"))
    p.add_argument("--july", type=Path, default=Path(
        "outputs/stock_automl/sina_15m_july/trainable_proxy_candidates.csv"))
    p.add_argument("--frozen-model", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_sina_15m_july/frozen_model3_binary_pass.joblib"))
    p.add_argument("--seven-model", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_sina_15m_july/exact_trained_seven_factor.joblib"))
    p.add_argument("--output", type=Path, default=Path("outputs/stock_automl/sina_15m_july"))
    a = p.parse_args()
    evaluate(a.expanded, a.sep30, a.aug05, a.aug24, a.july,
             a.frozen_model, a.seven_model, a.output)
