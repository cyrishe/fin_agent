"""Proof-of-concept: predict an early exit using only the first six opening bars."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor, export_text

from scripts.explore_automl_opening_shape import (
    classify_group_maxima,
    first_ten_bars,
    group_close_maxima,
)
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025


ROOT = Path("docs/stock_automl_runs")
OUT = ROOT / "20261011_opening_shape_prototype"
CACHE = Path("outputs/stock_automl/opening_shape_top1_2025_2026.csv.gz")
SEP_CACHE = Path("outputs/stock_automl/opening_shape_top1_2026_sep.csv.gz")
SHAPES = ["持续升高", "冲高回落", "持续走低", "起伏/其他"]
DECISION_THRESHOLD = 0.65


def selected_picks() -> pd.DataFrame:
    previous = []
    for folder in ("20261011_mk_2025_mar_aug_backtest",
                   "20261011_mk_2025_sep_oct_backtest"):
        trades = pd.read_csv(ROOT / folder / "trades.csv", dtype={"symbol6": str})
        previous.append(trades[
            trades.selection_rank.eq(1)
            & trades.policy.eq("只去掉14:40涨停ST")
        ][["signal_date", "next_date", "symbol6", "name", "signal_price",
           "signal_return", "volume_ratio", "turnover_so_far_pct",
           "float_mv_100m_cny", "close_40"]])
    selected_2025 = pd.concat(previous, ignore_index=True)
    selected_2025["source"] = "2025_top1"
    selected_2026 = pd.read_csv(
        ROOT / "20261011_mk_1m_tradable_st_rolling20/top1_verified.csv",
        dtype={"symbol6": str})
    selected_2026 = selected_2026[[
        "signal_date", "next_date", "symbol6", "name", "signal_price",
        "signal_return", "volume_ratio", "turnover_so_far_pct",
        "float_mv_100m_cny", "close_40"]]
    selected_2026["source"] = "2026_top1"
    picks = pd.concat([selected_2025, selected_2026], ignore_index=True)
    picks = picks.sort_values("signal_date").reset_index(drop=True)
    if picks.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate selected trade")
    return picks


def feature_record(pick, bars: pd.DataFrame) -> dict:
    if abs(float(bars.close.iloc[-1]) - pick.close_40) > 0.000001:
        raise ValueError(f"09:40 close disagrees with original replay: {pick.signal_date}")
    closes = bars.close.astype(float).tolist()
    maxima = group_close_maxima(closes)
    open_37 = float(bars.open.iloc[6])
    close_40 = float(bars.close.iloc[9])
    record = {
            "signal_date": pick.signal_date,
            "next_date": pick.next_date,
            "symbol6": pick.symbol6,
            "name": pick.name,
            "source": pick.source,
            "signal_price": pick.signal_price,
            "shape_label": classify_group_maxima(maxima),
            "g1_max_close": maxima[0],
            "g2_max_close": maxima[1],
            "g3_max_close": maxima[2],
            "return_0937open_pct": (open_37 / pick.signal_price - 1) * 100,
            "return_0940close_pct": (close_40 / pick.signal_price - 1) * 100,
            "early_advantage_pctpt": (open_37 - close_40) / pick.signal_price * 100,
            "signal_return": pick.signal_return,
            "volume_ratio": pick.volume_ratio,
            "turnover_so_far_pct": pick.turnover_so_far_pct,
            "log_float_mv": np.log1p(max(pick.float_mv_100m_cny, 0)),
    }
    for i, bar in enumerate(bars.itertuples(index=False), start=1):
        if i > 6:
            break
        record[f"open_ret_{i}"] = (bar.open / pick.signal_price - 1) * 100
        record[f"high_ret_{i}"] = (bar.high / pick.signal_price - 1) * 100
        record[f"low_ret_{i}"] = (bar.low / pick.signal_price - 1) * 100
        record[f"close_ret_{i}"] = (bar.close / pick.signal_price - 1) * 100
        record[f"log_volume_{i}"] = np.log1p(bar.volume)
        record[f"log_amount_{i}"] = np.log1p(bar.turnover)
    record["first6_move_pct"] = (bars.close.iloc[5] / bars.open.iloc[0] - 1) * 100
    record["first6_range_pct"] = (
        (bars.high.iloc[:6].max() - bars.low.iloc[:6].min()) / pick.signal_price * 100)
    # The decision features below must be computable at 09:36. The full-ten
    # maxima above are retained only as a retrospective shape label.
    first_group_max = float(bars.close.iloc[:3].max())
    second_group_max = float(bars.close.iloc[3:6].max())
    record["g2_minus_g1_pct"] = (second_group_max - first_group_max) / pick.signal_price * 100
    record["first6_up_bar_count"] = int((bars.close.iloc[:6] > bars.open.iloc[:6]).sum())
    return record


def build_features() -> pd.DataFrame:
    records = []
    picks = selected_picks()
    for index, pick in enumerate(picks.itertuples(index=False), start=1):
        root = COMPACT_ROOT_2025 if pick.source == "2025_top1" else COMPACT_ROOT
        bars = first_ten_bars(pick.next_date, pick.symbol6, root=root)
        records.append(feature_record(pick, bars))
        if index % 40 == 0:
            print(f"opening paths {index}/{len(picks)}", flush=True)
    result = pd.DataFrame(records)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(CACHE, index=False, compression="gzip")
    return result


def build_sep_features() -> pd.DataFrame:
    picks = pd.read_csv(ROOT / "20261011_aug_oct_independent20/top2_verified.csv",
                        dtype={"symbol6": str})
    picks = picks[picks.selection_rank.eq(1) &
                  picks.signal_date.between("2026-09-01", "2026-09-30")].copy()
    picks["source"] = "2026_sep_top1"
    if len(picks) != 20:
        raise ValueError("Expected 20 September 2026 Top1 picks")
    records = []
    with db_connection(Path(".env")) as conn:
        for index, pick in enumerate(picks.itertuples(index=False), start=1):
            day = pick.next_date
            rows = query(conn, """SELECT stk_code, bar_end_time, open_price, high_price,
                low_price, latest_price, volume, amount, is_finalized, is_fallback,
                source_snapshot_time
                FROM aiia_stock_realtime_minute_snapshot_full
                WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                  AND bar_end_time BETWEEN %s AND %s AND stk_code=%s
                ORDER BY bar_end_time""",
                (day, f"{day} 09:31:00", f"{day} 09:40:00", pick.symbol6))
            if len(rows) != 10:
                raise ValueError(f"Missing September 1m bars: {day} {pick.symbol6}")
            if not (rows.is_finalized.eq(1).all() and rows.is_fallback.eq(0).all() and
                    pd.to_datetime(rows.source_snapshot_time).eq(
                        pd.to_datetime(rows.bar_end_time)).all()):
                raise ValueError(f"Non-final September 1m bars: {day} {pick.symbol6}")
            bars = rows.rename(columns={
                "bar_end_time": "time", "stk_code": "symbol",
                "open_price": "open", "high_price": "high", "low_price": "low",
                "latest_price": "close", "amount": "turnover",
            })[["time", "symbol", "open", "high", "low", "close", "volume", "turnover"]]
            if pd.to_datetime(bars.time).dt.strftime("%H:%M").tolist() != [
                    f"09:{minute:02d}" for minute in range(31, 41)]:
                raise ValueError(f"Incorrect September minute sequence: {day}")
            for column in ("open", "high", "low", "close", "volume", "turnover"):
                bars[column] = pd.to_numeric(bars[column], errors="raise")
            if not (bars.high.ge(bars[["open", "close", "low"]].max(axis=1)).all() and
                    bars.low.le(bars[["open", "close", "high"]].min(axis=1)).all() and
                    bars[["volume", "turnover"]].ge(0).all().all()):
                raise ValueError(f"Invalid September OHLCVA: {day} {pick.symbol6}")
            # Database volume is in 100-share lots; MK archive volume is shares.
            bars["volume"] *= 100
            records.append(feature_record(pick, bars))
            if index % 5 == 0:
                print(f"September DB paths {index}/{len(picks)}", flush=True)
    result = pd.DataFrame(records)
    SEP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(SEP_CACHE, index=False, compression="gzip")
    return result


def evaluate_group(rows: pd.DataFrame, early: np.ndarray) -> dict:
    early = np.asarray(early, dtype=bool)
    if len(rows) != len(early):
        raise ValueError("Decision mask length mismatch")
    baseline = rows.return_0940close_pct.to_numpy()
    chosen = np.where(early, rows.return_0937open_pct.to_numpy(), baseline)
    delta = chosen - baseline
    triggered = delta[early]
    return {
        "samples": len(rows),
        "early_exits": int(early.sum()),
        "baseline_mean_pct": round(float(baseline.mean()), 6),
        "policy_mean_pct": round(float(chosen.mean()), 6),
        "paired_improvement_pctpt": round(float(delta.mean()), 6),
        "total_improvement_pctpt": round(float(delta.sum()), 6),
        "early_wins": int((triggered > 0).sum()),
        "early_losses": int((triggered < 0).sum()),
        "early_delta_median_pctpt": round(float(np.median(triggered)), 6) if len(triggered) else None,
    }


def rolling_probabilities(data: pd.DataFrame, features: list[str]) -> dict[tuple[str, str], float]:
    ordered = data.sort_values("signal_date").reset_index(drop=True)
    result = {}
    for row in ordered.itertuples(index=False):
        history = ordered[ordered.signal_date.lt(row.signal_date)].tail(120)
        if len(history) < 120:
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
        model.fit(history[features], history.early_advantage_pctpt.gt(0))
        point = pd.DataFrame([row._asdict()])[features]
        result[(row.signal_date, row.symbol6)] = float(model.predict_proba(point)[0, 1])
    return result


def main() -> None:
    data = pd.read_csv(CACHE, dtype={"symbol6": str}) if CACHE.exists() else build_features()
    sep = pd.read_csv(SEP_CACHE, dtype={"symbol6": str}) if SEP_CACHE.exists() else build_sep_features()
    data = pd.concat([data, sep], ignore_index=True)
    training = data[(data.source == "2025_top1") &
                    data.signal_date.between("2025-03-05", "2025-08-29")].copy()
    validation = data[(data.source == "2025_top1") &
                      data.signal_date.between("2025-09-01", "2025-10-31")].copy()
    test = data[data.source == "2026_top1"].copy()
    sep_test = data[data.source == "2026_sep_top1"].copy()
    if (len(training), len(validation), len(test), len(sep_test)) != (120, 39, 115, 20):
        raise ValueError(f"Unexpected chronological sample counts: "
                         f"{len(training)}, {len(validation)}, {len(test)}, {len(sep_test)}")
    morning = [name for name in data.columns if name.startswith((
        "open_ret_", "high_ret_", "low_ret_", "close_ret_", "log_volume_", "log_amount_"))]
    morning += ["first6_move_pct", "first6_range_pct", "g2_minus_g1_pct",
                "first6_up_bar_count"]
    prior = ["signal_return", "volume_ratio", "turnover_so_far_pct", "log_float_mv"]
    if data[morning + prior].isna().any().any():
        raise ValueError("Missing model features")
    shape_model = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
    shape_model.fit(training[morning], training.shape_label)
    action_model = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
    action_model.fit(training[morning], training.early_advantage_pctpt.gt(0))
    action_plus_prior = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
    action_plus_prior.fit(training[morning + prior], training.early_advantage_pctpt.gt(0))
    shallow_tree = DecisionTreeRegressor(max_depth=2, min_samples_leaf=15, random_state=20261011)
    shallow_tree.fit(training[morning], training.early_advantage_pctpt)
    direct_gain_model = make_pipeline(StandardScaler(), Ridge(alpha=100))
    direct_gain_model.fit(training[morning + prior], training.early_advantage_pctpt)
    rolling_prob = rolling_probabilities(data, morning + prior)
    results = {"sample_counts": {"train": len(training), "validation": len(validation),
                                  "test": len(test), "sep_test": len(sep_test)},
               "threshold": DECISION_THRESHOLD,
               "feature_count": {"morning": len(morning), "prior": len(prior)},
               "shallow_tree_rule": export_text(shallow_tree, feature_names=morning),
               "shape_counts": {name: data[data.source.eq(name)].shape_label.value_counts().to_dict()
                                for name in ("2025_top1", "2026_top1", "2026_sep_top1")},
               "splits": {}}
    prediction_rows = []
    for name, group in (("2025_validation", validation), ("2026_test", test),
                        ("2026_sep_test", sep_test)):
        shape_pred = shape_model.predict(group[morning])
        p_down = shape_model.predict_proba(group[morning])[:,
                                           list(shape_model[-1].classes_).index("持续走低")]
        p_early = action_model.predict_proba(group[morning])[:, 1]
        p_early_plus = action_plus_prior.predict_proba(group[morning + prior])[:, 1]
        p_early_rolling = np.array([
            rolling_prob[(row.signal_date, row.symbol6)]
            for row in group.itertuples(index=False)])
        predicted_early_gain = shallow_tree.predict(group[morning])
        predicted_direct_gain = direct_gain_model.predict(group[morning + prior])
        early = p_early >= DECISION_THRESHOLD
        early_plus = p_early_plus >= DECISION_THRESHOLD
        early_rolling = p_early_rolling >= DECISION_THRESHOLD
        early_tree = predicted_early_gain >= .25
        early_direct_gain = predicted_direct_gain >= .5
        early_from_shape = np.isin(shape_pred, ["冲高回落", "持续走低"])
        simple_rule = group.g2_minus_g1_pct.lt(0).to_numpy() & group.first6_move_pct.lt(0).to_numpy()
        results["splits"][name] = {
            "shape_accuracy": round(float(accuracy_score(group.shape_label, shape_pred)), 6),
            "shape_balanced_accuracy": round(float(balanced_accuracy_score(group.shape_label,
                                                                            shape_pred)), 6),
            "shape_confusion": pd.DataFrame(confusion_matrix(group.shape_label, shape_pred,
                                                               labels=SHAPES),
                                             index=SHAPES, columns=SHAPES).to_dict(),
            "always_0937_open": evaluate_group(group, np.ones(len(group), dtype=bool)),
            "prefix_rule": evaluate_group(group, simple_rule),
            "morning_model": evaluate_group(group, early),
            "morning_plus_prior": evaluate_group(group, early_plus),
            "rolling_plus_prior": evaluate_group(group, early_rolling),
            "shallow_tree_exploratory": evaluate_group(group, early_tree),
            "direct_gain_exploratory": evaluate_group(group, early_direct_gain),
            "shape_to_exit": evaluate_group(group, early_from_shape),
        }
        for i, rec in enumerate(group.to_dict("records")):
            prediction_rows.append({
                "split": name, "signal_date": rec["signal_date"],
                "next_date": rec["next_date"],
                "symbol6": rec["symbol6"], "name": rec["name"],
                "actual_shape": rec["shape_label"], "predicted_shape": shape_pred[i],
                "p_sustained_down": p_down[i],
                "g1_max_close": rec["g1_max_close"],
                "g2_max_close": rec["g2_max_close"],
                "g3_max_close": rec["g3_max_close"],
                "p_early_morning": p_early[i], "p_early_plus_prior": p_early_plus[i],
                "p_early_rolling": p_early_rolling[i],
                "tree_predicted_early_gain_pctpt": predicted_early_gain[i],
                "ridge_predicted_early_gain_pctpt": predicted_direct_gain[i],
                "return_0937open_pct": rec["return_0937open_pct"],
                "return_0940close_pct": rec["return_0940close_pct"],
                "early_advantage_pctpt": rec["early_advantage_pctpt"],
                "morning_model_early": bool(early[i]),
                "shallow_tree_early": bool(early_tree[i]),
                "ridge_early": bool(early_direct_gain[i]),
                "rolling_model_early": bool(early_rolling[i]),
                "shape_to_exit_early": bool(early_from_shape[i]),
                "prefix_rule_early": bool(simple_rule[i]),
            })
    OUT.mkdir(parents=True, exist_ok=True)
    predictions = pd.DataFrame(prediction_rows)
    predictions.to_csv(OUT / "exit_predictions.csv", index=False, float_format="%.6f")
    predictions[["split", "signal_date", "next_date", "symbol6", "name",
                 "g1_max_close", "g2_max_close", "g3_max_close",
                 "actual_shape", "predicted_shape", "p_sustained_down"]].to_csv(
                     OUT / "shape_predictions.csv", index=False, float_format="%.6f")
    (OUT / "model_summary.json").write_text(json.dumps(results, ensure_ascii=False,
                                                         indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
