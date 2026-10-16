"""Walk-forward three-class logistic regression with crossed 1m price validation."""
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

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import BINARY, DATA
from scripts.experiment_automl_matrix_10bar import PRICE_COLUMNS


PRICES = Path("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz")
OUT = Path("docs/stock_automl_runs/20261009_rolling_three_class_cross")
WINDOWS = (10, 15, 20)


def label(ret):
    return np.select([ret > .01, ret < 0], [1, -1], default=0).astype(int)


def fit(train, target):
    numeric = [col for col in FEATURES if col not in BINARY]
    binary = [col for col in FEATURES if col in BINARY]
    model = make_pipeline(ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("binary", "passthrough", binary),
    ]), LogisticRegression(C=.2, max_iter=2000, solver="lbfgs"))
    model.fit(train[list(FEATURES)], train[target])
    if not np.array_equal(model[-1].classes_, [-1, 0, 1]):
        raise ValueError(f"Expected all three training classes: {model[-1].classes_}")
    return model


def summarize(scored, scope):
    rows = []
    for (window, train_price, val_price), group in scored.groupby(
            ["window", "train_price", "val_price"], sort=False):
        for top_n in (2, 5):
            chosen = group[group["rank"].le(top_n)]
            candidate_hit = group.label.eq(1).mean()
            candidate_pass = group.label.ne(-1).mean()
            hit = int(chosen.label.eq(1).sum())
            neutral = int(chosen.label.eq(0).sum())
            critical = int(chosen.label.eq(-1).sum())
            rows.append({"训练窗口": window, "模型": "三分类逻辑回归",
                         "训练价格": train_price, "验证价格": val_price,
                         "评价范围": scope, "测试日数": group.signal_date.nunique(),
                         "候选总数": len(group), "每日取前": top_n,
                         "入选总数": len(chosen), "达标_大于1%": hit,
                         "中性_0%至1%": neutral, "严重错误_低于0%": critical,
                         "达标率": hit/len(chosen), "候选池达标率": candidate_hit,
                         "达标Lift": hit/len(chosen)/candidate_hit,
                         "非负通过率": (hit+neutral)/len(chosen),
                         "候选池非负率": candidate_pass,
                         "非负Lift": (hit+neutral)/len(chosen)/candidate_pass,
                         "入选门槛及以上最多数": max(
                             int(day.score.ge(day.score.iloc[top_n-1]).sum())
                             for _, day in group.groupby("signal_date"))})
    return rows


def main():
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(PRICES, dtype={"symbol6": str})
    data = base.merge(prices, on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(data.signal_date.unique())
    if len(data) != 14292 or len(dates) != 40 or not data.signal_return.between(.03, .06).all():
        raise ValueError("Expected 40 dates of 3%-6% 14:40 candidates")
    for price, col in PRICE_COLUMNS.items():
        ret = data[col]/data.entry_1440-1
        data[f"ret_{col}"] = ret
        data[f"label_{col}"] = label(ret)
    scored = []
    top5 = []
    fold_counts = []
    for window in WINDOWS:
        for ix in range(window, len(dates)):
            training_dates = dates[ix-window:ix]
            train = data[data.signal_date.isin(training_dates)]
            test = data[data.signal_date.eq(dates[ix])]
            if train.signal_date.nunique() != window or test.empty or training_dates[-1] >= dates[ix]:
                raise ValueError("Invalid walk-forward fold")
            for train_price, train_col in PRICE_COLUMNS.items():
                model = fit(train, f"label_{train_col}")
                probabilities = model.predict_proba(test[list(FEATURES)])
                frame = test[["signal_date", "next_date", "symbol6", "name",
                              "entry_1440", *FEATURES]].copy()
                frame["window"] = window
                frame["train_start"] = training_dates[0]
                frame["train_end"] = training_dates[-1]
                frame["train_price"] = train_price
                frame["prob_critical"] = probabilities[:, 0]
                frame["prob_neutral"] = probabilities[:, 1]
                frame["prob_hit"] = probabilities[:, 2]
                frame["score"] = frame.prob_hit-frame.prob_critical
                frame = frame.sort_values(["score", "symbol6"], ascending=[False, True])
                frame["rank"] = np.arange(1, len(frame)+1)
                for val_price, val_col in PRICE_COLUMNS.items():
                    frame[f"return_{val_col}"] = test.set_index("symbol6").loc[
                        frame.symbol6, f"ret_{val_col}"].to_numpy()
                    frame[f"class_{val_col}"] = test.set_index("symbol6").loc[
                        frame.symbol6, f"label_{val_col}"].to_numpy()
                    evaluation = frame[["signal_date", "next_date", "window",
                                        "train_price", "rank", "score"]].copy()
                    evaluation["val_price"] = val_price
                    evaluation["label"] = frame[f"class_{val_col}"].to_numpy()
                    scored.append(evaluation)
                top5.append(frame[frame["rank"].le(5)])
                fold_counts.append({"训练窗口": window, "训练价格": train_price,
                                    "测试日": dates[ix], "训练开始": training_dates[0],
                                    "训练结束": training_dates[-1], "训练样本": len(train),
                                    "测试候选": len(test),
                                    "训练负类": int(train[f"label_{train_col}"].eq(-1).sum()),
                                    "训练中性": int(train[f"label_{train_col}"].eq(0).sum()),
                                    "训练正类": int(train[f"label_{train_col}"].eq(1).sum())})
            print(f"window {window}: {dates[ix]}", flush=True)
    full = pd.concat(scored, ignore_index=True)
    common_dates = dates[max(WINDOWS):]
    summary = summarize(full, "窗口全部折外日期") + summarize(
        full[full.signal_date.isin(common_dates)], "共同20个折外日期")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary).to_csv(OUT/"summary.csv", index=False)
    pd.DataFrame(fold_counts).to_csv(OUT/"training_folds.csv", index=False)
    pd.concat(top5, ignore_index=True).to_csv(OUT/"daily_top5.csv", index=False)
    daily = []
    for keys, group in full.groupby(["window", "train_price", "val_price", "signal_date"],
                                    sort=False):
        for n in (2, 5):
            chosen = group[group["rank"].le(n)]
            daily.append({"训练窗口": keys[0], "训练价格": keys[1], "验证价格": keys[2],
                          "信号日": keys[3], "测试总候选": len(group), "每日取前": n,
                          "入选总数": len(chosen), "达标": int(chosen.label.eq(1).sum()),
                          "中性": int(chosen.label.eq(0).sum()),
                          "严重错误": int(chosen.label.eq(-1).sum())})
    pd.DataFrame(daily).to_csv(OUT/"daily_results.csv", index=False)
    manifest = {"candidate_condition": "14:40 signal_return in [3%,6%], exactly 14,292 rows over 40 signal dates",
                "classes": {"1": "return >1%", "0": "0% <= return <=1%", "-1": "return <0%"},
                "returns": "maximum of next-day exact 09:31-09:40 1m open/close/high divided by T-day 14:40 price, minus one",
                "score": "P(class=1)-P(class=-1); class=0 is modeled but not directly rewarded",
                "training": "previous 10/15/20 available signal dates; refit each day and each price target; no future test dates",
                "model": "multinomial LogisticRegression(C=.2, lbfgs, max_iter=2000); four numeric factors standardized within fold; three binary passthrough",
                "dates": dates, "common_test_dates": common_dates,
                "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (DATA, PRICES)}}
    (OUT/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    print(pd.DataFrame(summary).query("`每日取前` == 2 and `评价范围` == '共同20个折外日期'").to_string(index=False))


if __name__ == "__main__":
    main()
