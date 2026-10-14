"""Offline 14:40 intraday VWAP feature audit and paired rolling replay.

This experiment does not alter the live strategy or write to the database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from scipy.stats import fisher_exact

from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_second_high_four_class import four_class, models
from scripts.experiment_automl_mk_1m_st_policies import POLICIES, make_cohorts
from scripts.automl_st_status import load_st_intervals
from scripts.mk_minute_archive import day_file
from scripts.run_automl_four_class_pipeline import select_top


OUT = Path("docs/stock_automl_runs/20261012_intraday_vwap")
MK = Path("outputs/stock_automl/mk_1m_compact")
MK_CANDIDATES = Path("outputs/stock_automl/mk_1m_jan_jul/trainable_candidates.csv.gz")
LATE_CANDIDATES = Path("docs/stock_automl_runs/20261011_aug_oct_independent20/candidates.csv.gz")
LATE_MARKET = Path("docs/stock_automl_runs/20261011_aug_oct_independent20/market_context.csv")
OCT12_LABELS = Path("outputs/stock_automl/live_1440/cache/2026-10-12_training.csv.gz")
EARLY_TOP1 = Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv")
LATE_TOP2 = Path("docs/stock_automl_runs/20261011_aug_oct_independent20/top2_verified.csv")
DAILY = Path("docs/stock_automl_runs/20261011_market_state_asof/daily_2026.csv")
EXPECTED_TIMES = set(pd.date_range("09:31", periods=120, freq="min").strftime("%H:%M:%S"))
EXPECTED_TIMES.update(pd.date_range("13:01", periods=100, freq="min").strftime("%H:%M:%S"))


def feature_level(above_ratio: float) -> int:
    return 2 if above_ratio > .95 else 1 if above_ratio >= .85 else 0


def summarize_bars(bars: pd.DataFrame, day: str, volume_multiplier: int) -> pd.DataFrame:
    """Compare each minute close to cumulative price-by-volume through that minute."""
    required = ("symbol6", "clock", "close", "volume", "amount")
    if bars.empty:
        return pd.DataFrame(columns=["signal_date", "symbol6", "vwap_above_minutes",
                                     "vwap_above_ratio", "vwap_all_above", "vwap_level"])
    data = bars[list(required) + (["low"] if "low" in bars else [])].copy()
    data["symbol6"] = data.symbol6.astype(str).str.zfill(6)
    data["clock"] = data.clock.astype(str).str[-8:]
    for col in ("close", "volume", "amount") + (("low",) if "low" in data else ()):
        data[col] = pd.to_numeric(data[col], errors="raise")
    data = data[data.clock.isin(EXPECTED_TIMES)].sort_values(["symbol6", "clock"])
    if data.duplicated(["symbol6", "clock"]).any():
        raise ValueError(f"Duplicate required minute bars on {day}")
    data["shares"] = data.volume * volume_multiplier
    data["cum_shares"] = data.groupby("symbol6").shares.cumsum()
    data["cum_amount"] = data.groupby("symbol6").amount.cumsum()
    valid = data.close.gt(0) & data.shares.ge(0) & data.amount.ge(0) & data.cum_shares.gt(0)
    data["valid"] = valid
    data["bad"] = data.close.le(0) | data.shares.lt(0) | data.amount.lt(0)
    data["above"] = valid & (data.close * data.cum_shares > data.cum_amount)
    if "low" in data:
        data["low_above"] = valid & (data.low * data.cum_shares > data.cum_amount)
    # The expression above compares prices without rounding VWAP to cents.
    result = data.groupby("symbol6").agg(
        minute_bars=("clock", "size"), first_clock=("clock", "min"),
        last_clock=("clock", "max"), bad_bars=("bad", "sum"),
        total_shares=("cum_shares", "max"),
        vwap_above_minutes=("above", "sum")).reset_index()
    if "low" in data:
        lows = data.groupby("symbol6").low_above.sum().rename("low_above_minutes")
        result = result.merge(lows, on="symbol6", validate="one_to_one")
    result = result[result.minute_bars.eq(220) & result.bad_bars.eq(0) &
                    result.total_shares.gt(0) &
                    result.first_clock.eq("09:31:00") & result.last_clock.eq("14:40:00")].copy()
    result["vwap_above_ratio"] = result.vwap_above_minutes / 220
    result["vwap_all_above"] = result.vwap_above_minutes.eq(220).astype(int)
    result["vwap_level"] = result.vwap_above_ratio.map(feature_level).astype(int)
    result["signal_date"] = day
    if "low_above_minutes" in result:
        result["low_above_ratio"] = result.low_above_minutes / 220
        result["low_vwap_level"] = result.low_above_ratio.map(feature_level).astype(int)
        return result[["signal_date", "symbol6", "vwap_above_minutes",
                       "vwap_above_ratio", "vwap_all_above", "vwap_level",
                       "low_above_minutes", "low_above_ratio", "low_vwap_level"]]
    return result[["signal_date", "symbol6", "vwap_above_minutes",
                   "vwap_above_ratio", "vwap_all_above", "vwap_level"]]


def mk_day(day: str, codes: list[str]) -> pd.DataFrame:
    path = day_file(MK, day)
    if path.open("rb").read(7) == b"version":
        raise ValueError(f"Git LFS file is still a pointer: {path}")
    parts = []
    for chunk in pd.read_csv(path, usecols=["time", "symbol", "low", "close", "volume", "turnover"],
                             dtype={"symbol": str}, chunksize=300_000):
        part = chunk[chunk.symbol.str[:6].isin(codes)].copy()
        if not part.empty:
            parts.append(part)
    if not parts:
        return summarize_bars(pd.DataFrame(), day, 1)
    bars = pd.concat(parts, ignore_index=True)
    bars["symbol6"] = bars.symbol.str[:6]
    bars["clock"] = bars.time.str[-8:]
    return summarize_bars(bars.rename(columns={"turnover": "amount"}), day, 1)


def db_day(db, day: str, codes: list[str]) -> pd.DataFrame:
    # Database volume is in lots of 100 shares; amount is in yuan.
    placeholders = ",".join(["%s"] * len(codes))
    sql = ("WITH b AS (SELECT stk_code AS symbol6,bar_end_time,low_price,latest_price,volume,amount,"
           "is_finalized,is_fallback,source_snapshot_time,"
           "SUM(volume) OVER(PARTITION BY stk_code ORDER BY bar_end_time) AS cum_lots,"
           "SUM(amount) OVER(PARTITION BY stk_code ORDER BY bar_end_time) AS cum_amount "
           "FROM aiia_stock_realtime_minute_snapshot_full "
           "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
           "AND bar_end_time BETWEEN %s AND %s "
           f"AND stk_code IN ({placeholders})) "
           "SELECT symbol6,COUNT(*) AS minute_bars,COUNT(DISTINCT bar_end_time) AS distinct_bars,"
           "MIN(TIME(bar_end_time)) AS first_clock,MAX(TIME(bar_end_time)) AS last_clock,"
           "SUM(CASE WHEN latest_price*cum_lots*100>cum_amount THEN 1 ELSE 0 END) AS vwap_above_minutes,"
           "SUM(CASE WHEN low_price*cum_lots*100>cum_amount THEN 1 ELSE 0 END) AS low_above_minutes,"
           "MAX(cum_lots) AS total_lots,"
           "SUM(CASE WHEN latest_price<=0 OR low_price<=0 OR volume<0 OR amount<0 "
           "OR is_finalized<>1 OR is_fallback<>0 OR source_snapshot_time<>bar_end_time "
           "THEN 1 ELSE 0 END) AS bad_bars "
           "FROM b GROUP BY symbol6")
    frame = query(db, sql, (day, f"{day} 09:31:00", f"{day} 14:40:00", *codes))
    if frame.empty:
        return summarize_bars(pd.DataFrame(), day, 100)
    frame["symbol6"] = frame.symbol6.astype(str).str.zfill(6)
    for col in ("minute_bars", "distinct_bars", "vwap_above_minutes", "low_above_minutes",
                "bad_bars", "total_lots"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    keep = (frame.minute_bars.eq(220) & frame.distinct_bars.eq(220) &
            frame.first_clock.astype(str).str[-8:].eq("09:31:00") &
            frame.last_clock.astype(str).str[-8:].eq("14:40:00") &
            frame.bad_bars.eq(0) & frame.total_lots.gt(0))
    frame = frame[keep].copy()
    frame["vwap_above_ratio"] = frame.vwap_above_minutes / 220
    frame["vwap_all_above"] = frame.vwap_above_minutes.eq(220).astype(int)
    frame["vwap_level"] = frame.vwap_above_ratio.map(feature_level).astype(int)
    frame["low_above_ratio"] = frame.low_above_minutes / 220
    frame["low_vwap_level"] = frame.low_above_ratio.map(feature_level).astype(int)
    frame["signal_date"] = day
    return frame[["signal_date", "symbol6", "vwap_above_minutes",
                  "vwap_above_ratio", "vwap_all_above", "vwap_level",
                  "low_above_minutes", "low_above_ratio", "low_vwap_level"]]


def load_cohorts() -> tuple[pd.DataFrame, pd.DataFrame]:
    early = pd.read_csv(MK_CANDIDATES, dtype={"symbol6": str, "signal_date": str,
                                                   "next_date": str}, low_memory=False)
    with db_connection(Path(".env")) as db:
        intervals = load_st_intervals(db)
    early = make_cohorts(early, intervals)[0][POLICIES[1]]
    late = pd.read_csv(LATE_CANDIDATES, dtype={"symbol6": str, "signal_date": str,
                                                 "next_date": str})
    mature = pd.read_csv(OCT12_LABELS, dtype={"symbol6": str, "signal_date": str},
                         usecols=["signal_date", "symbol6", "second_high", "close_40"])
    mature = mature[mature.signal_date.eq("2026-10-09")]
    if (len(mature) != 471 or mature[["second_high", "close_40"]].isna().any().any() or
            set(late.loc[late.signal_date.eq("2026-10-09"), "symbol6"]) != set(mature.symbol6)):
        raise ValueError("10/12 morning outcome snapshot does not match frozen 10/9 cohort")
    late = late.merge(mature, on=["signal_date", "symbol6"], how="left",
                      suffixes=("", "_mature"), validate="one_to_one")
    for col in ("second_high", "close_40"):
        late[col] = late[col].fillna(late.pop(f"{col}_mature"))
    if early[list(FEATURES)].isna().any().any() or late[list(FEATURES)].isna().any().any():
        raise ValueError("Missing baseline seven-factor feature")
    return early, late


def extract(source: str, output: Path, start: str | None, stop: str | None) -> None:
    if source == "mk":
        rows = load_cohorts()[0]
    else:
        rows = pd.read_csv(LATE_CANDIDATES,
                           dtype={"symbol6": str, "signal_date": str, "next_date": str})
    days = sorted(x for x in rows.signal_date.unique()
                  if (start is None or x >= start) and (stop is None or x <= stop))
    output.mkdir(parents=True, exist_ok=True)
    folder = output / "factor_days"
    folder.mkdir(exist_ok=True)
    if source == "db":
        with db_connection(Path(".env")) as db:
            for day in days:
                path = folder / f"{day}.csv"
                existing = pd.read_csv(path, dtype={"symbol6": str}) if path.exists() else None
                all_codes = set(rows.loc[rows.signal_date.eq(day), "symbol6"].unique())
                codes = sorted(all_codes - (set(existing.symbol6) if existing is not None else set()))
                if codes:
                    parts = [db_day(db, day, codes[i:i+100])
                             for i in range(0, len(codes), 100)]
                    combined = pd.concat(([existing] if existing is not None else []) + parts,
                                         ignore_index=True)
                    combined.to_csv(path, index=False)
                actual = pd.read_csv(path, dtype={"symbol6": str})
                print(source, day, "factor rows", len(actual), "missing", len(all_codes-set(actual.symbol6)), flush=True)
    else:
        for day in days:
            path = folder / f"{day}.csv"
            existing = pd.read_csv(path, dtype={"symbol6": str}) if path.exists() else None
            all_codes = set(rows.loc[rows.signal_date.eq(day), "symbol6"].unique())
            codes = sorted(all_codes - (set(existing.symbol6) if existing is not None else set()))
            if codes:
                extra = mk_day(day, codes)
                combined = pd.concat(([existing] if existing is not None else [])+[extra],
                                     ignore_index=True)
                combined.to_csv(path, index=False)
            actual = pd.read_csv(path, dtype={"symbol6": str})
            print(source, day, "factor rows", len(actual), "missing", len(all_codes-set(actual.symbol6)), flush=True)


def factor_table(output: Path) -> pd.DataFrame:
    files = sorted((output / "factor_days").glob("2026-*.csv"))
    if not files:
        raise ValueError("No VWAP factor snapshots")
    factor = pd.concat([pd.read_csv(path, dtype={"symbol6": str, "signal_date": str})
                        for path in files], ignore_index=True)
    if factor.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate VWAP factor key")
    return factor


def replay_one(cohort: pd.DataFrame, calendar: list[str], segment: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    picks, folds = [], []
    cohort = cohort.copy()
    labeled = set(cohort.loc[cohort.second_high.notna(), "signal_date"])
    for i in range(20, len(calendar)):
        day = calendar[i]
        if segment == "feb_jul" and day == "2026-07-31":
            continue
        previous = calendar[i-20:i]
        if not set(previous).issubset(labeled):
            continue
        train = cohort[cohort.signal_date.isin(previous) & cohort.second_high.notna() &
                       cohort.next_date.le(day)]
        current = cohort[cohort.signal_date.eq(day)]
        if current.empty:
            continue
        if train.signal_date.nunique() != 20:
            raise ValueError(f"Immature training labels at {day}")
        fold = {"segment": segment, "signal_date": day, "train_rows": len(train),
                "candidates": len(current), "train_start": previous[0], "train_end": previous[-1]}
        folds.append(fold)
        train_labels = four_class(train.second_high / train.signal_price - 1).astype(str)
        for arm, features in (("baseline7", FEATURES), ("vwap8", (*FEATURES, "vwap_level"))):
            model = models()["small_boosted_tree"]
            model.fit(train[list(features)], train_labels)
            probabilities = model.predict_proba(current[list(features)])
            scored = current[["signal_date", "next_date", "symbol6", "name", "signal_price",
                              "vwap_above_ratio", "vwap_all_above", "vwap_level"]].copy()
            for label in ("lt0", "0to1", "1to3", "ge3"):
                scored[f"p_{label}"] = probabilities[:, list(model.classes_).index(label)]
            scored["predicted_class"] = np.asarray(model.classes_)[probabilities.argmax(axis=1)]
            top = select_top(scored)
            top = top[top.selection_rank.eq(1)].copy()
            top = top.merge(current[["signal_date", "symbol6", "second_high", "close_40"]],
                            on=["signal_date", "symbol6"], validate="one_to_one")
            top["actual_second_high_return"] = top.second_high / top.signal_price - 1
            top["actual_0940_return"] = top.close_40 / top.signal_price - 1
            top["actual_class"] = np.where(top.second_high.notna(),
                                            four_class(top.actual_second_high_return).astype(str),
                                            "UNKNOWN")
            top["arm"] = arm
            top["segment"] = segment
            picks.append(top)
        print(segment, day, "train", len(train), "candidates", len(current), flush=True)
    return pd.concat(picks, ignore_index=True), pd.DataFrame(folds)


def replay_early(output: Path) -> None:
    early = load_cohorts()[0]
    factor = factor_table(output)
    early = early.merge(factor, on=["signal_date", "symbol6"], how="left",
                        validate="one_to_one")
    if early.vwap_level.isna().any():
        raise ValueError("1–7 month VWAP factors are incomplete")
    picks, folds = replay_one(early, sorted(early.signal_date.unique()), "feb_jul")
    picks.to_csv(output / "paired_feb_jul.csv", index=False)
    folds.to_csv(output / "folds_feb_jul.csv", index=False)


def run(output: Path) -> None:
    early, late = load_cohorts()
    factor = factor_table(output)
    combined = pd.concat([early.assign(segment="feb_jul"), late.assign(segment="sep_oct")],
                         ignore_index=True)
    covered = combined.merge(factor, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    (covered.groupby("segment").agg(total=("symbol6", "size"),
                                       complete=("vwap_level", "count"))
     .reset_index().to_csv(output / "coverage.csv", index=False))
    covered["actual_second_high_return"] = covered.second_high / covered.signal_price - 1
    covered["actual_0940_return"] = covered.close_40 / covered.signal_price - 1
    (covered.groupby(["segment", "vwap_level"], dropna=False)
     .agg(candidates=("symbol6", "size"), labeled=("second_high", "count"),
          second_high_ge3=("actual_second_high_return", lambda s: int(s.ge(.03).sum())),
          sale_known=("close_40", "count"),
          sale_ge3=("actual_0940_return", lambda s: int(s.ge(.03).sum())),
          sale_negative=("actual_0940_return", lambda s: int(s.lt(0).sum())),
          mean_sale_return=("actual_0940_return", "mean"))
     .reset_index().to_csv(output / "candidate_level_outcomes.csv", index=False))
    # Both arms see precisely the same stocks; incomplete current-day factor rows are excluded.
    complete = covered[covered.vwap_level.notna()].copy()
    complete.vwap_level = complete.vwap_level.astype(int)
    early_days = sorted(early.signal_date.unique())
    late_days = pd.read_csv(LATE_MARKET).trade_date.astype(str).tolist()
    arms = []
    folds = []
    for segment, days in (("feb_jul", early_days), ("sep_oct", late_days)):
        pick_cache, fold_cache = (output / f"paired_{segment}.csv",
                                  output / f"folds_{segment}.csv")
        if pick_cache.exists() and fold_cache.exists():
            x = pd.read_csv(pick_cache, dtype={"symbol6": str, "signal_date": str})
            f = pd.read_csv(fold_cache, dtype={"signal_date": str})
        else:
            x, f = replay_one(complete[complete.segment.eq(segment)], days, segment)
            x.to_csv(pick_cache, index=False)
            f.to_csv(fold_cache, index=False)
        arms.append(x)
        folds.append(f)
    picks = pd.concat(arms, ignore_index=True)
    picks.to_csv(output / "paired_top1.csv", index=False)
    pd.concat(folds).to_csv(output / "folds.csv", index=False)
    picks["sale_class"] = four_class(picks.actual_0940_return).astype(str)
    (pd.crosstab([picks.segment, picks.arm], picks.actual_class)
     .reset_index().to_csv(output / "paired_target_class_counts.csv", index=False))
    (pd.crosstab([picks.segment, picks.arm], picks.sale_class)
     .reset_index().to_csv(output / "paired_sale_class_counts.csv", index=False))
    picks["month"] = picks.signal_date.str[:7]
    (picks.groupby(["month", "arm"]).agg(
        picks=("symbol6", "size"), target_ge3=("actual_class", lambda s: int(s.eq("ge3").sum())),
        sale_ge3=("actual_0940_return", lambda s: int(s.ge(.03).sum())),
        sale_negative=("actual_0940_return", lambda s: int(s.lt(0).sum())),
        mean_sale_return=("actual_0940_return", "mean"))
     .reset_index().to_csv(output / "paired_monthly.csv", index=False))
    fixed = pd.concat([pd.read_csv(EARLY_TOP1, dtype={"symbol6": str}),
                       pd.read_csv(LATE_TOP2, dtype={"symbol6": str}).query("selection_rank == 1")],
                      ignore_index=True)
    fixed = fixed.merge(late.loc[late.signal_date.eq("2026-10-09"),
                                  ["signal_date", "symbol6", "second_high", "close_40"]],
                        on=["signal_date", "symbol6"], how="left", suffixes=("", "_mature"),
                        validate="one_to_one")
    for col in ("second_high", "close_40"):
        fixed[col] = fixed[col].fillna(fixed.pop(f"{col}_mature"))
    fixed["actual_0940_return"] = fixed.close_40 / fixed.signal_price - 1
    fixed["actual_second_high_return"] = fixed.second_high / fixed.signal_price - 1
    fixed = fixed.merge(factor, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    fixed.to_csv(output / "existing_top1_vwap.csv", index=False)
    daily = pd.read_csv(DAILY, dtype={"Top1代码": str})
    daily["signal_date"] = pd.to_datetime(daily["选股日"]).dt.strftime("%Y-%m-%d")
    daily["symbol6"] = daily["Top1代码"].str.zfill(6)
    daily = daily.merge(factor, on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    daily["分时VWAP全程在上方（0/1）"] = daily.vwap_all_above.astype("Int64")
    daily["分时VWAP上方分钟占比"] = daily.vwap_above_ratio
    daily["分时VWAP档位（0/1/2）"] = daily.vwap_level.astype("Int64")
    daily["数据来源"] = "历史回测"
    live_file = output / "live_2026-10-12_top2_vwap.csv"
    if live_file.exists():
        live = pd.read_csv(live_file, dtype={"symbol6": str})
        top = live[live.selection_rank.eq(1)]
        if len(top) == 1:
            item = top.iloc[0]
            live_row = {"选股日": "2026/10/12", "Top1代码": item.symbol6,
                        "Top1股票": item["name"], "Top1次晨类别": "待验证",
                        "signal_date": "2026-10-12", "symbol6": item.symbol6,
                        "vwap_above_minutes": item.vwap_above_minutes,
                        "vwap_above_ratio": item.vwap_above_ratio,
                        "vwap_all_above": item.vwap_all_above,
                        "vwap_level": item.vwap_level,
                        "分时VWAP全程在上方（0/1）": item.vwap_all_above,
                        "分时VWAP上方分钟占比": item.vwap_above_ratio,
                        "分时VWAP档位（0/1/2）": item.vwap_level,
                        "数据来源": "今日实时选股，次晨待验证"}
            daily = pd.concat([daily, pd.DataFrame([live_row])], ignore_index=True)
    low_file = output / "old_top1_low_vwap.csv"
    if low_file.exists():
        low = pd.read_csv(low_file, dtype={"signal_date": str, "symbol6": str})
        live_low_file = output / "live_2026-10-12_top2_low_vwap.csv"
        if live_low_file.exists():
            live_low = pd.read_csv(live_low_file,
                                   dtype={"signal_date": str, "symbol6": str})
            low = pd.concat([low, live_low[live_low.selection_rank.eq(1)]], ignore_index=True)
        low = low[["signal_date", "symbol6", "low_above_minutes",
                   "low_above_ratio", "low_all_above"]]
        daily = daily.merge(low, on=["signal_date", "symbol6"], how="left",
                            validate="one_to_one")
        daily["分钟最低价高于当时VWAP占比"] = daily.low_above_ratio
        daily["分钟最低价全程高于当时VWAP（0/1）"] = daily.low_all_above.astype("Int64")
    daily.to_csv(output / "daily_with_vwap.csv", index=False)
    fixed["month"] = fixed.signal_date.str[:7]
    (fixed.groupby(["month", "vwap_all_above"], dropna=False)
     .agg(picks=("symbol6", "size"), known=("actual_0940_return", "count"),
          ge3_0940=("actual_0940_return", lambda s: int(s.ge(.03).sum())),
          negative_0940=("actual_0940_return", lambda s: int(s.lt(0).sum())),
          mean_0940=("actual_0940_return", "mean"))
     .reset_index().to_csv(output / "existing_top1_by_month.csv", index=False))
    summary = {"factor_coverage": pd.read_csv(output / "coverage.csv").to_dict("records"),
               "existing_top1": [], "paired": [], "paired_difference": [],
               "archived_baseline_reproduction": []}
    old = fixed[["signal_date", "symbol6", "p_ge3"]].rename(
        columns={"symbol6": "archived_symbol6", "p_ge3": "archived_p_ge3"})
    current = picks[picks.arm.eq("baseline7")][["segment", "signal_date", "symbol6", "p_ge3"]].rename(
        columns={"symbol6": "recomputed_symbol6", "p_ge3": "recomputed_p_ge3"})
    match = current.merge(old, on="signal_date", how="outer", validate="one_to_one")
    inferred_segment = pd.Series(np.where(match.signal_date.le("2026-07-31"),
                                          "feb_jul", "sep_oct"), index=match.index)
    match["segment"] = match.segment.where(match.segment.notna(), inferred_segment)
    match["same_top1"] = match.recomputed_symbol6.eq(match.archived_symbol6)
    match.to_csv(output / "baseline_reproduction.csv", index=False)
    for segment, group in match.groupby("segment"):
        common = group[group.recomputed_symbol6.notna() & group.archived_symbol6.notna()]
        same = common[common.same_top1]
        summary["archived_baseline_reproduction"].append({
            "segment": segment, "archived_picks": int(group.archived_symbol6.notna().sum()),
            "recomputed_picks": int(group.recomputed_symbol6.notna().sum()),
            "common_days": len(common), "same_top1": len(same),
            "same_stock_median_abs_p_ge3_difference": float(
                (same.recomputed_p_ge3-same.archived_p_ge3).abs().median()) if len(same) else None})
    for flag, group in fixed.groupby("vwap_all_above", dropna=False):
        known = group[group.actual_0940_return.notna()]
        summary["existing_top1"].append({"all_above": None if pd.isna(flag) else int(flag),
                                          "picks": len(group), "known": len(known),
                                          "ge3_0940": int(known.actual_0940_return.ge(.03).sum()),
                                          "ge1_0940": int(known.actual_0940_return.ge(.01).sum()),
                                          "negative_0940": int(known.actual_0940_return.lt(0).sum()),
                                          "mean_0940_pct": float(known.actual_0940_return.mean()*100)})
    observed = fixed[fixed.vwap_all_above.notna() & fixed.actual_0940_return.notna()]
    a = observed[observed.vwap_all_above.eq(1)]
    b = observed[observed.vwap_all_above.eq(0)]
    if len(a) and len(b):
        summary["existing_top1_fisher_ge3_p"] = float(fisher_exact(
            [[int(a.actual_0940_return.ge(.03).sum()), int(a.actual_0940_return.lt(.03).sum())],
             [int(b.actual_0940_return.ge(.03).sum()), int(b.actual_0940_return.lt(.03).sum())]]).pvalue)
    for (segment, arm), group in picks.groupby(["segment", "arm"]):
        known = group[group.actual_0940_return.notna()]
        summary["paired"].append({"segment": segment, "arm": arm,
                                   "picks": len(group), "known": len(known),
                                   "ge3_second_high": int(group.actual_class.eq("ge3").sum()),
                                   "ge3_0940": int(known.actual_0940_return.ge(.03).sum()),
                                   "ge1_0940": int(known.actual_0940_return.ge(.01).sum()),
                                   "negative_0940": int(known.actual_0940_return.lt(0).sum()),
                                   "mean_0940_pct": float(known.actual_0940_return.mean()*100)})
    for segment, group in picks.groupby("segment"):
        wide = group.pivot(index="signal_date", columns="arm",
                           values=["symbol6", "actual_0940_return"]).dropna()
        delta = (wide[("actual_0940_return", "vwap8")].astype(float) -
                 wide[("actual_0940_return", "baseline7")].astype(float))
        summary["paired_difference"].append({
            "segment": segment, "common_known_days": len(delta),
            "changed_top1_days": int(wide[("symbol6", "vwap8")].ne(
                wide[("symbol6", "baseline7")]).sum()),
            "vwap8_better_days": int(delta.gt(0).sum()),
            "vwap8_worse_days": int(delta.lt(0).sum()),
            "same_return_days": int(delta.eq(0).sum()),
            "mean_delta_percentage_points": float(delta.mean()*100)})
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    sources = (MK_CANDIDATES, LATE_CANDIDATES, OCT12_LABELS,
               Path("docs/stock_automl_runs/20261011_mk_1m_compact/compressed_manifest.json"),
               Path(__file__), Path("scripts/analyze_automl_top1_low_vwap.py"),
               Path("scripts/backtest_automl_intraday_vwap_gate.py"))
    manifest = {"git_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(),
        "python_packages": {"pandas": pd.__version__, "numpy": np.__version__,
                            "scikit_learn": sklearn.__version__},
        "sources_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sources},
        "model_parameters": models()["small_boosted_tree"].get_params(),
        "vwap_formula": "cumulative amount / cumulative shares, 09:31 through 14:40",
        "db_volume_unit": "100 shares per lot", "archive_volume_unit": "shares"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract-mk", "extract-db", "replay-early", "run"))
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--start")
    parser.add_argument("--stop")
    args = parser.parse_args()
    if args.command == "run":
        run(args.output)
    elif args.command == "replay-early":
        replay_early(args.output)
    else:
        extract(args.command.split("-")[1], args.output, args.start, args.stop)


if __name__ == "__main__":
    main()
