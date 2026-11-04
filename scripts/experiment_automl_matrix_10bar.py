"""Run the fixed 2 × 1 × 2 × 3 matrix on ten exact morning 1m bars."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_contiguous_close9 import fit


OUT = Path("docs/stock_automl_runs/20261009_tenbar_matrix")
SOURCE_DATES = Path("docs/stock_automl_runs/20261009_market_shape_training/summary.json")
PRICE_COLUMNS = {"开盘价": "max_open", "收盘价": "max_close", "最高价": "max_high"}
MODEL_TYPES = ("逻辑回归", "决策树")


def target_class(ret):
    return np.select([ret > .01, ret < .005], [1, -1], default=0).astype(int)


def fetch_price_extrema(rows, env_file):
    conn = db_connection(env_file)
    pieces = []
    try:
        for next_day, group in rows.groupby("next_date", sort=True):
            codes = sorted(group.symbol6.unique().tolist())
            placeholders = ",".join(["%s"] * len(codes))
            bars = query(conn, "SELECT stk_code, COUNT(*) AS n_bars, "
                         "MAX(open_price) AS max_open, MAX(latest_price) AS max_close, "
                         "MAX(high_price) AS max_high, MIN(is_finalized) AS all_final, "
                         "MAX(is_fallback) AS any_fallback, "
                         "SUM(source_snapshot_time=bar_end_time) AS exact_times "
                         "FROM aiia_stock_realtime_minute_snapshot_full "
                         "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                         "AND bar_end_time BETWEEN %s AND %s "
                         f"AND stk_code IN ({placeholders}) GROUP BY stk_code",
                         (next_day, f"{next_day} 09:31:00", f"{next_day} 09:40:00", *codes))
            if len(bars) != len(codes):
                raise ValueError(f"Missing ten-bar stocks on {next_day}")
            if not (bars.n_bars.eq(10) & bars.all_final.eq(1) &
                    bars.any_fallback.eq(0) & bars.exact_times.eq(10)).all():
                raise ValueError(f"Incomplete or inexact ten-bar prices on {next_day}")
            bars["next_date"] = next_day
            bars["symbol6"] = bars.stk_code.str[:6]
            pieces.append(bars[["next_date", "symbol6", *PRICE_COLUMNS.values()]])
            print(f"{next_day}: {len(bars)} stocks", flush=True)
    finally:
        conn.rollback()
        conn.close()
    extrema = pd.concat(pieces, ignore_index=True)
    if len(extrema) != len(rows) or extrema.duplicated(["next_date", "symbol6"]).any():
        raise ValueError("Ten-bar extrema do not match candidates")
    for col in PRICE_COLUMNS.values():
        extrema[col] = pd.to_numeric(extrema[col], errors="raise")
    if not (extrema[["max_open", "max_close", "max_high"]].gt(0).all().all() and
            extrema.max_high.ge(extrema[["max_open", "max_close"]].max(axis=1)).all()):
        raise ValueError("Invalid minute OHLC relationship")
    OUT.mkdir(parents=True, exist_ok=True)
    extrema.to_csv(OUT / "tenbar_price_extrema.csv.gz", index=False, compression="gzip")
    return extrema


def daily_ranking(model, pool):
    scored = pool.copy()
    scored["score"] = model.predict_proba(scored[list(FEATURES)])[:, 1]
    scored = scored.sort_values(["signal_date", "score", "symbol6"],
                                ascending=[True, False, True])
    scored["rank"] = scored.groupby("signal_date").cumcount() + 1
    return scored


def summarize(scored, top_n, scope, name, price, model_type):
    chosen = scored[scored["rank"].le(top_n)]
    cutoffs = [int(day.score.ge(day.score.iloc[top_n-1]).sum())
               for _, day in scored.groupby("signal_date")]
    y = scored["label"].eq(1).astype(int)
    return {"训练范围": name, "因子": "7因子", "模型结构": model_type,
            "卖价字段": price, "评价日期": scope, "交易日数": scored.signal_date.nunique(),
            "候选股票数": len(scored), "每日取前": top_n, "选中股票数": len(chosen),
            "超过1%": int(chosen.label.eq(1).sum()),
            "0.5%至1%": int(chosen.label.eq(0).sum()),
            "低于0.5%": int(chosen.label.eq(-1).sum()),
            "候选池超过1%比例": float(y.mean()),
            "前列超过1%比例": float(chosen.label.eq(1).mean()),
            "Lift": float(chosen.label.eq(1).mean()/y.mean()),
            "全候选AUC": float(roc_auc_score(y, scored.score)),
            "临界名次最多并列数": max(cutoffs)}


def main(env_file, rebuild_prices):
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    extrema_path = OUT / "tenbar_price_extrema.csv.gz"
    extrema = (fetch_price_extrema(base, env_file) if rebuild_prices or
               not extrema_path.exists() else
               pd.read_csv(extrema_path, dtype={"symbol6": str}))
    data = base.merge(extrema, on=["next_date", "symbol6"], validate="one_to_one")
    if len(data) != 14292 or data.signal_date.nunique() != 40:
        raise ValueError("Candidate or date count changed")
    saved_close = pd.read_csv(
        "docs/stock_automl_runs/20261009_close10_target/exact_0931_closes.csv.gz",
        dtype={"symbol6": str})
    check = data.merge(saved_close, on=["next_date", "symbol6"], validate="one_to_one")
    close_cols = ["close_31", *[f"close_{minute:02}" for minute in range(32, 41)]]
    check["close_31"] = pd.to_numeric(check.close_31)
    if len(check) != len(data) or not np.allclose(
            check.max_close.astype(float), check[close_cols].max(axis=1).astype(float)):
        raise ValueError("Ten-bar closing maxima differ from exact saved minute closes")
    dates = json.loads(SOURCE_DATES.read_text())
    training = {"连续20日（08-25至09-21）": dates["old_training_dates"],
                "选取20日（四类大盘各5日）": dates["new_training_dates"]}
    all_dates = set(data.signal_date.unique())
    if any(len(ds) != 20 or len(set(ds)) != 20 for ds in training.values()):
        raise ValueError("Training dates changed")
    common_dates = sorted(all_dates-set(training[next(iter(training))])-
                          set(training[list(training)[1]]))
    if len(common_dates) != 9:
        raise ValueError("Expected nine dates outside both training sets")
    summary, daily, detail, model_info = [], [], [], []
    model_dir = OUT / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    for scheme, dates_used in training.items():
        train = data[data.signal_date.isin(dates_used)]
        outside = data[~data.signal_date.isin(dates_used)]
        common = outside[outside.signal_date.isin(common_dates)]
        for price, col in PRICE_COLUMNS.items():
            labeled = data.assign(label=target_class(data[col]/data.entry_1440-1),
                                  target_return=data[col]/data.entry_1440-1)
            train_rows = labeled[labeled.signal_date.isin(dates_used)]
            for model_type in MODEL_TYPES:
                if model_type == "逻辑回归":
                    saved_close = Path("docs/stock_automl_runs/20261009_close10_target/"
                                       "新连续七因子_含首分钟收盘目标.joblib")
                    reuse = scheme.startswith("连续") and price == "收盘价"
                    model = (joblib.load(saved_close) if reuse else
                             fit(train_rows.assign(target_class=train_rows.label), FEATURES))
                else:
                    reuse = False
                    model = DecisionTreeClassifier(max_depth=3, min_samples_leaf=200,
                                                   random_state=42)
                    model.fit(train_rows[list(FEATURES)], train_rows.label.eq(1).astype(int))
                model_name = f"{'contiguous' if scheme.startswith('连续') else 'selected'}_" \
                             f"{'open' if price=='开盘价' else 'close' if price=='收盘价' else 'high'}_" \
                             f"{'logistic' if model_type=='逻辑回归' else 'tree'}"
                model_path = model_dir / f"{model_name}.joblib"
                joblib.dump(model, model_path)
                model_info.append({"name": model_name, "training": scheme, "price": price,
                                   "model": model_type, "path": str(model_path),
                                   "reused_saved_model": str(saved_close) if reuse else None,
                                   "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                                   "logistic_intercept": float(model.named_steps["logisticregression"].intercept_[0])
                                   if model_type == "逻辑回归" else None,
                                   "logistic_coefficients": dict(zip(FEATURES, map(float,
                                       model.named_steps["logisticregression"].coef_[0])))
                                   if model_type == "逻辑回归" else None,
                                   "tree_rules": export_text(model, feature_names=list(FEATURES))
                                   if model_type == "决策树" else None})
                for scope, part in (("训练日", labeled[labeled.signal_date.isin(dates_used)]),
                                    ("各自未训练20日", labeled[~labeled.signal_date.isin(dates_used)]),
                                    ("共同未训练9日", labeled[labeled.signal_date.isin(common_dates)])):
                    scored = daily_ranking(model, part)
                    for n in (2, 5):
                        summary.append(summarize(scored, n, scope, scheme, price, model_type))
                    if scope == "各自未训练20日":
                        for day, group in scored.groupby("signal_date"):
                            daily.append({"训练范围": scheme, "因子": "7因子", "模型结构": model_type,
                                          "卖价字段": price, "信号日": day,
                                          "次交易日": group.next_date.iloc[0],
                                          "候选股票数": len(group),
                                          "候选池超过1%": int(group.label.eq(1).sum()),
                                          "前二超过1%": int(group.head(2).label.eq(1).sum()),
                                          "前二低于0.5%": int(group.head(2).label.eq(-1).sum()),
                                          "前五超过1%": int(group.head(5).label.eq(1).sum()),
                                          "前五低于0.5%": int(group.head(5).label.eq(-1).sum())})
                        selected = scored[scored["rank"].le(5)].copy()
                        selected["训练范围"] = scheme
                        selected["模型结构"] = model_type
                        selected["卖价字段"] = price
                        selected["卖价窗口最高值"] = selected[col]
                        selected["前二"] = selected["rank"].le(2).astype(int)
                        selected["前三"] = selected["rank"].le(3).astype(int)
                        selected["前五"] = 1
                        detail.append(selected[["训练范围", "模型结构", "卖价字段", "signal_date",
                                                "next_date", "rank", "前二", "前三", "前五",
                                                "symbol6", "name", "score", "entry_1440",
                                                "卖价窗口最高值", "target_return", "label", *FEATURES]])
    pd.DataFrame(summary).to_csv(OUT / "matrix_summary.csv", index=False)
    pd.DataFrame(daily).to_csv(OUT / "daily_results.csv", index=False)
    pd.concat(detail).to_csv(OUT / "daily_top5.csv", index=False)
    report = {"definition": "T+1 09:31–09:40 exact 1m bars; max(open/close/high)/T 14:40 price - 1; >1%=1, <0.5%=-1, otherwise 0",
              "fit": "Each of 12 combinations trained once. Binary label is target 1 versus 0/-1. LogisticRegression(C=0.2) scales four continuous features and passes three binary features. Tree max_depth=3, min_samples_leaf=200. No model selection or trading simulation.",
              "training_dates": training, "common_out_of_training_dates": common_dates,
              "models": model_info,
              "candidate_counts": {price: {str(k): int((target_class(data[col]/data.entry_1440-1)==k).sum())
                                           for k in (1, 0, -1)}
                                   for price, col in PRICE_COLUMNS.items()},
              "source_sha256": {"candidates": hashlib.sha256(DATA.read_bytes()).hexdigest(),
                                "tenbar_extrema": hashlib.sha256(extrema_path.read_bytes()).hexdigest(),
                                "selected_dates": hashlib.sha256(SOURCE_DATES.read_bytes()).hexdigest()}}
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    result = pd.DataFrame(summary)
    print(result[(result["每日取前"]==2)&(result["评价日期"]!="训练日")].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--rebuild-prices", action="store_true")
    args = parser.parse_args()
    main(args.env_file, args.rebuild_prices)
