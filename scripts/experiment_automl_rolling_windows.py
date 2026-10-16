"""Walk forward seven-factor models on exact 1m prices, without refitting on test days."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import BINARY, DATA
from scripts.experiment_automl_matrix_10bar import PRICE_COLUMNS, target_class


SOURCE = Path("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz")
OUT = Path("docs/stock_automl_runs/20261009_rolling_windows")
WINDOWS = (10, 15, 20)


def fit_model(train: pd.DataFrame, model_name: str):
    if model_name == "逻辑回归":
        numeric = [c for c in FEATURES if c not in BINARY]
        binary = [c for c in FEATURES if c in BINARY]
        model = make_pipeline(ColumnTransformer([
            ("numeric", StandardScaler(), numeric),
            ("binary", "passthrough", binary),
        ]), LogisticRegression(C=0.2, max_iter=2000))
        model.fit(train[list(FEATURES)], train.label.eq(1).astype(int))
    elif model_name == "回归树":
        model = DecisionTreeRegressor(max_depth=5, min_samples_leaf=80, random_state=42)
        model.fit(train[list(FEATURES)], train.target_return.clip(-0.1, 0.1))
    else:
        raise ValueError(model_name)
    return model


def score(model, model_name: str, test: pd.DataFrame):
    if model_name == "逻辑回归":
        return model.predict_proba(test[list(FEATURES)])[:, 1]
    return model.predict(test[list(FEATURES)])


def summarize(detail: pd.DataFrame, scope: str):
    summary = []
    for keys, group in detail.groupby(["window", "model", "price"], sort=False):
        for top_n in (2, 5):
            picked = group[group["rank"].le(top_n)]
            base = group.label.eq(1).mean()
            summary.append({"训练窗口": keys[0], "模型": keys[1], "训练及验证价格": keys[2],
                            "评价范围": scope, "测试日数": group.signal_date.nunique(),
                            "候选总数": len(group), "每日取前": top_n,
                            "选出总数": len(picked), "达标": int(picked.label.eq(1).sum()),
                            "中性": int(picked.label.eq(0).sum()),
                            "严重错误": int(picked.label.eq(-1).sum()),
                            "候选池达标率": base,
                            "Lift": picked.label.eq(1).mean()/base,
                            "入选门槛及以上最多数": max(
                                day.score.ge(day.score.iloc[top_n-1]).sum()
                                for _, day in group.groupby("signal_date"))})
    return summary


def main():
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SOURCE, dtype={"symbol6": str})
    if prices.duplicated(["next_date", "symbol6"]).any():
        raise ValueError("Duplicate ten-bar prices")
    data = base.merge(prices, on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(data.signal_date.unique())
    if len(data) != 14292 or len(dates) != 40 or len(prices) != len(data):
        raise ValueError("Expected exact 40-day candidate pool")
    for name, col in PRICE_COLUMNS.items():
        data[f"return_{col}"] = data[col]/data.entry_1440-1
        data[f"label_{col}"] = target_class(data[f"return_{col}"])
    scored = []
    for window in WINDOWS:
        for ix in range(window, len(dates)):
            training_dates = dates[ix-window:ix]
            train = data[data.signal_date.isin(training_dates)]
            test = data[data.signal_date.eq(dates[ix])]
            if train.signal_date.nunique() != window or test.empty:
                raise ValueError("Incomplete rolling fold")
            for price, col in PRICE_COLUMNS.items():
                labeled_train = train.assign(target_return=train[f"return_{col}"],
                                             label=train[f"label_{col}"])
                for model_name in ("逻辑回归", "回归树"):
                    model = fit_model(labeled_train, model_name)
                    frame = test[["signal_date", "next_date", "symbol6", "name",
                                  "entry_1440", *FEATURES]].copy()
                    frame["window"] = window
                    frame["train_start"] = training_dates[0]
                    frame["train_end"] = training_dates[-1]
                    frame["model"] = model_name
                    frame["price"] = price
                    frame["target_return"] = test[f"return_{col}"].to_numpy()
                    frame["label"] = test[f"label_{col}"].to_numpy()
                    frame["score"] = score(model, model_name, test)
                    frame = frame.sort_values(["score", "symbol6"], ascending=[False, True])
                    frame["rank"] = np.arange(1, len(frame)+1)
                    scored.append(frame)
            print(f"window {window}: {dates[ix]}", flush=True)
    full = pd.concat(scored, ignore_index=True)
    common_dates = dates[max(WINDOWS):]
    common = full[full.signal_date.isin(common_dates)]
    summary = summarize(full, "各窗口全部折外日期") + summarize(common, "共同20个折外日期")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary).to_csv(OUT/"summary.csv", index=False)
    full[full["rank"].le(5)].to_csv(OUT/"daily_top5.csv", index=False)
    daily = full.groupby(["window", "model", "price", "signal_date", "next_date",
                          "train_start", "train_end"], as_index=False).agg(
        candidates=("label", "size"), pool_hits=("label", lambda x: int(x.eq(1).sum())))
    for n in (2, 5):
        t = full[full["rank"].le(n)].groupby(
            ["window", "model", "price", "signal_date"], as_index=False).agg(
            **{f"top{n}_hits": ("label", lambda x: int(x.eq(1).sum())),
               f"top{n}_neutral": ("label", lambda x: int(x.eq(0).sum())),
               f"top{n}_critical": ("label", lambda x: int(x.eq(-1).sum()))})
        daily = daily.merge(t, on=["window", "model", "price", "signal_date"],
                            validate="one_to_one")
    daily.to_csv(OUT/"daily_results.csv", index=False)
    manifest = {"source_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (DATA, SOURCE)},
                "dates": dates, "common_test_dates": common_dates,
                "method": "Each day uses exactly the previous 10/15/20 available signal dates. "
                          "Target is max of next-day 09:31-09:40 exact 1m open/close/high "
                          "relative to 14:40 entry. Matched train and verification price. "
                          "Logistic targets >1%; regression tree targets continuous return "
                          "clipped to [-10%,10%]. No model selection or simulated trades.",
                "models": {"logistic": "C=.2; four continuous factors standardized; three binary passthrough",
                           "regression_tree": "max_depth=5, min_samples_leaf=80, random_state=42"}}
    (OUT/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    print(pd.DataFrame(summary).query("`每日取前` == 2").to_string(index=False))


if __name__ == "__main__":
    main()
