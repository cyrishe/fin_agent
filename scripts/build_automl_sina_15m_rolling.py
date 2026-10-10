"""Build one 14:45 / next-09:45 close cohort from cached 15-minute bars."""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_st_status import ELIGIBLE_TYPES, attach_st_status, load_st_intervals
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection
from scripts.build_automl_sina_15m_july import THROUGH_1445
from scripts.build_automl_tail_standard import load_daily, prior_profile
from scripts.experiment_automl_1450_grid import board_limit
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class


START, LAST_SIGNAL, END = "2026-06-01", "2026-08-10", "2026-08-11"


def at_nominal_five_limit(reference: float, signal: float) -> bool:
    price = float((Decimal(str(reference)) * Decimal("1.05")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))
    return abs(signal - price) <= .001


def compute(cache: Path, env_file: Path, output: Path,
            exclude_nominal_five_limit: bool = False,
            exclude_flat_five_limit: bool = False,
            exclude_historical_st: bool = False) -> dict:
    with db_connection(env_file) as db:
        daily, reported_value = load_daily(db, history_start="2026-04-01", end=END)
        with db.cursor() as cursor:
            cursor.execute("""SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,
                       turn_ratio FROM kcrp_stock_price
                       WHERE trade_date BETWEEN %s AND %s""", ("2026-04-01", END))
            turnover = pd.DataFrame(cursor.fetchall())
            cursor.execute("""SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,
                       preclose FROM kcrp_stock_price
                       WHERE trade_date BETWEEN %s AND %s AND preclose>0""",
                           (START, LAST_SIGNAL))
            signal_days = pd.DataFrame(cursor.fetchall())
            cursor.execute("SELECT LEFT(stk_code,6) AS symbol6,stk_name AS name "
                           "FROM kcrp_stock_baseinfo")
            names = {row["symbol6"]: row["name"] or "" for row in cursor.fetchall()}
        st_intervals = load_st_intervals(db) if exclude_historical_st else None
    turnover["date"] = pd.to_datetime(turnover.date)
    turnover["symbol6"] = turnover.symbol6.astype(str).str.zfill(6)
    turnover["turn_ratio"] = pd.to_numeric(turnover.turn_ratio, errors="coerce")
    derived = daily[["date", "symbol6", "close", "volume"]].merge(
        turnover, on=["date", "symbol6"], validate="one_to_one")
    # Daily turnover is percentage of tradable shares. Use the same T-1
    # derivation in every month so June's sparse valuation table cannot skew
    # the training universe or cause a source-method jump on July 1.
    derived["float_share"] = derived.volume / (derived.turn_ratio / 100)
    derived["float_mv"] = derived.float_share * derived.close
    valid = (derived.turn_ratio.gt(0) & derived.volume.gt(0) &
             derived.close.gt(0) & np.isfinite(derived.float_share) &
             np.isfinite(derived.float_mv))
    value = derived.loc[valid, ["date", "symbol6", "float_share", "float_mv"]]
    profile, calendar = prior_profile(daily, value)
    audit = derived[["date", "symbol6", "float_mv"]].merge(
        reported_value[["date", "symbol6", "float_mv"]],
        on=["date", "symbol6"], how="inner", suffixes=("_derived", "_reported"),
        validate="one_to_one")
    audit = audit[audit.float_mv_reported.gt(0) & audit.float_mv_derived.gt(0)]
    ratio = audit.float_mv_derived / audit.float_mv_reported
    value_audit = {
        "overlap_rows": len(audit),
        "median_proxy_over_reported": float(ratio.median()),
        "within_1pct": float(ratio.between(.99, 1.01).mean()),
        "within_5pct": float(ratio.between(.95, 1.05).mean()),
        "abs_relative_error_p95": float((ratio-1).abs().quantile(.95)),
    }
    value_audit["by_month"] = {}
    for month in ("2026-06", "2026-07", "2026-08"):
        monthly = audit[audit.date.dt.strftime("%Y-%m").eq(month)]
        monthly_ratio = monthly.float_mv_derived / monthly.float_mv_reported
        month_rows = derived.date.dt.strftime("%Y-%m").eq(month)
        value_audit["by_month"][month] = {
            "daily_stock_rows": int(month_rows.sum()),
            "valid_proxy_rows": int((month_rows & valid).sum()),
            "reported_overlap_rows": len(monthly),
            "within_5pct_on_overlap": float(
                monthly_ratio.between(.95, 1.05).mean()),
        }
    next_date = {str(a)[:10]: str(b)[:10] for a, b in zip(calendar[:-1], calendar[1:])}
    wanted: dict[str, set[str]] = defaultdict(set)
    preclose = {}
    for row in signal_days.itertuples():
        day = str(row.date)[:10]
        wanted[row.symbol6].add(day)
        preclose[(day, row.symbol6)] = float(row.preclose)
    records = []
    status = Counter()
    for symbol, dates in wanted.items():
        file = cache / f"{symbol}.json.gz"
        if not file.exists():
            status["stock_cache_missing"] += 1
            continue
        with gzip.open(file, "rt", encoding="utf8") as source:
            bars = json.load(source)
        by_day: dict[str, dict[str, dict]] = defaultdict(dict)
        for bar in bars:
            stamp = str(bar.get("day", ""))
            if len(stamp) >= 16:
                by_day[stamp[:10]][stamp[11:16]] = bar
        for day in dates:
            status["source_stock_days"] += 1
            current = by_day.get(day, {})
            if not set(THROUGH_1445).issubset(current):
                status["signal_15m_incomplete"] += 1
                continue
            tomorrow = next_date.get(day)
            if tomorrow is None:
                status["next_trading_date_missing"] += 1
                continue
            try:
                signal = float(current["14:45"]["close"])
                reference = preclose[(day, symbol)]
                opens = [float(current[t]["open"]) for t in THROUGH_1445]
                highs = [float(current[t]["high"]) for t in THROUGH_1445]
                lows = [float(current[t]["low"]) for t in THROUGH_1445]
                closes = [float(current[t]["close"]) for t in THROUGH_1445]
                volumes = [float(current[t]["volume"]) for t in THROUGH_1445]
            except (KeyError, TypeError, ValueError, OverflowError):
                status["invalid_signal_value"] += 1
                continue
            if (min([signal, reference, *opens, *lows, *closes]) <= 0 or
                    min(volumes) < 0 or
                    any(high < max(opn, close) or low > min(opn, close)
                        for opn, high, low, close in zip(opens, highs, lows, closes))):
                status["invalid_signal_bar"] += 1
                continue
            gain = signal / reference - 1
            if not .03 <= gain <= .06:
                status["outside_3to6_1445"] += 1
                continue
            status["raw_3to6_1445"] += 1
            at_five = at_nominal_five_limit(reference, signal)
            if at_five and exclude_nominal_five_limit:
                status["exact_nominal_5pct_price_excluded"] += 1
                continue
            if at_five and exclude_flat_five_limit and max(
                    opens[-1], highs[-1], lows[-1], closes[-1]) - min(
                    opens[-1], highs[-1], lows[-1], closes[-1]) <= .001:
                status["flat_nominal_5pct_bar_excluded"] += 1
                continue
            morning = by_day.get(tomorrow, {})
            record = {"signal_date": day, "next_date": tomorrow,
                      "symbol6": symbol, "name": names.get(symbol, ""),
                      "t_reference_preclose": reference, "signal_price": signal,
                      "signal_return": gain, "minute_volume_shares": sum(volumes),
                      "min_low_so_far": min(lows), "bars_signal": len(THROUGH_1445),
                      "bars_next": int("09:45" in morning)}
            for field, bar in (("close_1500", current.get("15:00")),
                               ("close_0945", morning.get("09:45")),
                               ("close_1000", morning.get("10:00")),
                               ("close_1015", morning.get("10:15"))):
                try:
                    price = float(bar["close"]) if bar is not None else np.nan
                    record[field] = price if price > 0 else np.nan
                except (KeyError, TypeError, ValueError, OverflowError):
                    record[field] = np.nan
            try:
                record["high_0945"] = float(morning["09:45"]["high"])
            except (KeyError, TypeError, ValueError, OverflowError):
                record["high_0945"] = np.nan
            records.append(record)
    if not records:
        raise ValueError("No 14:45 candidate rows")
    candidates = pd.DataFrame(records)
    candidates["signal_date"] = pd.to_datetime(candidates.signal_date)
    candidates["next_date"] = pd.to_datetime(candidates.next_date)
    if exclude_historical_st:
        candidates = attach_st_status(candidates, st_intervals)
        status["st_status_missing"] = int(candidates.st_type.isna().sum())
        status["historical_risk_excluded"] = int(
            (~candidates.st_type.isin(ELIGIBLE_TYPES)).sum())
        for st_type, count in candidates.st_type.value_counts(dropna=False).items():
            status[f"st_type_{st_type}"] = int(count)
        candidates = candidates[candidates.st_type.isin(ELIGIBLE_TYPES)].copy()
    candidates = candidates.merge(
        profile[profile.signal_date.between(START, LAST_SIGNAL)],
        on=["signal_date", "symbol6"], how="left", validate="one_to_one")
    candidates["volume_ratio"] = (candidates.minute_volume_shares * 240 /
                                   (225 * candidates.avg_volume5_shares))
    candidates["turnover_so_far_pct"] = candidates.minute_volume_shares / candidates.float_share * 100
    candidates["float_mv_100m_cny"] = candidates.float_mv / 1e8
    for period in (5, 10, 20):
        candidates[f"ma{period}"] = (candidates[f"ma{period}_adj"] *
                                     candidates.t_reference_preclose / candidates.prior_adjclose)
    highest_ma = candidates[["ma5", "ma10", "ma20"]].max(axis=1)
    candidates["ma_bull_5_10_20"] = (candidates.ma5.gt(candidates.ma10) &
                                      candidates.ma10.gt(candidates.ma20)).astype("Int8")
    candidates["all_intraday_lows_above_ma"] = (
        candidates.min_low_so_far.gt(highest_ma)).astype("Int8")
    candidates["target_0945_close_return"] = (
        candidates.close_0945 / candidates.signal_price - 1)
    candidates["target_0945_high_return"] = (
        candidates.high_0945 / candidates.signal_price - 1)
    candidates["limit_buffer"] = [board_limit(symbol, "") for symbol in candidates.symbol6]
    complete = pd.Series(True, index=candidates.index)
    filter_steps = {"raw_3to6": len(candidates)}
    for name, condition in (
        ("complete_tminus1_history", candidates.history_complete.eq(True)),
        ("no_recent_adjustment", candidates.adj_price_break_20d.eq(False)),
        ("no_t_day_reference_break",
         candidates.t_reference_preclose.div(candidates.prior_close).sub(1).abs().le(.01)),
        ("complete_tminus1_valuation", candidates.value_complete.eq(True)),
        ("not_near_limit", candidates.limit_buffer.notna() &
         candidates.signal_return.lt(candidates.limit_buffer)),
    ):
        complete &= condition.fillna(False)
        filter_steps[name] = int(complete.sum())
    complete &= candidates[list(FEATURES)].notna().all(axis=1)
    filter_steps["all_model_features_present"] = int(complete.sum())
    selected = candidates[complete].copy()
    for field in ("volume_4of5_increasing", "ma_bull_5_10_20",
                  "all_intraday_lows_above_ma"):
        selected[field] = selected[field].astype(int)
    if selected.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate candidate stock-day")
    has_label = selected.target_0945_close_return.notna()
    selected["class"] = pd.Series(pd.NA, index=selected.index, dtype="string")
    selected.loc[has_label, "class"] = four_class(
        selected.loc[has_label, "target_0945_close_return"].round(10))
    if not selected.loc[has_label, "class"].isin(CLASSES).all():
        raise ValueError("Invalid four-class label")
    output.mkdir(parents=True, exist_ok=True)
    selected.sort_values(["signal_date", "symbol6"]).to_csv(
        output / "trainable_15m_candidates.csv", index=False)
    daily_coverage = selected.groupby("signal_date").agg(
        candidate_stocks=("symbol6", "size"), labeled_stocks=("class", "count"),
        close_1000_stocks=("close_1000", "count"),
        close_1015_stocks=("close_1015", "count")).reset_index()
    daily_coverage.to_csv(output / "coverage_by_day.csv", index=False)
    summary = {
        "source": "Sina unadjusted 15m bars; prior daily and turnover from read-only KingdomAI",
        "float_market_value": "T-1 daily volume / (T-1 daily turnover percentage / 100) * T-1 close, same proxy in every month",
        "float_market_value_audit": value_audit,
        "signal": "14:45 completed 15m close, 3-6% over previous close",
        "exclude_nominal_five_limit": exclude_nominal_five_limit,
        "exclude_flat_five_limit": exclude_flat_five_limit,
        "exclude_historical_st": exclude_historical_st,
        "historical_st_rule": ("kcrp_stock_st interval active on signal date "
                               "[begin_date,end_date); keep N or R only; "
                               "exclude S/Y/L/T/Z or missing status" if
                               exclude_historical_st else None),
        "five_limit_rule": ("Exclude 14:45 close exactly equal (within CNY 0.001) to "
                            "ROUND_HALF_UP(previous close * 1.05, CNY 0.01); uses "
                            "information known by 14:45 and can exclude non-ST stocks." if
                            exclude_nominal_five_limit else None),
        "flat_five_limit_rule": ("Exclude only if the completed 14:45 bar has "
                                 "open, high, low, close all at the rounded "
                                 "nominal 5% upper price." if exclude_flat_five_limit else None),
        "target": "next trading day's 09:45 completed 15m close / 14:45 entry proxy - 1",
        "signals": int(selected.signal_date.nunique()),
        "first_signal_date": selected.signal_date.min().date().isoformat(),
        "last_signal_date": selected.signal_date.max().date().isoformat(),
        "raw_candidates": len(candidates), "complete_candidates": len(selected),
        "labeled_candidates": int(has_label.sum()),
        "source_symbols_expected": len(wanted),
        "cache_symbols": len(list(cache.glob("*.json.gz"))),
        "candidate_filter_steps": filter_steps, "status": dict(status),
    }
    (output / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=Path("outputs/stock_automl/sina_15m_rolling/cache"))
    parser.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/stock_automl/sina_15m_rolling"))
    parser.add_argument("--exclude-nominal-five-limit", action="store_true")
    parser.add_argument("--exclude-flat-five-limit", action="store_true")
    parser.add_argument("--exclude-historical-st", action="store_true")
    args = parser.parse_args()
    compute(args.cache, args.env_file, args.output,
            exclude_nominal_five_limit=args.exclude_nominal_five_limit,
            exclude_flat_five_limit=args.exclude_flat_five_limit,
            exclude_historical_st=args.exclude_historical_st)
