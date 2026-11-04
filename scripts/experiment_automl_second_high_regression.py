"""Fit rounded second-high returns on seven features with rolling date folds."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_close9_models import BINARY, DATA


OUT = Path("docs/stock_automl_runs/20261009_second_high_regression")
MAXIMUMS = Path("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz")
SECOND_HIGHS = OUT / "second_high_prices.csv.gz"
WINDOW = 20


def fetch_second_highs(base: pd.DataFrame, env_file: Path) -> pd.DataFrame:
    conn = db_connection(env_file)
    pieces = []
    try:
        for next_day, group in base.groupby("next_date", sort=True):
            codes = sorted(group.symbol6.unique())
            placeholders = ",".join(["%s"] * len(codes))
            bars = query(conn, "SELECT stk_code, bar_end_time, high_price, "
                         "is_finalized, is_fallback, source_snapshot_time "
                         "FROM aiia_stock_realtime_minute_snapshot_full "
                         "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                         "AND bar_end_time BETWEEN %s AND %s "
                         f"AND stk_code IN ({placeholders}) "
                         "ORDER BY stk_code, bar_end_time",
                         (next_day, f"{next_day} 09:31:00", f"{next_day} 09:40:00", *codes))
            bars["symbol6"] = bars.stk_code.str[:6]
            if len(bars) != len(codes)*10 or set(bars.symbol6) != set(codes):
                raise ValueError(f"Missing ten exact one-minute bars on {next_day}")
            if bars.duplicated(["symbol6", "bar_end_time"]).any():
                raise ValueError(f"Duplicated one-minute bars on {next_day}")
            if not (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
                    pd.to_datetime(bars.source_snapshot_time).eq(
                        pd.to_datetime(bars.bar_end_time))).all():
                raise ValueError(f"Non-exact one-minute bar on {next_day}")
            bars["high_price"] = pd.to_numeric(bars.high_price, errors="raise")
            if not bars.high_price.gt(0).all():
                raise ValueError(f"Invalid high price on {next_day}")
            series = bars.groupby("symbol6").high_price.agg(
                max_high="max", second_high=lambda x: np.sort(x.to_numpy())[-2],
                n_bars="size").reset_index()
            if not series.n_bars.eq(10).all():
                raise ValueError(f"Missing stock bars on {next_day}")
            series["next_date"] = next_day
            pieces.append(series[["next_date", "symbol6", "max_high", "second_high"]])
            print(f"{next_day}: {len(series)} stocks", flush=True)
    finally:
        conn.rollback()
        conn.close()
    result = pd.concat(pieces, ignore_index=True)
    if len(result) != len(base) or result.duplicated(["next_date", "symbol6"]).any():
        raise ValueError("Second-high prices do not match candidates")
    old = pd.read_csv(MAXIMUMS, dtype={"symbol6": str})
    check = result.merge(old[["next_date", "symbol6", "max_high"]],
                         on=["next_date", "symbol6"], validate="one_to_one",
                         suffixes=("_new", "_old"))
    if len(check) != len(base) or not np.allclose(check.max_high_new, check.max_high_old):
        raise ValueError("Fetched ten-bar maxima differ from archived exact maxima")
    OUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(SECOND_HIGHS, index=False, compression="gzip")
    return result


def make_models():
    numeric = [x for x in FEATURES if x not in BINARY]
    binary = [x for x in FEATURES if x in BINARY]
    scaled = ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("binary", "passthrough", binary)], remainder="drop")
    return {
        "训练均值": DummyRegressor(strategy="mean"),
        "岭回归": make_pipeline(scaled, Ridge(alpha=20)),
        "浅回归树": DecisionTreeRegressor(max_depth=3, min_samples_leaf=100,
                                      random_state=42),
        "小型提升树": HistGradientBoostingRegressor(
            max_iter=60, learning_rate=.05, max_leaf_nodes=7,
            min_samples_leaf=100, l2_regularization=5,
            early_stopping=False, random_state=42),
    }


def summarize(frame: pd.DataFrame) -> dict:
    target = frame.target_rounded_pct.to_numpy()
    prediction = frame.predicted_pct.to_numpy()
    ranked = frame.sort_values(["signal_date", "predicted_pct", "symbol6"],
                               ascending=[True, False, True]).copy()
    ranked["rank"] = ranked.groupby("signal_date").cumcount()+1
    top2 = ranked[ranked["rank"].le(2)]
    top5 = ranked[ranked["rank"].le(5)]
    daily_base = frame.groupby("signal_date").second_high_return.apply(
        lambda x: x.gt(.01).mean())
    return {"days": frame.signal_date.nunique(), "stocks": len(frame),
            "MAE_integer_percentage_points": float(mean_absolute_error(target, prediction)),
            "RMSE_integer_percentage_points": float(np.sqrt(mean_squared_error(target, prediction))),
            "R2_integer_target": float(r2_score(target, prediction)),
            "Spearman": float(spearmanr(target, prediction).statistic)
            if np.std(prediction) > 0 else None,
            "pool_second_high_over_1pct": int(frame.second_high_return.gt(.01).sum()),
            "pool_second_high_over_1pct_rate": float(frame.second_high_return.gt(.01).mean()),
            "daily_top2_over_1pct": int(top2.second_high_return.gt(.01).sum()),
            "daily_top2_total": len(top2),
            "daily_top2_random_expected_over_1pct": float(2*daily_base.sum()),
            "daily_top2_mean_raw_return_pct": float(top2.second_high_return.mean()*100),
            "daily_top5_over_1pct": int(top5.second_high_return.gt(.01).sum()),
            "daily_top5_total": len(top5),
            "daily_top5_random_expected_over_1pct": float(5*daily_base.sum())}


def main(env_file: Path, rebuild_prices: bool):
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = (fetch_second_highs(base, env_file) if rebuild_prices or
              not SECOND_HIGHS.exists() else
              pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str}))
    data = base.merge(prices, on=["next_date", "symbol6"], validate="one_to_one")
    if len(data) != 14292 or data.signal_date.nunique() != 40:
        raise ValueError("Candidate/date count changed")
    data["second_high_return"] = data.second_high/data.entry_1440-1
    data["target_rounded_pct"] = np.rint(100*data.second_high_return).astype(int)
    dates = sorted(data.signal_date.unique())
    scored = []
    class_compared = []
    for i in range(WINDOW, len(dates)):
        train = data[data.signal_date.isin(dates[i-WINDOW:i])]
        test = data[data.signal_date.eq(dates[i])]
        x_train = train[list(FEATURES)]
        x_test = test[list(FEATURES)]
        for name, model in make_models().items():
            model.fit(x_train, train.target_rounded_pct)
            frame = test[["signal_date", "next_date", "symbol6", "name",
                          "entry_1440", "max_high", "second_high",
                          "second_high_return", "target_rounded_pct", *FEATURES]].copy()
            frame["model"] = name
            frame["predicted_pct"] = model.predict(x_test)
            scored.append(frame)
        for target_name, target in (
            ("原最高价超过1%分类", "max_high"),
            ("第二高价超过1%分类", "second_high"),
        ):
            model = HistGradientBoostingClassifier(
                max_iter=60, learning_rate=.05, max_leaf_nodes=7,
                min_samples_leaf=100, l2_regularization=5,
                early_stopping=False, random_state=42)
            model.fit(x_train, train[target].div(train.entry_1440).sub(1).gt(.01))
            frame = test[["signal_date", "symbol6", "second_high_return"]].copy()
            frame["model"] = target_name
            frame["score"] = model.predict_proba(x_test)[:, 1]
            class_compared.append(frame)
        print(f"test {dates[i]}: {len(test)} stocks", flush=True)
    result = pd.concat(scored, ignore_index=True)
    metrics = {name: summarize(frame) for name, frame in result.groupby("model")}
    OUT.mkdir(parents=True, exist_ok=True)
    classification = pd.concat(class_compared, ignore_index=True)
    comparison = {}
    for name, frame in classification.groupby("model"):
        frame = frame.sort_values(["signal_date", "score", "symbol6"],
                                  ascending=[True, False, True]).copy()
        frame["rank"] = frame.groupby("signal_date").cumcount()+1
        comparison[name] = {
            "daily_top2_second_high_over_1pct": int(frame.loc[frame["rank"].le(2),
                                                          "second_high_return"].gt(.01).sum()),
            "daily_top5_second_high_over_1pct": int(frame.loc[frame["rank"].le(5),
                                                          "second_high_return"].gt(.01).sum()),
        }
    classification.to_csv(OUT / "classification_controls.csv.gz", index=False,
                          compression="gzip")
    result = result.sort_values(["model", "signal_date", "predicted_pct", "symbol6"],
                                ascending=[True, True, False, True])
    result["rank"] = result.groupby(["model", "signal_date"]).cumcount()+1
    OUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT / "rolling_predictions.csv.gz", index=False, compression="gzip")
    result[result["rank"].le(5)].to_csv(OUT / "daily_top5.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps({
        "target": "second order statistic among next-day 09:31–09:40 ten exact 1m high prices / T 14:40 price - 1",
        "rounding": "np.rint(100 * return): nearest integer percentage point",
        "training_window_days": WINDOW, "test_days": dates[WINDOW:],
        "n_candidates_all_days": len(data),
        "overall_rounded_target_counts": {str(k): int(v) for k, v in
                                          data.target_rounded_pct.value_counts().sort_index().items()},
        "models": metrics,
        "classification_controls_same_boosted_tree_different_targets": comparison,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (DATA, MAXIMUMS, SECOND_HIGHS)},
    }, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    parser.add_argument("--rebuild-prices", action="store_true")
    args = parser.parse_args()
    main(args.env_file, args.rebuild_prices)
