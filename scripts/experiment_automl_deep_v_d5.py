"""Two-stage rolling deep-V research, with a 10:00 minute-close execution audit.

Stage 1: T-1 features, 20 prior trading days, all-stock top 100.
Stage 2: 10:00 features, same rolling window, score stage-1 candidates only.
The stage-2 label requires a post-10:00 running-peak drawdown and a whole-day
deep-V close; trades enter at the *next* minute close after the first trigger.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_deep_v_d3 import BASE, make_model


ROOT = Path("outputs/stock_automl/deep_v_20261013_d5/model_v1")
MORNING = [
    "early30_open_gap", "early30_return", "early30_range", "early30_drawdown",
    "early30_position", "early30_last10_return", "early30_first10_return",
    "early30_volatility", "early30_worst5", "early30_amount_ratio20",
    "early30_amount_trend", "early30_vwap_gap", "early30_vs_prior",
    "early30_relative_return", "early30_market_down_share",
]
SQL = """SELECT LEFT(stk_code,6) AS symbol6,bar_end_time,latest_price
 FROM aiia_stock_realtime_minute_snapshot_full
 WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
 AND stk_code IN ({}) ORDER BY stk_code,bar_end_time"""


def fetch_paths(root: Path, wanted: pd.DataFrame, cache_name: str) -> pd.DataFrame:
    cache = root / cache_name
    cache.mkdir(exist_ok=True)
    load_dotenv(".env")
    with db_connection() as conn:
        for day, group in wanted.groupby("signal_date", sort=True):
            path = cache / f"{day}.csv.gz"
            symbols = sorted(set(group.symbol6))
            if not symbols:
                continue
            if path.exists():
                old = pd.read_csv(path, dtype={"symbol6": str})
                symbols = sorted(set(symbols) - set(old.symbol6))
            else:
                old = pd.DataFrame()
            if symbols:
                fresh = query(conn, SQL.format(",".join(["%s"] * len(symbols))),
                              (day, *symbols))
                fresh.insert(0, "signal_date", day)
                whole = pd.concat([old, fresh], ignore_index=True)
                whole.to_csv(path, index=False, compression="gzip")
                print(f"{cache_name} {day}: +{len(symbols)} symbols", flush=True)
        conn.rollback()
    files = [cache / f"{day}.csv.gz" for day in sorted(wanted.signal_date.unique())]
    return pd.concat([pd.read_csv(path, dtype={"symbol6": str}) for path in files],
                     ignore_index=True) if files else pd.DataFrame()


def path_result(frame: pd.DataFrame) -> dict:
    frame = frame.sort_values("bar_end_time")
    p = pd.to_numeric(frame.latest_price, errors="coerce").to_numpy(float)
    times = frame.bar_end_time.astype(str).str[-8:].to_numpy()
    valid = (len(p) == 240 and len(set(times)) == 240 and
             times[0] == "09:31:00" and times[-1] == "15:00:00" and
             np.isfinite(p).all() and (p > 0).all())
    if not valid:
        return {"path_valid": False, "late_trigger": False,
                "entry": np.nan, "exit": np.nan, "gross_return": np.nan}
    peak = np.maximum.accumulate(p)
    hits = np.flatnonzero((np.arange(len(p)) >= 30) & (1 - p / peak > 0.05))
    # Leave a distinct 15:00 exit bar after the next-bar entry.
    hits = hits[hits + 1 < len(p) - 1]
    if not len(hits):
        return {"path_valid": True, "late_trigger": False,
                "entry": np.nan, "exit": float(p[-1]), "gross_return": np.nan}
    i = int(hits[0])
    return {"path_valid": True, "late_trigger": True,
            "trigger_time": times[i], "entry_time": times[i + 1],
            "entry": float(p[i + 1]), "exit": float(p[-1]),
            "gross_return": float(p[-1] / p[i + 1] - 1)}


def add_morning(panel: pd.DataFrame, morning: pd.DataFrame) -> pd.DataFrame:
    p = panel.merge(morning, on=["signal_date", "symbol6"], how="left",
                    validate="one_to_one")
    p["early30_open_gap"] = p.p31_30 / p.prior_close - 1
    p["early30_vs_prior"] = p.p1000 / p.prior_close - 1
    market = p.groupby("signal_date").early30_vs_prior.agg(
        median="median", down_share=lambda s: s.lt(0).mean())
    p = p.join(market, on="signal_date")
    p["early30_relative_return"] = p.early30_vs_prior - p["median"]
    p["early30_market_down_share"] = p.down_share
    p["v_completed_by_1000"] = p.v_completed_by_1000.fillna(False).astype(bool)
    return p


def safe_auc(y: pd.Series, score: pd.Series) -> float:
    return roc_auc_score(y, score) if y.nunique() == 2 else np.nan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root
    panel = pd.read_csv(root / "panel.csv.gz", dtype={"symbol6": str})
    panel["label"] = pd.to_numeric(panel.label, errors="coerce")
    full = pd.read_csv(root / "full_universe.csv.gz", dtype={"symbol6": str})
    full["full240"] = True
    panel = panel.merge(full, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    panel["full240"] = panel.full240.eq(True)
    morning = pd.read_csv(root / "early30_features.csv.gz", dtype={"symbol6": str})
    panel = add_morning(panel, morning)
    days = sorted(panel.signal_date.unique())
    # The early-trigger cases require a full path to distinguish a continuing
    # dip from a later repeat dip. All other class-1 cases are known from D1.
    events = pd.read_csv(root.parent / "all_events.csv.gz", dtype={"symbol": str})
    events["symbol6"] = events.symbol.str[:6]
    events = events[["day", "symbol6", "first_trigger_slot"]].rename(columns={"day": "signal_date"})
    panel = panel.merge(events, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    panel["late_success"] = (panel.label.eq(1) &
                             panel.first_trigger_slot.gt(30) &
                             panel.first_trigger_slot.le(238) &
                             ~panel.v_completed_by_1000)
    ambiguous = panel[(panel.full240 & panel.label.eq(1) &
                       panel.first_trigger_slot.le(30) &
                       ~panel.v_completed_by_1000)][["signal_date", "symbol6"]]
    print(f"ambiguous class-1 paths: {len(ambiguous)}", flush=True)
    if len(ambiguous):
        bars = fetch_paths(root, ambiguous, "ambiguous_positive_paths")
        outcomes = [{"signal_date": day, "symbol6": symbol, **path_result(g)}
                    for (day, symbol), g in bars.groupby(["signal_date", "symbol6"], sort=False)]
        out = pd.DataFrame(outcomes)
        if not out.path_valid.all():
            raise ValueError("incomplete ambiguous positive path")
        panel = panel.merge(out[["signal_date", "symbol6", "late_trigger"]],
                            on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
        panel["late_success"] |= (panel.label.eq(1) & panel.late_trigger.eq(True) &
                                   ~panel.v_completed_by_1000)
        panel = panel.drop(columns=["late_trigger"])
    panel["late_success"] = panel.late_success.astype(bool)
    panel.to_csv(root / "panel_two_stage.csv.gz", index=False, compression="gzip")

    predictions = []
    daily = []
    for i in range(20, len(days)):
        day = days[i]
        train = panel[panel.signal_date.isin(days[i - 20:i]) & panel.full240]
        test = panel[panel.signal_date.eq(day)].copy()
        model1 = make_model("hgb_prior")
        model1.fit(train[BASE].replace([np.inf, -np.inf], np.nan),
                   train.label.eq(1).astype(int))
        test["stage1_score"] = model1.predict_proba(
            test[BASE].replace([np.inf, -np.inf], np.nan))[:, 1]
        test = test.sort_values(["stage1_score", "symbol6"],
                                ascending=[False, True]).reset_index(drop=True)
        test["stage1_rank"] = np.arange(1, len(test) + 1)
        observed = test[test.full240]
        y1 = observed.label.eq(1)
        d = {"day": day, "train_start": days[i - 20], "train_end": days[i - 1],
             "train_rows_stage1": len(train), "train_deepv": int(train.label.eq(1).sum()),
             "score_universe": len(test), "full240": len(observed),
             "deepv_all": int(y1.sum()), "stage1_auc": safe_auc(y1, observed.stage1_score),
             "stage1_ap": average_precision_score(y1, observed.stage1_score),
             "base_deepv_rate": float(y1.mean())}
        for n in (20, 50, 100, 200):
            top = test.head(n)
            d[f"s1_top{n}_deepv"] = int(top.label.eq(1).sum())
            d[f"s1_top{n}_late_success"] = int(top.late_success.sum())
            d[f"s1_top{n}_events"] = int(top.is_event.sum())
        shortlist = test.head(100).copy()
        shortlist["stage2_eligible"] = shortlist.full240 & shortlist.p1000.notna() & ~shortlist.v_completed_by_1000
        stage2_train = train[train.p1000.notna() & ~train.v_completed_by_1000]
        model2 = make_model("hgb_morning")
        model2.fit(stage2_train[BASE + MORNING].replace([np.inf, -np.inf], np.nan),
                   stage2_train.late_success.astype(int))
        shortlist["stage2_score"] = np.nan
        eligible = shortlist.stage2_eligible
        shortlist.loc[eligible, "stage2_score"] = model2.predict_proba(
            shortlist.loc[eligible, BASE + MORNING].replace([np.inf, -np.inf], np.nan))[:, 1]
        shortlist["stage2_rank"] = np.nan
        shortlist.loc[eligible, "stage2_rank"] = shortlist.loc[eligible, "stage2_score"].rank(
            ascending=False, method="first")
        e = shortlist[eligible].copy()
        y2 = e.late_success.astype(int)
        d.update({"train_rows_stage2": len(stage2_train),
                  "train_late_success": int(stage2_train.late_success.sum()),
                  "stage2_eligible": len(e), "stage2_late_success": int(y2.sum()),
                  "stage2_base_rate": float(y2.mean()) if len(e) else np.nan,
                  "stage2_auc": safe_auc(y2, e.stage2_score),
                  "stage2_ap": average_precision_score(y2, e.stage2_score)
                  if y2.sum() else np.nan,
                  "early_completed_excluded": int(shortlist.v_completed_by_1000.sum())})
        for n in (10, 20, 30):
            d[f"s2_top{n}_late_success"] = int(e.nsmallest(n, "stage2_rank").late_success.sum())
            d[f"s1_eligible_top{n}_late_success"] = int(e.nsmallest(n, "stage1_rank").late_success.sum())
        predictions.append(shortlist)
        daily.append(d)
        print(f"modeled {day}: s1 100 deepV={d['s1_top100_deepv']}, "
              f"s2 eligible={len(e)} lateV={int(y2.sum())}", flush=True)
    top = pd.concat(predictions, ignore_index=True)
    day_metrics = pd.DataFrame(daily)
    top.to_csv(root / "two_stage_top100.csv.gz", index=False, compression="gzip")
    day_metrics.to_csv(root / "two_stage_daily.csv", index=False)

    # All shortlisted paths are fetched for a separate, honest close-to-close
    # minute-trigger audit. The classification metrics above remain unchanged.
    paths = fetch_paths(root, top[["signal_date", "symbol6"]], "top100_paths")
    outcomes = pd.DataFrame([{"signal_date": day, "symbol6": symbol, **path_result(g)}
                             for (day, symbol), g in paths.groupby(["signal_date", "symbol6"], sort=False)])
    if outcomes.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("duplicate path result")
    top = top.merge(outcomes, on=["signal_date", "symbol6"], how="left",
                    validate="one_to_one")
    top["path_valid"] = top.path_valid.eq(True)
    top["late_trigger"] = top.late_trigger.eq(True) & top.stage2_eligible
    top.to_csv(root / "two_stage_trades.csv.gz", index=False, compression="gzip")
    print({"test_days": len(day_metrics), "stage1_deepv_top100": int(day_metrics.s1_top100_deepv.sum()),
           "stage2_eligible": int(day_metrics.stage2_eligible.sum()),
           "stage2_late_success": int(day_metrics.stage2_late_success.sum()),
           "stage2_top20_success": int(day_metrics.s2_top20_late_success.sum())}, flush=True)


if __name__ == "__main__":
    main()
