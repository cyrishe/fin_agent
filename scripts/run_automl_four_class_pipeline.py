"""Eight-stage 14:40 four-class replay with recent-20/similar-20 training.

The current-day inference stage never reads current-day outcomes. Historical
minute prices stand in for a live quote snapshot; see the run report for the
limits of this retrospective execution-price proxy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, roc_auc_score

from scripts.audit_automl_four_class_decisions import class_priority_fallback
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class, models


RAW = Path("outputs/stock_automl/tail_20261010_wide_025_065/candidates_1440.csv")
OLD = Path("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz")
SECOND = Path("docs/stock_automl_runs/20261009_second_high_regression/second_high_prices.csv.gz")
OUT = Path("docs/stock_automl_runs/20261010_four_class_similar_days")
MARKET = OUT / "market_states_1440.csv"
PREPARED = OUT / "prepared_candidates.csv.gz"
STATE_COLUMNS = ("candidate_count", "sh_return", "sz_return", "bj_return",
                 "sh_volume_hands", "sz_volume_hands", "bj_volume_hands")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_candidates(raw_path: Path, old_path: Path, second_path: Path,
                       output: Path, env_file: Path) -> pd.DataFrame:
    """Prepare historical features and post-event outcomes as separate columns."""
    raw = pd.read_csv(raw_path, dtype={"symbol6": str}, low_memory=False)
    raw = raw[raw.signal_return.between(.03, .06, inclusive="both") &
              raw.feature_complete].copy()
    raw = raw[["signal_date", "next_date", "symbol6", "name", "signal_price",
               "label_complete", *FEATURES]].copy()
    if raw.duplicated(["signal_date", "symbol6"]).any() or raw[list(FEATURES)].isna().any().any():
        raise ValueError("Invalid 14:40 feature rows")
    old = pd.read_csv(old_path, dtype={"symbol6": str},
                      usecols=["signal_date", "symbol6", "close_40"])
    second = pd.read_csv(second_path, dtype={"symbol6": str},
                         usecols=["next_date", "symbol6", "second_high"])
    rows = raw.merge(old, on=["signal_date", "symbol6"], how="left",
                     validate="one_to_one")
    rows = rows.merge(second, on=["next_date", "symbol6"], how="left",
                      validate="one_to_one")
    missing = rows[rows.label_complete & rows.second_high.isna()].copy()
    observed = []
    if len(missing):
        conn = db_connection(env_file)
        try:
            for day, group in missing.groupby("next_date", sort=True):
                codes = sorted(group.symbol6.tolist())
                placeholders = ",".join(["%s"] * len(codes))
                bars = query(conn, "SELECT stk_code AS symbol6, bar_end_time, high_price, "
                             "latest_price, is_finalized, is_fallback, source_snapshot_time "
                             "FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s "
                             "AND kline_type='1m' AND period_minutes=1 "
                             "AND bar_end_time BETWEEN %s AND %s "
                             f"AND stk_code IN ({placeholders})",
                             (day, f"{day} 09:31:00", f"{day} 09:40:00", *codes))
                bars["bar_end_time"] = pd.to_datetime(bars.bar_end_time)
                if (len(bars) != len(codes)*10 or bars.duplicated(["symbol6", "bar_end_time"]).any()
                        or not bars.is_finalized.eq(1).all() or not bars.is_fallback.eq(0).all()
                        or not pd.to_datetime(bars.source_snapshot_time).eq(bars.bar_end_time).all()):
                    raise ValueError(f"Missing exact second-high bars for {day}")
                bars["high_price"] = pd.to_numeric(bars.high_price, errors="raise")
                bars["latest_price"] = pd.to_numeric(bars.latest_price, errors="raise")
                if not bars.high_price.gt(0).all():
                    raise ValueError(f"Invalid morning high on {day}")
                for code, stock in bars.groupby("symbol6"):
                    observed.append({"next_date": day, "symbol6": code,
                                     "extra_second_high": float(np.sort(stock.high_price)[-2]),
                                     "extra_close_40": float(stock.loc[
                                         stock.bar_end_time.eq(pd.Timestamp(f"{day} 09:40:00")),
                                         "latest_price"].iloc[0])})
        finally:
            conn.rollback()
            conn.close()
        extra = pd.DataFrame(observed)
        rows = rows.merge(extra, on=["next_date", "symbol6"], how="left",
                          validate="one_to_one")
        rows["second_high"] = rows.second_high.fillna(rows.extra_second_high)
        rows["close_40"] = rows.close_40.fillna(rows.extra_close_40)
        rows = rows.drop(columns=["extra_second_high", "extra_close_40"])
    if rows.loc[rows.label_complete, ["second_high", "close_40"]].isna().any().any():
        raise ValueError("A complete historical label lacks its exact outcome")
    if rows.loc[~rows.label_complete, ["second_high", "close_40"]].notna().any().any():
        raise ValueError("Future-incomplete rows unexpectedly have archived outcomes")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(output, index=False, compression="gzip")
    return rows


# 1. Choose the previous labeled signal dates. The selection method reads
# only market states known by the current signal time, never class outcomes.
def select_training_samples(rows: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    selected = rows[rows.signal_date.isin(dates) & rows.second_high.notna()].copy()
    if selected.empty:
        raise ValueError("Selected training dates have no mature labels")
    return selected


# 2. The seven factors use each training signal day's 14:40 and prior data.
def generate_training_features(rows: pd.DataFrame) -> pd.DataFrame:
    features = rows[list(FEATURES)].copy()
    if features.isna().any().any():
        raise ValueError("Missing seven-factor training input")
    return features


# 3. This stage alone reads the historical T+1 morning second-high price.
def generate_training_labels(rows: pd.DataFrame) -> pd.Series:
    if rows.second_high.isna().any() or not rows.signal_price.gt(0).all():
        raise ValueError("Training target is incomplete")
    return pd.Series(four_class(rows.second_high / rows.signal_price - 1).astype(str),
                     index=rows.index)


# 4. Refit for each signal date; no model is reused from a future fold.
def train_model(features: pd.DataFrame, labels: pd.Series):
    model = models()["small_boosted_tree"]
    model.fit(features, labels)
    return model


# 5. Deliberately project away every outcome column before current-day score.
def select_inference_candidates(rows: pd.DataFrame, day: str) -> pd.DataFrame:
    frame = rows.loc[rows.signal_date.eq(day),
                     ["signal_date", "next_date", "symbol6", "name",
                      "signal_price", *FEATURES]].copy()
    if frame[list(FEATURES)].isna().any().any():
        raise ValueError(f"Incomplete 14:40 inference candidates for {day}")
    return frame


# 6. A probability vector is retained for every candidate, before top-K.
def infer(model, candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        scored = candidates.copy()
        for label in CLASSES:
            scored[f"p_{label}"] = pd.Series(dtype=float)
        scored["predicted_class"] = pd.Series(dtype=str)
        return scored
    probabilities = model.predict_proba(candidates[list(FEATURES)])
    scored = candidates.copy()
    for label in CLASSES:
        scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
    scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
    return scored


# 7. >=3%-argmax first, 1-3%-argmax fallback, at most two; top1 is rank 1.
def select_top(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty:
        empty = scored.copy()
        empty["selection_rank"] = pd.Series(dtype=int)
        return empty
    return class_priority_fallback(scored)


# 8. Outcomes are joined only after prediction and selection have finished.
def verify_next_day(picks: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    outcomes = rows[["signal_date", "symbol6", "second_high", "close_40"]]
    result = picks.merge(outcomes, on=["signal_date", "symbol6"], how="left",
                         validate="one_to_one")
    result["actual_second_high_return"] = result.second_high / result.signal_price - 1
    result["actual_0940_return"] = result.close_40 / result.signal_price - 1
    result["actual_class"] = np.where(result.second_high.notna(),
                                      four_class(result.actual_second_high_return).astype(str),
                                      "UNKNOWN")
    return result


def state_matrix(states: pd.DataFrame) -> pd.DataFrame:
    result = states[list(STATE_COLUMNS)].copy().astype(float)
    for column in ("candidate_count", "sh_volume_hands", "sz_volume_hands",
                   "bj_volume_hands"):
        result[column] = np.log1p(result[column])
    mandatory = result.drop(columns=["bj_return", "bj_volume_hands",
                                     "sh_volume_hands", "sz_volume_hands"])
    if mandatory.isna().any().any() or np.isinf(result.to_numpy()).any():
        raise ValueError("Market state is incomplete")
    return result


def similar_days(states: pd.DataFrame, day: str, count: int = 20) -> tuple[list[str], pd.DataFrame]:
    history = states[states.signal_date.lt(day)].copy()
    target = states[states.signal_date.eq(day)].copy()
    if "market_state_valid" in states:
        history = history[history.market_state_valid].copy()
        if len(target) == 1 and not bool(target.market_state_valid.iloc[0]):
            raise ValueError(f"Current market state fails coverage on {day}")
    if len(history) < count or len(target) != 1:
        raise ValueError("Need 20 prior market states and exactly one current state")
    previous = state_matrix(history)
    current = state_matrix(target).iloc[0]
    median = previous.median()
    scale = previous.quantile(.75) - previous.quantile(.25)
    scale = scale.where(scale.gt(1e-10), previous.std(ddof=0)).fillna(1)
    scale = scale.where(scale.gt(1e-10), 1)
    squared = ((previous-current)/scale)**2
    history["distance"] = np.sqrt(squared.sum(axis=1) / squared.notna().sum(axis=1))
    ordered = history.sort_values(["distance", "signal_date"], ascending=[True, False])
    selected = ordered.head(count).signal_date.tolist()
    return selected, ordered[["signal_date", "distance"]]


def summary_for_picks(frame: pd.DataFrame) -> dict:
    known = frame[frame.actual_class.ne("UNKNOWN")]
    result = {"picks": len(frame), "known_outcomes": len(known),
              "unknown_outcomes": len(frame)-len(known),
              "selected_days": frame.signal_date.nunique(),
              "ge3": int(known.actual_class.eq("ge3").sum()),
              "ge1": int(known.actual_class.isin(("ge3", "1to3")).sum()),
              "lt0": int(known.actual_class.eq("lt0").sum()),
              "below_minus1": int(known.actual_second_high_return.lt(-.01).sum()),
              "mean_0940_return_pct": float(known.actual_0940_return.mean()*100)
              if len(known) else None}
    return result


def classification_on_common_days(scored: pd.DataFrame, rows: pd.DataFrame,
                                  days: set[str], output: Path) -> dict:
    outcomes = rows[["signal_date", "symbol6", "signal_price", "second_high"]]
    checked = scored[scored.signal_date.isin(days)].merge(
        outcomes, on=["signal_date", "symbol6"], how="left",
        suffixes=("", "_outcome"), validate="many_to_one")
    result = {}
    for method, group in checked.groupby("method"):
        known = group[group.second_high.notna()].copy()
        actual = pd.Series(four_class(known.second_high /
                                      known.signal_price_outcome - 1).astype(str),
                           index=known.index)
        predicted = known.predicted_class.astype(str)
        matrix = confusion_matrix(actual, predicted, labels=list(CLASSES))
        pd.DataFrame(matrix, index=CLASSES, columns=CLASSES).to_csv(
            output / f"confusion_{method}.csv")
        result[method] = {"candidate_rows": len(group), "known_labels": len(known),
                          "unknown_labels": len(group)-len(known),
                          "accuracy": float(accuracy_score(actual, predicted)),
                          "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
                          "ge3_auc": float(roc_auc_score(actual.eq("ge3"), known.p_ge3)),
                          "confusion_rows_actual_columns_predicted": matrix.tolist()}
    return result


def run(rows: pd.DataFrame, states: pd.DataFrame, output: Path,
        test_start_index: int = 20) -> dict:
    states = states.sort_values("signal_date").reset_index(drop=True)
    dates = states.signal_date.tolist()
    if not set(rows.signal_date.unique()).issubset(dates) or len(dates) < test_start_index+1:
        raise ValueError("Market-state dates and candidate dates differ")
    fold_rows, scored_rows, pick_rows, similarity_rows = [], [], [], []
    for day in dates[test_start_index:]:
        past = [d for d in dates if d < day]
        recent = past[-20:]
        methods = [("recent20", recent)]
        if "market_state_valid" not in states or bool(states.loc[
                states.signal_date.eq(day), "market_state_valid"].iloc[0]):
            similar, distances = similar_days(states, day)
            similarity_rows.extend({"test_date": day, "train_date": r.signal_date,
                                    "distance": r.distance, "selected": r.signal_date in similar}
                                   for r in distances.itertuples())
            methods.append(("similar20", similar))
        for method, train_dates in methods:
            if len(train_dates) != 20 or max(train_dates) >= day:
                raise ValueError("Training window reaches current/future day")
            chosen = select_training_samples(rows, train_dates)
            if not (chosen.next_date <= day).all():
                raise ValueError("Training label not mature by inference day")
            x = generate_training_features(chosen)
            y = generate_training_labels(chosen)
            model = train_model(x, y)
            candidates = select_inference_candidates(rows, day)
            scored = infer(model, candidates)
            picks = select_top(scored)
            verified = verify_next_day(picks, rows)
            scored["method"] = method
            verified["method"] = method
            scored_rows.append(scored)
            pick_rows.append(verified)
            fold_rows.append({"test_date": day, "method": method,
                              "training_dates": "|".join(train_dates),
                              "training_rows": len(chosen), "candidate_rows": len(candidates),
                              "top1_symbol": verified.iloc[0].symbol6 if len(verified) else None,
                              "top1_known": bool(verified.iloc[0].actual_class != "UNKNOWN")
                              if len(verified) else None})
        print(f"pipeline {day}: {', '.join(name for name, _ in methods)} complete", flush=True)
    scored = pd.concat(scored_rows, ignore_index=True)
    picks = pd.concat(pick_rows, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "all_predictions.csv.gz", index=False, compression="gzip")
    picks.to_csv(output / "top2_verified.csv", index=False)
    folds.to_csv(output / "folds.csv", index=False)
    pd.DataFrame(similarity_rows).to_csv(output / "similarity_distances.csv", index=False)
    metrics = {}
    for method, group in picks.groupby("method"):
        metrics[method] = {"top1": summary_for_picks(group[group.selection_rank.eq(1)]),
                           "top2": summary_for_picks(group)}
    top1 = picks[picks.selection_rank.eq(1)]
    paired = top1.pivot(index="signal_date", columns="method",
                        values="actual_0940_return").reindex(
                            columns=["recent20", "similar20"]).dropna()
    common_days = set(picks.loc[picks.method.eq("similar20"), "signal_date"])
    metrics["common_days"] = {}
    for method in ("recent20", "similar20"):
        same = picks[picks.signal_date.isin(common_days) & picks.method.eq(method)]
        metrics["common_days"][method] = {
            "top1": summary_for_picks(same[same.selection_rank.eq(1)]),
            "top2": summary_for_picks(same)}
    metrics["candidate_classification_common_days"] = classification_on_common_days(
        scored, rows, common_days, output)
    metrics["paired_top1"] = {
        "known_days": len(paired),
        "similar_minus_recent_mean_pct": float((paired.similar20-paired.recent20).mean()*100)
        if len(paired) else None,
        "similar_better_days": int(paired.similar20.gt(paired.recent20).sum()),
        "recent_better_days": int(paired.similar20.lt(paired.recent20).sum()),
        "same_return_days": int(paired.similar20.eq(paired.recent20).sum())}
    summary = {"model": "unweighted four-class small boosted tree, same parameters for both",
               "pool": "All feature-complete 3-6% 14:40 candidates; current-day labels never gate inference",
               "market_state": "14:40 exchange equal-weight returns and 3%-6% basket cumulative volume by exchange; not official index levels",
               "test_dates": dates[test_start_index:],
               "similarity_skipped_dates": [day for day in dates[test_start_index:]
                                            if day not in set(folds.loc[
                                                folds.method.eq("similar20"), "test_date"])],
               "folds": len(folds),
               "candidate_rows": len(rows), "observable_outcomes": int(rows.second_high.notna().sum()),
               "missing_outcomes": int(rows.second_high.isna().sum()), "metrics": metrics}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--old", type=Path, default=OLD)
    parser.add_argument("--second", type=Path, default=SECOND)
    parser.add_argument("--market", type=Path, default=MARKET)
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    rows = (pd.read_csv(args.prepared, dtype={"symbol6": str}, low_memory=False)
            if args.prepared.exists() else
            prepare_candidates(args.raw, args.old, args.second, args.prepared, args.env_file))
    states = pd.read_csv(args.market)
    summary = run(rows, states, args.output)
    summary["sources"] = {str(path): sha256(path) for path in
                          (args.raw, args.old, args.second, args.market, args.prepared)}
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
