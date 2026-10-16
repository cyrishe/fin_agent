"""Compare 3–6% and 2.5–6.5% 14:40 pools with the same four-class walk-forward models."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix

from scripts.audit_automl_four_class_decisions import class_priority_fallback
from scripts.backtest_automl_staged_minute_exit import exit_on_closes
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class, models


DEFAULT_DATA = Path("outputs/stock_automl/tail_20261010_wide_025_065/trainable_1440.csv")
DEFAULT_OUT = Path("docs/stock_automl_runs/20261010_four_class_wide_025_065")
OLD = Path("outputs/stock_automl/historical_st_8to9/exact_candidates_without_historical_st.csv.gz")
OLD_HIGHS = Path("docs/stock_automl_runs/20261009_second_high_regression/second_high_prices.csv.gz")
MINUTES = tuple(range(31, 41))


def fetch_exact_morning(data: pd.DataFrame, env_file: Path) -> tuple[pd.DataFrame, dict]:
    conn = db_connection(env_file)
    pieces = []
    try:
        for day, group in data.groupby("next_date", sort=True):
            codes = sorted(group.symbol6.unique())
            for start in range(0, len(codes), 500):
                batch = codes[start:start + 500]
                placeholders = ",".join(["%s"] * len(batch))
                bars = query(conn, "SELECT stk_code,bar_end_time,high_price,latest_price,"
                             "is_fallback,is_finalized,source_snapshot_time "
                             "FROM aiia_stock_realtime_minute_snapshot_full "
                             "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                             "AND bar_end_time BETWEEN %s AND %s "
                             f"AND stk_code IN ({placeholders})",
                             (day, f"{day} 09:31:00", f"{day} 09:40:00", *batch))
                if not bars.empty:
                    bars["next_date"] = day
                    bars["symbol6"] = bars.stk_code.astype(str).str[:6]
                    bars["minute"] = pd.to_datetime(bars.bar_end_time).dt.strftime("%H:%M")
                    pieces.append(bars)
            print(f"morning {day}: {len(codes)} candidates", flush=True)
    finally:
        conn.rollback()
        conn.close()
    bars = pd.concat(pieces, ignore_index=True)
    keys = ["next_date", "symbol6"]
    if bars.duplicated(keys + ["minute"]).any():
        raise ValueError("Duplicate exact morning minute")
    valid = (bars.is_fallback.eq(0) & bars.is_finalized.eq(1) &
             pd.to_datetime(bars.source_snapshot_time).eq(pd.to_datetime(bars.bar_end_time)))
    bars["high_price"] = pd.to_numeric(bars.high_price, errors="coerce")
    bars["latest_price"] = pd.to_numeric(bars.latest_price, errors="coerce")
    valid &= bars.high_price.gt(0) & bars.latest_price.gt(0)
    bars = bars[valid].copy()
    grouped = bars.groupby(keys, sort=False)
    complete = grouped.minute.agg(lambda x: set(x) == {f"09:{m:02}" for m in MINUTES})
    keep = complete[complete].index
    bars = bars.set_index(keys).loc[keep].reset_index()
    highs = bars.groupby(keys).high_price.agg(
        second_high=lambda x: float(np.sort(x.to_numpy())[-2])).reset_index()
    closes = bars.pivot(index=keys, columns="minute", values="latest_price")
    closes = closes.rename(columns={f"09:{m:02}": f"close_{m:02}" for m in MINUTES}).reset_index()
    result = highs.merge(closes, on=keys, validate="one_to_one")
    return result, {"input_stock_days": len(data), "exact_stock_days": len(result),
                    "excluded_incomplete_or_nonexact": len(data) - len(result),
                    "valid_minute_bars": len(bars)}


def old_overlap_audit(data: pd.DataFrame) -> dict:
    old = pd.read_csv(OLD, dtype={"symbol6": str})
    highs = pd.read_csv(OLD_HIGHS, dtype={"symbol6": str})
    old = old.merge(highs[["next_date", "symbol6", "second_high"]],
                    on=["next_date", "symbol6"], validate="one_to_one")
    narrow = data[data.signal_return.between(.03, .06, inclusive="both")]
    merged = narrow.merge(old, on=["signal_date", "next_date", "symbol6"],
                          suffixes=("_new", "_old"), validate="one_to_one")
    columns = [*FEATURES, "entry_1440", "second_high", "close_40"]
    max_diff = {col: float((merged[f"{col}_new"] - merged[f"{col}_old"]).abs().max())
                for col in columns}
    return {"old_rows": len(old), "new_narrow_rows": len(narrow),
            "common_rows": len(merged), "old_only": len(old)-len(merged),
            "new_only": len(narrow)-len(merged), "max_abs_difference": max_diff}


def make_scored(data: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    dates = sorted(data.signal_date.unique())
    if len(dates) != 40:
        raise ValueError(f"Expected 40 common signal days; got {len(dates)}")
    scored, folds = [], []
    for end in range(20, 40):
        train = data[data.signal_date.isin(dates[end-20:end])]
        test = data[data.signal_date.eq(dates[end])]
        if train["class"].nunique() != 4:
            raise ValueError(f"Missing target class for {dates[end]}")
        fold = {"test_date": dates[end], "train_rows": len(train),
                "test_rows": len(test), "train_start": dates[end-20],
                "train_end": dates[end-1]}
        for name, model in models().items():
            model.fit(train[list(FEATURES)], train["class"].astype(str))
            fold[f"{name}_train_fit_accuracy"] = float(accuracy_score(
                train["class"].astype(str), model.predict(train[list(FEATURES)])))
            probs = model.predict_proba(test[list(FEATURES)])
            labels = list(model.classes_ if hasattr(model, "classes_")
                          else model[-1].classes_)
            frame = test[["signal_date", "next_date", "symbol6", "name", "signal_return",
                          "entry_1440", "second_high_return", "class",
                          *[f"close_{m:02}" for m in MINUTES]]].copy()
            frame["model"] = name
            for label in CLASSES:
                frame[f"p_{label}"] = probs[:, labels.index(label)]
            frame["predicted_class"] = np.asarray(labels)[probs.argmax(axis=1)]
            fold[f"{name}_test_accuracy"] = float(accuracy_score(
                test["class"].astype(str), frame.predicted_class))
            scored.append(frame)
        folds.append(fold)
        print(f"fit {dates[end]}: {len(train)} train, {len(test)} test", flush=True)
    return pd.concat(scored, ignore_index=True), folds


def metrics(data: pd.DataFrame, scored: pd.DataFrame, dates: list[str]) -> dict:
    result = {"all_rows": len(data), "test_rows": int(len(scored)/2),
              "pool_class_counts": {label: int(data["class"].eq(label).sum())
                                    for label in CLASSES}, "models": {}}
    for model_name, frame in scored.groupby("model"):
        picks = class_priority_fallback(frame)
        model_result = {"accuracy": float(accuracy_score(frame["class"].astype(str),
                                                      frame.predicted_class)),
                        "confusion_rows_actual_columns_predicted": confusion_matrix(
                            frame["class"].astype(str), frame.predicted_class,
                            labels=CLASSES).tolist(), "top": {}}
        for top_k in (1, 2):
            subset = picks[picks.selection_rank.le(top_k)].copy()
            subset["return_0940"] = subset.close_40/subset.entry_1440-1
            subset["return_original"] = [exit_on_closes(
                row.entry_1440, [getattr(row, f"close_{m:02}") for m in MINUTES])[
                    "exit_return"] for row in subset.itertuples()]
            daily = subset.groupby("signal_date").return_0940.mean()
            model_result["top"][str(top_k)] = {
                "selected": len(subset), "days": int(subset.signal_date.nunique()),
                "skipped_days": len(dates)-subset.signal_date.nunique(),
                "actual_ge3": int(subset["class"].eq("ge3").sum()),
                "actual_ge1": int(subset["class"].isin(("ge3", "1to3")).sum()),
                "actual_lt0": int(subset["class"].eq("lt0").sum()),
                "actual_below_minus1": int(subset.second_high_return.lt(-.01).sum()),
                "mean_second_high_pct": float(subset.second_high_return.mean()*100),
                "mean_0940_pct": float(subset.return_0940.mean()*100),
                "median_0940_pct": float(subset.return_0940.median()*100),
                "positive_0940": int(subset.return_0940.gt(0).sum()),
                "mean_original_rules_pct": float(subset.return_original.mean()*100),
                "calendar_mean_0940_with_skip_zero_pct": float(
                    daily.reindex(dates, fill_value=0).mean()*100),
            }
        result["models"][model_name] = model_result
    return result


def run(data_path: Path, env_file: Path, out: Path) -> dict:
    base = pd.read_csv(data_path, dtype={"symbol6": str})
    raw_rows = len(base)
    base = base[base.st_type.isin(("N", "R")) &
                ~base.name.astype(str).str.contains("ST", case=False, regex=False)].copy()
    base["signal_date"] = base.signal_date.astype(str)
    base["next_date"] = base.next_date.astype(str)
    old = pd.read_csv(OLD, dtype={"symbol6": str},
                      usecols=["signal_date", "symbol6"])
    old_keys = pd.MultiIndex.from_frame(old[["signal_date", "symbol6"]])
    keys = pd.MultiIndex.from_frame(base[["signal_date", "symbol6"]])
    narrow = base.signal_return.between(.03, .06, inclusive="both")
    added_in_old_band = int((narrow & ~keys.isin(old_keys)).sum())
    base = base[~narrow | keys.isin(old_keys)].copy()
    if base.duplicated(["signal_date", "symbol6"]).any() or base[list(FEATURES)].isna().any().any():
        raise ValueError("Invalid candidate keys or missing model features")
    exact, coverage = fetch_exact_morning(base, env_file)
    data = base.merge(exact, on=["next_date", "symbol6"], validate="one_to_one")
    data["entry_1440"] = data.signal_price
    data["second_high_return"] = data.second_high/data.entry_1440-1
    data["class"] = four_class(data.second_high_return)
    overlap = old_overlap_audit(data)
    if (overlap["old_only"] or overlap["new_only"] or
            any(value > 1e-6 for value in overlap["max_abs_difference"].values())):
        raise ValueError("The 3–6% baseline does not reproduce the archived clean pool")
    bands = {"3_to_6": data[data.signal_return.between(.03, .06, inclusive="both")].copy(),
             "2p5_to_6p5": data.copy()}
    summary = {"method": "Same 40 historical signal dates, 20 prior signal days per fold, same seven factors, same unweighted four-class model and class-priority fallback; exact ten 1m highs/close; price proxies before costs.",
               "source": {"data": str(data_path), "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest()},
               "pool_reconciliation": {"raw_wide_trainable": raw_rows,
                   "removed_non_nr_or_st_name": raw_rows-len(base)-added_in_old_band,
                   "excluded_new_rows_inside_original_3_to_6_band": added_in_old_band,
                   "new_analysis_pool_before_morning_check": len(base)},
               "coverage": coverage, "old_narrow_overlap": overlap,
               "bands": {}}
    out.mkdir(parents=True, exist_ok=True)
    all_picks = []
    for band, rows in bands.items():
        scored, folds = make_scored(rows)
        dates = sorted(rows.signal_date.unique())[20:]
        result = metrics(rows, scored, dates)
        result["folds"] = folds
        result["mean_fold_train_fit_accuracy"] = {
            name: float(np.mean([fold[f"{name}_train_fit_accuracy"] for fold in folds]))
            for name in models()}
        summary["bands"][band] = result
        scored.to_csv(out / f"predictions_{band}.csv.gz", index=False, compression="gzip")
        for name, frame in scored.groupby("model"):
            picked = class_priority_fallback(frame)
            picked["band"] = band
            all_picks.append(picked)
    pd.concat(all_picks, ignore_index=True).to_csv(out / "daily_top2.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(run(args.data, args.env_file, args.out), ensure_ascii=False, indent=2))
