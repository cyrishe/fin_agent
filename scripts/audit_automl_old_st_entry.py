"""Audit historical ST training rows and original four-class picks at 14:40."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


RISK = Path("docs/stock_automl_runs/20261010_four_class_15m_close_rolling_historical_st/old_8to9_excluded_risk.csv")
PICKS = Path("docs/stock_automl_runs/20261010_second_high_four_class_weighted/daily_top2.csv")
BASE = Path("docs/stock_automl_runs/20261009_close9_target/exact_1440_to_close9_candidates.csv.gz")
OUT = Path("docs/stock_automl_runs/20261010_old_four_class_st_execution_audit")
KEY = ["signal_date", "symbol6"]


def fetch_prices(keys: pd.DataFrame, env_file: Path) -> pd.DataFrame:
    conn = db_connection(env_file)
    try:
        codes = sorted(keys.symbol6.unique())
        placeholders = ",".join(["%s"] * len(codes))
        daily = query(conn, "SELECT trade_date AS signal_date,LEFT(stk_code,6) AS symbol6,"
                      "preclose AS daily_preclose,open AS daily_open,"
                      "high AS daily_high,low AS daily_low,close AS daily_close,"
                      "is_limit_price FROM kcrp_stock_price "
                      f"WHERE trade_date BETWEEN %s AND %s AND LEFT(stk_code,6) IN ({placeholders})",
                      (keys.signal_date.min(), keys.signal_date.max(), *codes))
        minute = []
        for day, group in keys.groupby("signal_date", sort=True):
            symbols = sorted(group.symbol6.unique())
            marks = ",".join(["%s"] * len(symbols))
            fetched = query(conn, "SELECT trade_date AS signal_date,LEFT(stk_code,6) AS symbol6,"
                            "stk_name AS minute_name,"
                            "open_price AS bar_open,high_price AS bar_high,"
                            "low_price AS bar_low,latest_price AS bar_close,"
                            "volume AS bar_volume,is_fallback,is_finalized,"
                            "source_snapshot_time,bar_end_time "
                            "FROM aiia_stock_realtime_minute_snapshot_full "
                            "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                            f"AND bar_end_time=%s AND stk_code IN ({marks})",
                            (day, f"{day} 14:40:00", *symbols))
            minute.append(fetched)
    finally:
        conn.rollback()
        conn.close()
    minute = pd.concat(minute, ignore_index=True)
    for frame in (daily, minute):
        frame["signal_date"] = pd.to_datetime(frame.signal_date).dt.strftime("%Y-%m-%d")
        frame["symbol6"] = frame.symbol6.astype(str).str.zfill(6)
        if frame.duplicated(KEY).any():
            raise ValueError("Duplicate daily or minute price")
    prices = keys.merge(daily, on=KEY, validate="one_to_one").merge(
        minute, on=KEY, validate="one_to_one")
    if len(prices) != len(keys):
        raise ValueError("Missing daily or 14:40 bar")
    for field in ("daily_preclose", "daily_open", "daily_high", "daily_low", "daily_close",
                  "is_limit_price", "bar_open", "bar_high", "bar_low", "bar_close",
                  "bar_volume"):
        prices[field] = pd.to_numeric(prices[field], errors="raise")
    prices["bar_exact"] = (prices.is_fallback.eq(0) & prices.is_finalized.eq(1) &
                           pd.to_datetime(prices.source_snapshot_time).eq(
                               pd.to_datetime(prices.bar_end_time)))
    prices["bar_flat"] = prices[["bar_open", "bar_high", "bar_low", "bar_close"]].max(
        axis=1).sub(prices[["bar_open", "bar_high", "bar_low", "bar_close"]].min(
            axis=1)).abs().le(.001)
    prices["daily_up_limit_close"] = (prices.is_limit_price.eq(1) &
                                      prices.daily_close.gt(prices.daily_preclose))
    prices["at_final_limit_at_1440"] = (prices.daily_up_limit_close &
                                        prices.bar_close.sub(prices.daily_close).abs().le(.001))
    prices["nominal_5pct"] = [float((Decimal(str(value)) * Decimal("1.05")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)) for value in prices.daily_preclose]
    prices["at_nominal_5pct"] = prices.bar_close.sub(prices.nominal_5pct).abs().le(.001)
    prices["daily_high_above_nominal_5pct"] = prices.daily_high.gt(
        prices.nominal_5pct + .001)
    prices["minute_name_contains_st"] = prices.minute_name.astype(str).str.contains(
        "ST", case=False, regex=False)
    if not prices.bar_exact.all():
        raise ValueError("Nonexact 14:40 bar in audited stock days")
    return prices


def counts(frame: pd.DataFrame) -> dict:
    at_five = frame[frame.at_nominal_5pct]
    return {"stock_days": len(frame), "daily_up_limit_close": int(frame.daily_up_limit_close.sum()),
            "at_final_limit_at_1440": int(frame.at_final_limit_at_1440.sum()),
            "at_nominal_5pct": len(at_five),
            "nominal_5pct_but_daily_high_above": int(
                at_five.daily_high_above_nominal_5pct.sum()),
            "nominal_5pct_with_zero_1440_volume": int(at_five.bar_volume.eq(0).sum()),
            "zero_1440_volume": int(frame.bar_volume.eq(0).sum()),
            "flat_1440_bar": int(frame.bar_flat.sum()),
            "minute_name_contains_st": int(frame.minute_name_contains_st.sum())}


def run(env_file: Path, out: Path) -> dict:
    risk = pd.read_csv(RISK, dtype={"symbol6": str})
    if len(risk) != 300 or not risk.st_type.isin(("S", "Y")).all():
        raise ValueError("Archived historical ST rows changed")
    picks = pd.read_csv(PICKS, dtype={"symbol6": str})
    picks = picks[picks.model.eq("unweighted") &
                  picks.selection_rule.eq("class_priority_fallback")].copy()
    if len(picks) != 40 or picks.selection_rank.eq(1).sum() != 20:
        raise ValueError("Original best model's 20-day Top2 list changed")
    original_dates = sorted(pd.read_csv(BASE, usecols=["signal_date"]).signal_date.unique())
    train_dates = set(original_dates[:-1])
    keys = pd.concat([risk[KEY], picks[KEY]], ignore_index=True).drop_duplicates()
    prices = fetch_prices(keys, env_file)
    risk = risk.merge(prices, on=KEY, validate="one_to_one")
    picks = picks.merge(prices, on=KEY, validate="one_to_one")
    if not risk.entry_1440.sub(risk.bar_close).abs().le(.001).all() or not (
            picks.entry_1440.sub(picks.bar_close).abs().le(.001).all()):
        raise ValueError("Saved 14:40 entries differ from exact minute bars")
    risk_train = risk[risk.signal_date.isin(train_dates)]
    selected_st = picks.merge(risk[KEY], on=KEY, how="inner")
    summary = {
        "scope": "Original, unweighted four-class boosted tree; 20 prior signal days per fold; class-priority fallback; 2026-09-02 to 2026-09-30 predictions.",
        "criteria": {
            "historical_st": "kcrp_stock_st effective signal-date status S/Y, audited separately",
            "daily_up_limit_close": "kcrp_stock_price is_limit_price=1 and close>preclose",
            "at_final_limit_at_1440": "daily_up_limit_close and exact 14:40 minute close equals daily close within CNY 0.001",
            "nominal_5pct": "preclose*1.05 rounded half-up to CNY 0.01; diagnostic only, not the actual board limit for every stock",
            "execution": "Minute volume or a nonflat bar does not prove that a buy order at the recorded close would fill."},
        "historical_st_full_candidate_pool": counts(risk),
        "historical_st_ever_in_rolling_training": counts(risk_train),
        "historical_st_test_only_final_date": len(risk)-len(risk_train),
        "original_picks_top1": counts(picks[picks.selection_rank.eq(1)]),
        "original_picks_top2": counts(picks),
        "historical_st_directly_selected_top2": len(selected_st),
        "original_top2_min_1440_bar_volume": float(picks.bar_volume.min()),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (RISK, PICKS, BASE)},
    }
    out.mkdir(parents=True, exist_ok=True)
    risk.to_csv(out / "st_stock_days.csv", index=False)
    picks.to_csv(out / "old_top2_stock_days.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(run(args.env_file, args.out), ensure_ascii=False, indent=2))
