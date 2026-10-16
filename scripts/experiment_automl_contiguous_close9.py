"""Refit the frozen contiguous 20-day experiment on the exact close-nine target."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.benchmark_automl_1440_inference import FEATURES, frozen_score, verify_model
from scripts.experiment_automl_close9_models import (
    BINARY, COMPACT, DATA, MARKET, OUT as DIVERSE, day_weights, models, split_dates,
)


OUT = Path("docs/stock_automl_runs/20261009_contiguous_close9")
TRAIN_START, TRAIN_END = "2026-08-25", "2026-09-21"


def fit(rows: pd.DataFrame, columns, weights=None):
    numeric = [column for column in columns if column not in BINARY]
    binary = [column for column in columns if column in BINARY]
    model = make_pipeline(ColumnTransformer([
        ("numeric", StandardScaler(), numeric), ("binary", "passthrough", binary)]),
        LogisticRegression(C=.2, max_iter=2000))
    extra = {} if weights is None else {"logisticregression__sample_weight": weights}
    model.fit(rows[list(columns)], rows.target_class.eq(1).astype(int), **extra)
    return model


def ranked(model, columns, rows, frozen=False):
    result = rows.copy()
    result["score"] = (frozen_score(model, result[list(columns)]) if frozen else
                       model.predict_proba(result[list(columns)])[:, 1])
    result = result.sort_values(["signal_date", "score", "symbol6"],
                                ascending=[True, False, True])
    result["rank"] = result.groupby("signal_date").cumcount() + 1
    return result


def metrics(rows, n):
    chosen = rows[rows["rank"].le(n)]
    pool_rate = rows.target_class.eq(1).mean()
    cutoff_ties = [int(day.score.ge(day.score.iloc[n-1]).sum())
                   for _, day in rows.groupby("signal_date")]
    return {"signal_days": int(rows.signal_date.nunique()),
            "candidate_stocks": len(rows), "top_n": n, "selected": len(chosen),
            "strong": int(chosen.target_class.eq(1).sum()),
            "neutral": int(chosen.target_class.eq(0).sum()),
            "critical": int(chosen.target_class.eq(-1).sum()),
            "strong_rate": float(chosen.target_class.eq(1).mean()),
            "pool_strong_rate": float(pool_rate),
            "lift": float(chosen.target_class.eq(1).mean()/pool_rate),
            "max_at_or_above_cutoff": max(cutoff_ties)}


def main():
    rows = pd.read_csv(DATA, dtype={"symbol6": str})
    in_train = rows.signal_date.between(TRAIN_START, TRAIN_END)
    train, outside = rows[in_train], rows[~in_train]
    if (train.signal_date.nunique(), len(train), outside.signal_date.nunique(),
            len(outside)) != (20, 7488, 20, 6804):
        raise ValueError("Frozen contiguous split or exact candidate pool changed")
    old, old_check = verify_model()
    OUT.mkdir(parents=True, exist_ok=True)
    archived_path = OUT / "original_contiguous_seven_factor.joblib"
    shutil.copyfile(Path("docs/stock_automl_runs/20261008_sina_15m_july/"
                         "exact_trained_seven_factor.joblib"), archived_path)
    old_hash = hashlib.sha256(archived_path.read_bytes()).hexdigest()
    if old_hash != "de04586fe320ae4d6f18670578ebf1a195362f0f378c15b97cb0ea182f782539":
        raise ValueError("Original frozen model changed")
    fitted = {"原连续七因子_旧目标": (FEATURES, old, True),
              "新连续七因子_新目标": (FEATURES, fit(train, FEATURES), False),
              "新连续五因子_新目标": (COMPACT, fit(train, COMPACT), False)}
    for key in ("新连续七因子_新目标", "新连续五因子_新目标"):
        joblib.dump(fitted[key][1], OUT / f"{key}.joblib")

    states = pd.read_csv(MARKET)
    assignments = split_dates(states)
    market_train = rows[rows.signal_date.map(assignments).eq("train")]
    common_dates = sorted(set(outside.signal_date.unique()) -
                          set(market_train.signal_date.unique()))
    if len(common_dates) != 8:
        raise ValueError("Expected eight dates outside both training sets")
    diverse_five = joblib.load(DIVERSE / "selected_close9_model.joblib")
    seven_columns, diverse_seven = models()["seven_logistic"]
    diverse_seven.fit(market_train[list(seven_columns)],
                      market_train.target_class.eq(1).astype(int),
                      logisticregression__sample_weight=day_weights(market_train))
    fitted.update({"大盘分组七因子_新目标": (seven_columns, diverse_seven, False),
                   "大盘分组五因子_新目标": (COMPACT, diverse_five, False)})

    summary, detail = [], []
    for name, (columns, model, frozen) in fitted.items():
        partitions = {"训练20天": train, "非训练20天": outside,
                      "训练前14天": outside[outside.signal_date.lt(TRAIN_START)],
                      "训练后6天": outside[outside.signal_date.gt(TRAIN_END)],
                      "双方均未训练的8天": outside[outside.signal_date.isin(common_dates)]}
        if name.startswith("大盘分组"):
            partitions = {"双方均未训练的8天": partitions["双方均未训练的8天"]}
        for part, population in partitions.items():
            scored = ranked(model, columns, population, frozen)
            for n in (2, 5):
                summary.append({"model": name, "partition": part, **metrics(scored, n)})
            top5 = scored[scored["rank"].le(5)].copy()
            top5["model"] = name
            top5["partition"] = part
            top5["top2"] = top5["rank"].le(2).astype(int)
            top5["top3"] = top5["rank"].le(3).astype(int)
            top5["top5"] = 1
            top5["critical_error"] = top5.target_class.eq(-1).astype(int)
            detail.append(top5[["model", "partition", "signal_date", "next_date", "rank",
                                "top2", "top3", "top5", "symbol6", "name", "score",
                                "entry_1440", "max_close9_return", "close_0940_return",
                                "target_class", "critical_error", *FEATURES]])
    summary_frame = pd.DataFrame(summary)
    detail_frame = pd.concat(detail, ignore_index=True)
    summary_frame.to_csv(OUT / "ranking_summary.csv", index=False)
    detail_frame.to_csv(OUT / "daily_top5.csv", index=False)
    report = {"original_frozen_model_sha256": old_hash,
              "original_model_verification": old_check,
              "training_period": [TRAIN_START, TRAIN_END],
              "training_dates": sorted(train.signal_date.unique().tolist()),
              "out_of_training_dates": sorted(outside.signal_date.unique().tolist()),
              "common_evaluation_dates": common_dates,
              "new_target": "max(T+1 09:32..09:40 minute closes)/T 14:40 price - 1; >1%=1, <0.5%=-1",
              "new_fit": "Same LogisticRegression(C=0.2) and scaling as archived contiguous seven-factor model; new target is class 1 versus 0/-1; no day weights",
              "comparison_limit": "Common eight dates were previously explored; descriptive comparison only, not blind prospective test",
              "input_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
              "new_model_sha256": {
                  key: hashlib.sha256((OUT / f"{key}.joblib").read_bytes()).hexdigest()
                  for key in ("新连续七因子_新目标", "新连续五因子_新目标")}}
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(summary_frame.to_string(index=False))


if __name__ == "__main__":
    main()
