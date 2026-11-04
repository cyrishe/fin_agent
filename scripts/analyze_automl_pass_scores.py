"""Audit whether the eight-factor pass score has absolute or day-level meaning.

Only chronological out-of-fold stock-day scores are evaluated. Market breadth
uses exact 14:40 minute prices and the day's preclose reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values
from sklearn.metrics import brier_score_loss, roc_auc_score

from scripts.experiment_automl_1450_grid import frame, read_only_db
from scripts.experiment_automl_tail_three_class import day_folds, read_samples


def market_breadth(conn, day: str):
    bars = frame(conn, """SELECT stk_code AS symbol6, latest_price, is_fallback,
                             is_finalized, source_snapshot_time
                      FROM aiia_stock_realtime_minute_snapshot_full
                      WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                        AND bar_end_time=%s""", (day, f"{day} 14:40:00"))
    preclose = frame(conn, """SELECT LEFT(stk_code,6) AS symbol6, preclose
                          FROM kcrp_stock_price WHERE trade_date=%s""", (day,))
    bars = bars[bars.is_fallback.eq(0) & bars.is_finalized.eq(1) &
                pd.to_datetime(bars.source_snapshot_time).eq(
                    pd.Timestamp(f"{day} 14:40:00"))]
    preclose = preclose.drop_duplicates("symbol6", keep=False)
    joined = bars.merge(preclose, on="symbol6", validate="one_to_one")
    joined["return_1440"] = (pd.to_numeric(joined.latest_price, errors="coerce") /
                             pd.to_numeric(joined.preclose, errors="coerce") - 1)
    joined = joined[joined.return_1440.notna() & joined.preclose.gt(0)]
    if len(joined) < 4000:
        raise ValueError(f"Only {len(joined)} exact 14:40 market rows on {day}")
    return {"day": day, "stocks": len(joined),
            "up_fraction": float(joined.return_1440.gt(0).mean()),
            "median_return": float(joined.return_1440.median()),
            "over_1pct_fraction": float(joined.return_1440.gt(.01).mean()),
            "below_minus_1pct_fraction": float(joined.return_1440.lt(-.01).mean())}


def analyze(workbook_path: Path, samples_path: Path, today_path: Path,
            env_path: Path, output_path: Path):
    rows = pd.DataFrame(json.loads(workbook_path.read_text())["all"])
    rows["pass"] = rows["class"].ne(-1).astype(int)
    samples = read_samples(samples_path)
    training_priors = {fold: float(train["class"].ne(-1).mean())
                       for fold, (train, _) in enumerate(day_folds(samples), 1)}
    rows["training_prior"] = rows.test_fold.map(training_priors)
    if rows.training_prior.isna().any():
        raise ValueError("Missing chronological training prior")
    bins = [0, .6, .65, .7, .75, .8, 1.000001]
    calibration = []
    for left, right in zip(bins, bins[1:]):
        part = rows[rows.binary_pass_score.ge(left) & rows.binary_pass_score.lt(right)]
        calibration.append({"score_range": [left, right], "n": len(part),
                            "mean_score": float(part.binary_pass_score.mean()) if len(part) else None,
                            "actual_pass_rate": float(part["pass"].mean()) if len(part) else None,
                            "actual_over_1pct_rate": float(part["class"].eq(1).mean()) if len(part) else None})
    folds = []
    for fold, group in rows.groupby("test_fold"):
        folds.append({"fold": int(fold), "n": len(group),
                      "mean_score": float(group.binary_pass_score.mean()),
                      "actual_pass_rate": float(group["pass"].mean()),
                      "auc": float(roc_auc_score(group["pass"], group.binary_pass_score))})
    daily_pool = rows.groupby("signal_date").agg(
        pool_n=("pass", "size"), pool_pass=("pass", "mean"),
        pool_over_1pct=("class", lambda values: values.eq(1).mean()),
        pool_mean_score=("binary_pass_score", "mean"))
    top2 = rows[rows.rank_binary.le(2)].groupby("signal_date").agg(
        top2_min_score=("binary_pass_score", "min"),
        top2_mean_score=("binary_pass_score", "mean"),
        top2_pass=("pass", "mean"),
        top2_over_1pct=("class", lambda values: values.eq(1).mean()),
        top2_critical=("class", lambda values: values.eq(-1).sum()))
    daily = top2.join(daily_pool).reset_index()
    today = json.loads(today_path.read_text())
    config = dotenv_values(env_path)
    url = config.get("SIMPLE_BI_PLATFORM_DB_URL") or config.get("PLATFORM_DB_URL")
    if not url:
        raise ValueError("Missing authorized database URL")
    os.environ["SIMPLE_BI_PLATFORM_DB_URL"] = url
    breadth = []
    with read_only_db() as conn:
        for day in [*daily.signal_date.tolist(), today["signal_date"]]:
            breadth.append(market_breadth(conn, day))
    breadth = pd.DataFrame(breadth)
    daily = daily.merge(breadth.rename(columns={"day": "signal_date"}),
                        on="signal_date", validate="one_to_one")
    today_breadth = breadth[breadth.day.eq(today["signal_date"])].iloc[0]
    history_breadth = breadth[~breadth.day.eq(today["signal_date"])]
    today_top2_min = min(record["binary_pass_score"] for record in today["top5"][:2])
    correlations = {}
    for predictor in ("top2_min_score", "top2_mean_score", "up_fraction",
                      "median_return", "pool_mean_score"):
        correlations[predictor] = {
            outcome: float(daily[predictor].corr(daily[outcome], method="spearman"))
            for outcome in ("top2_pass", "top2_over_1pct", "pool_pass")}
    median_score = daily.top2_min_score.median()
    low = daily[daily.top2_min_score.lt(median_score)]
    high = daily[daily.top2_min_score.ge(median_score)]
    result = {
        "provenance_sha256": {
            "historical_scores": hashlib.sha256(workbook_path.read_bytes()).hexdigest(),
            "training_samples": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
            "today_summary": hashlib.sha256(today_path.read_bytes()).hexdigest(),
            "script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "historical_test_days": len(daily), "historical_scored_rows": len(rows),
        "pass_definition": "class 0 or 1; class -1 is critical",
        "score_mean": float(rows.binary_pass_score.mean()),
        "actual_pass_rate": float(rows["pass"].mean()),
        "auc": float(roc_auc_score(rows["pass"], rows.binary_pass_score)),
        "brier_score": float(brier_score_loss(rows["pass"], rows.binary_pass_score)),
        "chronological_training_prior_brier": float(brier_score_loss(
            rows["pass"], rows.training_prior)),
        "calibration_bins": calibration, "folds": folds,
        "daily_spearman": correlations,
        "top2_daily_min_median": float(median_score),
        "top2_low_score_days": {"days": len(low), "stocks": len(low) * 2,
                                "critical": int(low.top2_critical.sum())},
        "top2_high_score_days": {"days": len(high), "stocks": len(high) * 2,
                                 "critical": int(high.top2_critical.sum())},
        "today": {"date": today["signal_date"], "top2_min_score": today_top2_min,
                  "top2_min_historical_percentile": float(
                      daily.top2_min_score.le(today_top2_min).mean()),
                  "market_up_fraction_1440": float(today_breadth.up_fraction),
                  "market_median_return_1440": float(today_breadth.median_return),
                  "market_up_historical_percentile": float(
                      history_breadth.up_fraction.le(today_breadth.up_fraction).mean()),
                  "market_stocks": int(today_breadth.stocks)},
        "daily": daily.to_dict(orient="records"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({key: value for key, value in result.items() if key not in
                      ("daily", "calibration_bins", "folds")}, ensure_ascii=False,
                     indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path(
        "outputs/stock_automl/tail_critical_review/workbook_data.json"))
    parser.add_argument("--samples", type=Path, default=Path(
        "outputs/stock_automl/tail_standard/trainable_1440.csv"))
    parser.add_argument("--today", type=Path, default=Path(
        "outputs/stock_automl/tail_live_review/2026-10-08_summary.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_score_reliability/summary.json"))
    args = parser.parse_args()
    analyze(args.workbook, args.samples, args.today, args.env_file, args.output)
