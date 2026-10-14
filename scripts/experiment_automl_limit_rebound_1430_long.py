"""Long-window C1430 comparison: 2025 train/valid, 2026 forward test."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_automl_limit_rebound_d0_open_r5 import FEATURES as PRIOR, build_panel, load_daily
from scripts.experiment_automl_limit_rebound_1430_r7 import (
    INTRADAY_FEATURES, add_labels, metrics, scan_local_minutes,
)
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_files, trading_day


OUT = Path("outputs/stock_automl/limit_rebound_1430_long_20261014")
COMPACT_INTRADAY = (
    "d1_open_gap", "d1_1430_return", "d1_range", "d1_1430_vs_vwap",
    "d1_first60_amount_fraction", "d1_amount_ratio_d0",
)
SETS = {"d0_only": PRIOR, "full35": PRIOR + INTRADAY_FEATURES,
        "compact29": PRIOR + COMPACT_INTRADAY}


def model(name: str):
    if name != "regularized35":
        return make_model("shallow_boost")
    return HistGradientBoostingClassifier(
        max_iter=80, learning_rate=.05, max_leaf_nodes=8, max_depth=2,
        min_samples_leaf=200, l2_regularization=10, early_stopping=False,
        random_state=20261013)


def source_days(interim_2026: bool) -> dict:
    days25 = []
    if not interim_2026:
        paths25 = day_files([COMPACT_ROOT_2025])
        days25 = [trading_day(path) for path in paths25]
        if len(days25) < 220 or days25[0] != "2025-01-02" or days25[-1] < "2025-12-31":
            raise FileNotFoundError("The full 2025 minute days are unavailable; do not train a partial 2025 cohort")
    paths26 = day_files([COMPACT_ROOT])
    days26 = [trading_day(path) for path in paths26]
    if "2026-08-07" not in days26:
        raise FileNotFoundError("August 2026 local minute archive is incomplete")
    return {"2025": days25, "2026": [d for d in days26 if d <= "2026-08-07"]}


def build_data(interim_2026: bool) -> tuple[pd.DataFrame, list[str], dict]:
    days = source_days(interim_2026)
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    calendar = sorted(daily.trade_date.unique())
    if not interim_2026:
        expected_2025 = {day for day in calendar if "2025-01-02" <= day <= "2025-12-31"}
        missing_2025 = expected_2025 - set(days["2025"])
        if missing_2025:
            raise FileNotFoundError(f"Missing {len(missing_2025)} 2025 trading-minute days")
    panel, quality = build_panel(daily, OUT)
    wanted = set(days["2025"] + days["2026"])
    panel = panel[panel.d1_date.isin(wanted)].copy()
    panel["d1_i"] = panel.date_i + 1
    parts = []
    for year, root in (("2025", COMPACT_ROOT_2025), ("2026", COMPACT_ROOT)):
        if not days[year]:
            continue
        section = panel[panel.d1_date.isin(days[year])]
        cache = OUT / f"minute_{year}.csv.gz"
        if cache.exists():
            intraday = pd.read_csv(cache, dtype={"symbol": str, "d1_date": str})
        elif year == "2026":
            old = pd.read_csv("outputs/stock_automl/limit_rebound_1430_r7_20261014/minute_1430_features.csv.gz",
                              dtype={"symbol": str, "d1_date": str})
            august = scan_local_minutes(section[section.d1_date.ge("2026-08-03")], root)
            intraday = pd.concat([old, august], ignore_index=True)
            intraday.to_csv(cache, index=False, compression="gzip")
        else:
            intraday = scan_local_minutes(section, root)
            intraday.to_csv(cache, index=False, compression="gzip")
        parts.append(intraday)
    minute = pd.concat(parts, ignore_index=True)
    if minute.duplicated(["symbol", "d1_date"]).any():
        raise ValueError("Duplicate minute features")
    panel = panel.merge(minute, on=["symbol", "d1_date"], validate="one_to_one")
    panel = panel[panel.d1_1430_price.lt(panel.d1_cap - .005)].copy()
    panel = add_labels(panel, daily)
    panel = panel[panel.observed_next3].copy()
    panel.to_csv(OUT / "panel.csv.gz", index=False, compression="gzip")
    return panel, calendar, {"daily_quality": quality, "minute_days": {y: len(v) for y, v in days.items()},
                             "panel_rows": len(panel), "panel_sha256": hashlib.sha256((OUT / "panel.csv.gz").read_bytes()).hexdigest()}


def split(panel: pd.DataFrame, calendar: list[str], interim_2026: bool):
    index = {day: i for i, day in enumerate(calendar)}
    valid_i, test_i = ((index["2026-04-01"], index["2026-06-01"])
                       if interim_2026 else (index["2025-09-01"], index["2026-01-05"]))
    train = panel[panel.d1_i.add(3).lt(valid_i)].copy()
    valid = panel[panel.d1_i.ge(valid_i) & panel.d1_i.add(3).lt(test_i)].copy()
    test = panel[panel.d1_i.ge(test_i)].copy()
    valid_events = set(zip(valid.symbol, valid.first_limit_date))
    test_events = set(zip(test.symbol, test.first_limit_date))
    train = train[[key not in valid_events for key in zip(train.symbol, train.first_limit_date)]]
    refit = panel[panel.d1_i.add(3).lt(test_i)].copy()
    refit = refit[[key not in test_events for key in zip(refit.symbol, refit.first_limit_date)]]
    assert not set(zip(train.symbol, train.first_limit_date)) & valid_events
    assert not set(zip(refit.symbol, refit.first_limit_date)) & test_events
    return train, valid, refit, test


def run(interim_2026: bool = False) -> dict:
    global OUT
    if interim_2026:
        OUT = Path("outputs/stock_automl/limit_rebound_1430_interim_2026_20261014")
    panel, calendar, source = build_data(interim_2026)
    train, valid, refit, test = split(panel, calendar, interim_2026)
    result = {"experiment": "C1430-B_interim_2026" if interim_2026 else "C1430-B_long_window",
              "sklearn": sklearn.__version__,
              "target": "D2-D4 any sealed close", "source": source,
              "splits": {name: {"rows": len(frame), "days": frame.d1_date.nunique(),
                                "positive": int(frame.label_next3.sum()),
                                "unique_events": len(set(zip(frame.symbol, frame.first_limit_date))),
                                "start": frame.d1_date.min(), "end": frame.d1_date.max()}
                         for name, frame in (("train", train), ("valid", valid),
                                             ("refit", refit), ("test", test))},
              "models": {}}
    for name, cols in (*SETS.items(), ("regularized35", SETS["full35"])):
        fitted = model(name)
        fitted.fit(train[list(cols)], train.label_next3,
                   sample_weight=balanced_weights(train.label_next3))
        if name == "full35":
            training = train[["symbol", "first_limit_date", "d0_date", "d1_date", *cols, "label_next3"]]
            training.to_csv(OUT / "full35_train_training.csv.gz", index=False,
                            compression="gzip", float_format="%.17g")
            joblib.dump(fitted, OUT / "full35_train_model.joblib")
        train_score = fitted.predict_proba(train[list(cols)])[:, 1]
        valid_score = fitted.predict_proba(valid[list(cols)])[:, 1]
        final = model(name)
        final.fit(refit[list(cols)], refit.label_next3,
                  sample_weight=balanced_weights(refit.label_next3))
        if name == "full35":
            training = refit[["symbol", "first_limit_date", "d0_date", "d1_date", *cols, "label_next3"]]
            training.to_csv(OUT / "full35_refit_training.csv.gz", index=False,
                            compression="gzip", float_format="%.17g")
            joblib.dump(final, OUT / "full35_refit_model.joblib")
        test_score = final.predict_proba(test[list(cols)])[:, 1]
        result["models"][name] = {"features": cols,
                                  "train_fit_in": metrics(train, train_score, "label_next3"),
                                  "valid": metrics(valid, valid_score, "label_next3"),
                                  "test": metrics(test, test_score, "label_next3")}
        if name == "full35":
            output = train[["symbol", "d0_date", "d1_date", "first_limit_date",
                            "label_next3"]].copy()
            output["score"] = train_score
            output.to_csv(OUT / "full35_train.csv.gz", index=False, compression="gzip")
        for part, frame, score in (("valid", valid, valid_score), ("test", test, test_score)):
            output = frame[["symbol", "d0_date", "d1_date", "first_limit_date",
                            "label_next3"]].copy()
            output["score"] = score
            output.to_csv(OUT / f"{name}_{part}.csv.gz", index=False, compression="gzip")
        print(name, "train/valid/test AP", *(round(result["models"][name][part]["ap"], 4)
                                             for part in ("train_fit_in", "valid", "test")), flush=True)
    (OUT / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interim-2026", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.interim_2026), ensure_ascii=False, indent=2))
