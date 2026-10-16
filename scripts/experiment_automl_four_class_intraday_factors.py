"""Historical-only ablation of two 14:40 intraday factors on recent-20 folds."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, roc_auc_score

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class, models
from scripts.run_automl_four_class_pipeline import (
    PREPARED, MARKET, generate_training_labels, select_top, summary_for_picks,
    verify_next_day,
)


OUT = Path("docs/stock_automl_runs/20261010_four_class_intraday_factors")
EXTRA = Path("outputs/stock_automl/four_class_intraday_factors/features.csv.gz")
DRAWDOWN = "drawdown_from_high_1440"
VOLUME_TREND = "max_rising_5m_volume_blocks"
ARMS = {"seven_factors": (), "plus_drawdown": (DRAWDOWN,),
        "plus_volume_trend": (VOLUME_TREND,), "plus_both": (DRAWDOWN, VOLUME_TREND)}


def longest_rising_run(volumes: list[float]) -> int:
    """Count blocks in the longest strictly rising consecutive sequence."""
    longest = current = 1
    for left, right in zip(volumes, volumes[1:]):
        current = current + 1 if right > left else 1
        longest = max(longest, current)
    return longest


def intraday_features(bars: pd.DataFrame, day: str) -> pd.DataFrame:
    """Use 09:31–14:40 highs and eight full five-minute volume blocks."""
    if bars.empty:
        return pd.DataFrame(columns=["signal_date", "symbol6", DRAWDOWN, VOLUME_TREND])
    bars = bars.copy()
    bars["bar_end_time"] = pd.to_datetime(bars.bar_end_time)
    bars = bars[bars.bar_end_time.between(pd.Timestamp(f"{day} 09:31"),
                                          pd.Timestamp(f"{day} 14:40"))].copy()
    bars["minute"] = bars.bar_end_time.dt.hour * 60 + bars.bar_end_time.dt.minute
    bars["block"] = ((bars.minute - 841) // 5).where(bars.minute.between(841, 880))
    valid = (bars.is_finalized.eq(1) & bars.is_fallback.eq(0) &
             pd.to_datetime(bars.source_snapshot_time).eq(bars.bar_end_time))
    bars["valid_bar"] = valid
    bars["high_price"] = pd.to_numeric(bars.high_price, errors="coerce")
    bars["volume"] = pd.to_numeric(bars.volume, errors="coerce")
    rows = []
    for symbol, stock in bars.groupby("symbol6", sort=False):
        full = (len(stock) == 220 and stock.bar_end_time.nunique() == 220 and
                stock.valid_bar.all() and stock.high_price.gt(0).all())
        high = float(stock.high_price.max()) if full else np.nan
        late = stock[stock.block.notna()].copy()
        block = late.groupby("block").agg(bars=("volume", "size"),
                                           volume=("volume", "sum"))
        volume_ok = (full and len(late) == 40 and len(block) == 8 and
                     block.bars.eq(5).all() and late.volume.ge(0).all())
        rows.append({"signal_date": day, "symbol6": symbol,
                     "high_until_1440": high,
                     VOLUME_TREND: longest_rising_run(block.volume.tolist())
                     if volume_ok else np.nan})
    return pd.DataFrame(rows)


def build_features(rows: pd.DataFrame, env_file: Path, output: Path) -> pd.DataFrame:
    conn = db_connection(env_file)
    collected = []
    try:
        for day, group in rows.groupby("signal_date", sort=True):
            codes = group.symbol6.tolist()
            placeholders = ",".join(["%s"] * len(codes))
            bars = query(conn, "SELECT stk_code AS symbol6, bar_end_time, high_price, "
                         "volume, is_finalized, is_fallback, source_snapshot_time "
                         "FROM aiia_stock_realtime_minute_snapshot_full "
                         "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                         "AND bar_end_time BETWEEN %s AND %s "
                         f"AND stk_code IN ({placeholders})",
                         (day, f"{day} 09:31:00", f"{day} 14:40:00", *codes))
            features = intraday_features(bars, day)
            collected.append(features)
            print(f"intraday {day}: {len(features)}/{len(codes)} symbols", flush=True)
    finally:
        conn.rollback()
        conn.close()
    extra = pd.concat(collected, ignore_index=True)
    extra = rows[["signal_date", "symbol6", "signal_price"]].merge(
        extra, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    extra[DRAWDOWN] = extra.signal_price / extra.high_until_1440 - 1
    extra = extra.drop(columns=["signal_price"])
    output.parent.mkdir(parents=True, exist_ok=True)
    extra.to_csv(output, index=False, compression="gzip")
    return extra


def run(rows: pd.DataFrame, extra: pd.DataFrame, dates: list[str], output: Path) -> dict:
    data = rows.merge(extra[["signal_date", "symbol6", DRAWDOWN, VOLUME_TREND]],
                      on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    if len(data) != len(rows):
        raise ValueError("Extra feature join changed the candidate pool")
    if data[DRAWDOWN].dropna().gt(1e-10).any():
        raise ValueError("Historical high is lower than 14:40 price")
    all_scored, all_picks, train_rows = [], [], []
    for day in dates[20:]:
        past = dates[dates.index(day)-20:dates.index(day)]
        training = data[data.signal_date.isin(past) & data.second_high.notna()].copy()
        candidates = data[data.signal_date.eq(day)].copy()
        if not (training.next_date.le(day)).all():
            raise ValueError("Training outcome is not mature at decision time")
        y = generate_training_labels(training)
        for arm, added in ARMS.items():
            feature_columns = [*FEATURES, *added]
            model = models()["small_boosted_tree"]
            model.fit(training[feature_columns], y)
            probabilities = model.predict_proba(candidates[feature_columns])
            scored = candidates[["signal_date", "next_date", "symbol6", "name",
                                 "signal_price", *feature_columns]].copy()
            for label in CLASSES:
                scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
            scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
            scored["arm"] = arm
            all_scored.append(scored)
            picks = verify_next_day(select_top(scored), data)
            picks["arm"] = arm
            all_picks.append(picks)
            train_rows.append({"test_date": day, "arm": arm,
                               "training_rows": len(training), "candidates": len(candidates),
                               "training_dates": "|".join(past)})
        print(f"fit {day}: {len(training)} train, {len(candidates)} candidates", flush=True)
    scored = pd.concat(all_scored, ignore_index=True)
    picks = pd.concat(all_picks, ignore_index=True)
    outcome = data[["signal_date", "symbol6", "signal_price", "second_high"]].rename(
        columns={"signal_price": "outcome_entry"})
    checked = scored.merge(outcome, on=["signal_date", "symbol6"],
                           how="left", validate="many_to_one")
    summary = {"train_window": 20, "test_dates": dates[20:],
               "candidate_rows": len(data), "feature_availability": {
                   feature: {"available": int(data[feature].notna().sum()),
                             "missing": int(data[feature].isna().sum()),
                             "min": float(data[feature].min()),
                             "median": float(data[feature].median()),
                             "max": float(data[feature].max())}
                   for feature in (DRAWDOWN, VOLUME_TREND)},
               "arms": {}}
    for arm, group in picks.groupby("arm", sort=False):
        target = checked[(checked.arm.eq(arm)) & checked.second_high.notna()].copy()
        actual = pd.Series(four_class(target.second_high / target.outcome_entry - 1).astype(str))
        pred = target.predicted_class.astype(str)
        matrix = confusion_matrix(actual, pred, labels=list(CLASSES))
        top1 = group[group.selection_rank.eq(1)]
        summary["arms"][arm] = {
            "top1": summary_for_picks(top1), "top2": summary_for_picks(group),
            "four_class_accuracy": float(accuracy_score(actual, pred)),
            "balanced_accuracy": float(balanced_accuracy_score(actual, pred)),
            "ge3_auc": float(roc_auc_score(actual.eq("ge3"), target.p_ge3)),
            "confusion_rows_actual_columns_predicted": matrix.tolist(),
        }
        pd.DataFrame(matrix, index=CLASSES, columns=CLASSES).to_csv(
            output / f"confusion_{arm}.csv")
    base_top1 = picks[(picks.arm.eq("seven_factors")) & picks.selection_rank.eq(1)]
    paired = {"seven_factors": base_top1.set_index("signal_date").actual_0940_return}
    for arm in ARMS:
        if arm != "seven_factors":
            paired[arm] = picks[(picks.arm.eq(arm)) & picks.selection_rank.eq(1)].set_index(
                "signal_date").actual_0940_return
    paired = pd.DataFrame(paired)
    summary["paired_top1"] = {
        arm: {"common_known_days": int(paired[["seven_factors", arm]].dropna().shape[0]),
              "mean_delta_pct_points": float((paired[arm]-paired.seven_factors).mean()*100),
              "better_days": int(paired[arm].gt(paired.seven_factors).sum()),
              "worse_days": int(paired[arm].lt(paired.seven_factors).sum()),
              "same_days": int(paired[arm].eq(paired.seven_factors).sum())}
        for arm in ARMS if arm != "seven_factors"}
    scored.to_csv(output / "all_predictions.csv.gz", index=False, compression="gzip")
    picks.to_csv(output / "top2_verified.csv", index=False)
    pd.DataFrame(train_rows).to_csv(output / "folds.csv", index=False)
    paired.to_csv(output / "paired_top1_daily_returns.csv")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--market", type=Path, default=MARKET)
    parser.add_argument("--extra", type=Path, default=EXTRA)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    rows = pd.read_csv(args.prepared, dtype={"symbol6": str}, low_memory=False)
    dates = pd.read_csv(args.market).signal_date.tolist()
    extra = (pd.read_csv(args.extra, dtype={"symbol6": str}) if args.extra.exists()
             else build_features(rows, args.env_file, args.extra))
    args.output.mkdir(parents=True, exist_ok=True)
    summary = run(rows, extra, dates, args.output)
    summary["sources_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (args.prepared, args.market, args.extra,
                                              Path(__file__))}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"arms": summary["arms"], "paired_top1": summary["paired_top1"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
