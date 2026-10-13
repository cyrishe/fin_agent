"""Later-date cooling-minute ablation using read-only finalized DB bars."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE
from scripts.experiment_automl_limit_rebound_cooling_minute import (
    MINUTE_FEATURES, SOURCE, extract_cooling_minute,
)
from scripts.experiment_automl_limit_rebound_model import (
    evaluate, event_keys, make_model, topk_by_day,
)


LOCAL = Path("outputs/stock_automl/limit_rebound_cooling_minute_r1/cooling_minute_features.csv.gz")
OUT = Path("outputs/stock_automl/limit_rebound_cooling_minute_holdout_r1")


def fetch_db_minutes(panel: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    by_day = panel[["asof_date", "symbol"]].drop_duplicates().groupby("asof_date").symbol.apply(set)
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily.trade_date).dt.strftime("%Y-%m-%d")
    daily["symbol"] = daily.stk_code.astype(str).str[:6]
    closes = daily[daily.trade_date.isin(by_day.index)].set_index(["trade_date", "symbol"]).close.to_dict()
    rows = []
    with db_connection() as conn:
        for index, (day, symbols) in enumerate(by_day.items(), 1):
            marks = ",".join(["%s"] * len(symbols))
            sql = ("SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,"
                   "high_price AS high,low_price AS low,latest_price AS close,"
                   "volume,amount AS turnover FROM aiia_stock_realtime_minute_snapshot_full "
                   "WHERE trade_date=%s AND kline_type=%s AND period_minutes=%s "
                   "AND is_finalized=1 AND is_fallback=0 "
                   "AND source_snapshot_time=bar_end_time AND stk_code IN (" + marks + ")")
            bars = query(conn, sql, (day, "1m", 1, *sorted(symbols)))
            for symbol, group in bars.groupby("symbol"):
                close = closes.get((day, str(symbol)[:6]))
                if close is None:
                    continue
                group = group.copy()
                # This DB table records volume in lots; local MK files use shares.
                group["volume"] = pd.to_numeric(group.volume, errors="coerce") * 100
                values = extract_cooling_minute(group, float(close))
                if values is not None:
                    rows.append({"asof_date": day, "symbol": str(symbol)[:6], **values})
            if index % 10 == 0:
                print(f"DB minute days {index}/{len(by_day)}; complete {len(rows)}", flush=True)
        conn.rollback()
    return pd.DataFrame(rows)


def paired_top5_interval(test: pd.DataFrame, daily_score: np.ndarray,
                         minute_score: np.ndarray, target: str) -> dict:
    daily_hits = topk_by_day(test, daily_score, target).groupby("asof_date")[target].sum()
    minute_hits = topk_by_day(test, minute_score, target).groupby("asof_date")[target].sum()
    delta = (minute_hits - daily_hits).to_numpy()
    rng = np.random.default_rng(20261014)
    sampled = rng.choice(delta, size=(5000, len(delta)), replace=True).mean(axis=1) / 5
    return {"days": len(delta), "top5_precision_delta": float(delta.mean() / 5),
            "top5_precision_delta_95pct_day_bootstrap": np.quantile(sampled, [.025, .975]).tolist()}


def run(source: Path = SOURCE, local: Path = LOCAL, out: Path = OUT) -> dict:
    features = pd.read_csv(source / "features.csv.gz", dtype={"symbol": str})
    labels = pd.read_csv(source / "labels.csv.gz", dtype={"symbol": str})
    panel = features.merge(labels, on=KEY, validate="one_to_one")
    local_minutes = pd.read_csv(local, dtype={"symbol": str})
    train_all = panel[panel.asof_date.between("2026-01-05", "2026-07-31")].merge(
        local_minutes, on=["asof_date", "symbol"], how="inner", validate="one_to_one")
    test_all = panel[panel.asof_date.between("2026-09-01", "2026-10-12")].copy()
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "db_cooling_minute_features.csv.gz"
    if cache.exists():
        db_minutes = pd.read_csv(cache, dtype={"symbol": str})
    else:
        daily = pd.read_csv(source / "daily_source.csv.gz", dtype={"stk_code": str},
                            usecols=["trade_date", "stk_code", "close"])
        db_minutes = fetch_db_minutes(test_all, daily)
        db_minutes.to_csv(cache, index=False, compression="gzip")
    test_all = test_all.merge(db_minutes, on=["asof_date", "symbol"],
                              how="inner", validate="one_to_one")
    if test_all.empty:
        raise ValueError("No later-date complete minute decisions")
    result = {"source_train": "local MK 1m 2026-01 to 07",
              "source_test": "kingdomai.aiia_stock_realtime_minute_snapshot_full finalized exact 1m 2026-09 to 10",
              "train_complete": len(train_all), "test_complete": len(test_all),
              "minute_features": MINUTE_FEATURES, "targets": {}}
    for target, observed in (("label_next_limit_up", "label_next_observed"),
                             ("label_next3_any_limit_up", "label_next3_observed")):
        train = train_all[train_all[observed].eq(True)].copy()
        test = test_all[test_all[observed].eq(True)].copy()
        train = train[~pd.MultiIndex.from_frame(train[["symbol", "first_limit_date"]]).isin(event_keys(test))]
        train[target] = train[target].astype(int)
        test[target] = test[target].astype(int)
        scores = {}
        metrics = {}
        for name, cols in (("daily", BASE), ("daily_plus_cooling_minutes", BASE + tuple(MINUTE_FEATURES))):
            model = make_model("shallow_boost")
            model.fit(train[list(cols)], train[target])
            scores[name] = model.predict_proba(test[list(cols)])[:, 1]
            metrics[name] = evaluate(test, scores[name], target)
        result["targets"][target] = {"train_n": len(train), "test_n": len(test),
            "models": metrics,
            "paired_top5": paired_top5_interval(test, scores["daily"],
                                                  scores["daily_plus_cooling_minutes"], target)}
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


if __name__ == "__main__":
    run()
