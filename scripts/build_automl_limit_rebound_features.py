"""Point-in-time cooling-day research panel for the R1 sealed-limit cohort.

One row is one stock at the close of one non-limit cooling day. Features and
future labels are written separately so a model cannot ingest labels by accident.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.mk_minute_archive import day_file


SOURCE_START = "2025-11-20"  # 5-day baselines before the first 2026 event.
SOURCE_END = "2026-10-13"
R1_EVENTS = Path("outputs/stock_automl/limit_rebound_20261013/streak_events.csv")
OUT = Path("outputs/stock_automl/limit_rebound_features_r1")
KEY = ["symbol", "first_limit_date", "streak_end_date", "asof_date", "cooling_day"]
NUMERIC = ("preclose", "open", "high", "low", "close", "volume", "amount",
           "turn_ratio", "is_limit_price")

# Single source for column meaning. Add a formula here when adding a feature below.
FEATURES = {
    "event": {
        "streak_length": "封板连续交易日数",
        "first_open_gap": "首板开盘/昨收-1",
        "first_intraday_return": "首板收盘/开盘-1",
        "first_range": "首板(最高-最低)/昨收",
        "first_upper_shadow": "首板(最高-max(开盘,收盘))/昨收",
        "first_lower_shadow": "首板(min(开盘,收盘)-最低)/昨收",
        "first_close_position": "首板(收盘-最低)/(最高-最低)；无振幅时缺失",
        "first_one_price_proxy": "首板日OHLC全相等且有成交；日线代理，不能证明盘中未开板",
        "first_volume_ratio_pre5": "首板成交量/首板前5个完整个股交易日均量",
        "first_amount_ratio_pre5": "首板成交额/首板前5个完整个股交易日均额",
        "first_turnover_pct": "首板日源表换手率，百分数",
        "pre5_return": "首板前5个完整交易日的(收盘/昨收)逐日复合收益-1",
        "pre5_mean_amount": "首板前5个完整个股交易日平均成交额，源表单位",
        "pre5_mean_turnover_pct": "首板前5个完整个股交易日平均换手率，百分数",
        "last_board_range": "连板末日(最高-最低)/昨收",
        "last_board_open_gap": "连板末日开盘/昨收-1",
        "last_board_volume": "连板末日成交量，源表单位",
        "last_board_amount": "连板末日成交额，源表单位",
        "last_board_turnover_pct": "连板末日换手率，百分数",
        "streak_return": "连板末日收盘/首板昨收-1",
        "streak_total_turnover_pct": "连板期间换手率之和，百分数",
        "streak_last_first_volume_ratio": "连板末日量/首板量",
    },
    "cooling_day": {
        "day_return": "冷静当日收盘/昨收-1",
        "day_open_gap": "冷静当日开盘/昨收-1",
        "day_intraday_return": "冷静当日收盘/开盘-1",
        "day_range": "冷静当日(最高-最低)/昨收",
        "day_upper_shadow": "冷静当日(最高-max(开盘,收盘))/昨收",
        "day_lower_shadow": "冷静当日(min(开盘,收盘)-最低)/昨收",
        "day_close_position": "冷静当日收盘在日内高低区间的位置；无振幅时缺失",
        "day_high_return": "冷静当日最高/昨收-1；接近涨停的连续数值代理",
        "day_low_return": "冷静当日最低/昨收-1",
        "day_close_from_streak_end": "冷静当日收盘/连板末日收盘-1",
        "day_high_from_streak_end": "冷静当日最高/连板末日收盘-1",
        "day_low_from_streak_end": "冷静当日最低/连板末日收盘-1",
        "day_close_from_first_board": "冷静当日收盘/首板收盘-1",
        "day_volume": "冷静当日成交量，源表单位",
        "day_amount": "冷静当日成交额，源表单位",
        "day_turnover_pct": "冷静当日源表换手率，百分数",
        "day_volume_ratio_last_board": "冷静当日量/连板末日量",
        "day_volume_ratio_pre5": "冷静当日量/首板前5日均量",
        "day_amount_ratio_pre5": "冷静当日额/首板前5日均额",
        "day_amount_ratio_last_board": "冷静当日额/连板末日额",
        "day_turnover_ratio_last_board": "冷静当日换手率/连板末日换手率",
        "day_turnover_ratio_pre5": "冷静当日换手率/首板前5日均换手率",
    },
    "cooling_path": {
        "path_cumulative_return": "截至当日收盘/连板末日收盘-1",
        "path_min_low_from_end": "截至当日最低价/连板末日收盘-1",
        "path_max_high_from_end": "截至当日最高价/连板末日收盘-1",
        "path_recovery_from_low": "截至当日收盘/冷静期至今最低价-1",
        "path_pullback_from_high": "截至当日收盘/冷静期至今最高价-1",
        "path_up_close_days": "冷静期至今收盘上涨的天数",
        "path_positive_body_days": "冷静期至今收盘高于开盘的天数",
        "path_volume_contract_days": "冷静日量低于上一交易日的天数；首日比连板末日",
        "path_total_turnover_pct": "冷静期至今换手率之和，百分数",
        "path_mean_range": "冷静期至今平均日振幅",
        "path_return_std": "冷静期至今每日收盘涨跌幅总体标准差；首日缺失",
        "path_last_first_volume_ratio": "当前冷静日量/首个冷静日量",
    },
    "market": {
        "market_limit_up_count": "决策日全市场封板涨停股票数",
        "market_prev_limit_up_count": "上一市场交易日全市场封板涨停股票数",
        "market_limit_up_count_change": "决策日封板数减上一交易日封板数",
        "market_ge7_count": "决策日全市场非封板且收盘涨幅至少7%的股票数",
        "market_up_fraction": "决策日全市场收盘上涨比例",
        "market_median_return": "决策日全市场收盘收益率中位数",
    },
    "first_board_minute": {
        "first_minute_complete": "首板日非复权分钟K完整240根且日收盘价匹配；1为可计算",
        "first_open_at_cap_proxy": "首根分钟开盘等于最终涨停价；非竞价挂单信息",
        "first_touch_minute": "首次分钟最高触及最终涨停价的交易分钟序号，1-240",
        "first_close_at_cap_minute": "首次分钟收盘等于最终涨停价的交易分钟序号，1-240",
        "first_last_reseal_minute": "最后一次由分钟收盘未封转为封板的序号",
        "first_reseal_count": "分钟收盘未封转为封板的次数；不等于逐笔炸板次数",
        "first_below_cap_after_touch_minutes": "首次触及所在分钟及以后，分钟最低价低于涨停价的分钟数；触及分钟内不判断先后",
        "first_close_at_cap_fraction": "全日分钟收盘在最终涨停价的比例",
        "first_amount_first_hour_fraction": "首小时成交额占全天比例；分钟成交额缺失时留空",
    },
}


def ratio(numerator, denominator):
    return float(numerator / denominator) if pd.notna(numerator) and pd.notna(denominator) and denominator > 0 else np.nan


def candle(row, prefix):
    return {
        f"{prefix}_open_gap": ratio(row.open, row.preclose) - 1,
        f"{prefix}_intraday_return": ratio(row.close, row.open) - 1,
        f"{prefix}_range": ratio(row.high - row.low, row.preclose),
        f"{prefix}_upper_shadow": ratio(row.high - max(row.open, row.close), row.preclose),
        f"{prefix}_lower_shadow": ratio(min(row.open, row.close) - row.low, row.preclose),
        f"{prefix}_close_position": ratio(row.close - row.low, row.high - row.low),
    }


def prepare_daily(daily: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "stk_code", *NUMERIC}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"Daily source missing columns: {sorted(missing)}")
    result = daily.copy()
    result["trade_date"] = pd.to_datetime(result.trade_date).dt.strftime("%Y-%m-%d")
    result["symbol"] = result.stk_code.astype(str).str[:6]
    result = result[result.symbol.str.startswith(("0", "3", "6", "9"))].copy()
    if result.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate stock-date rows")
    for col in NUMERIC:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["up"] = result.is_limit_price.eq(1) & result.close.gt(result.preclose)
    result["ret"] = result.close.div(result.preclose).sub(1)
    return result.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def minute_features(bars: pd.DataFrame, cap: float) -> dict:
    empty = {name: np.nan for name in FEATURES["first_board_minute"]}
    empty["first_minute_complete"] = 0
    required = {"time", "open", "high", "low", "close", "volume"}
    if bars is None or not required.issubset(bars.columns) or len(bars) != 240:
        return empty
    bars = bars.sort_values("time")
    clock = pd.to_datetime(bars.time).dt.strftime("%H:%M:%S")
    if (clock.nunique() != 240 or clock.iloc[0] != "09:31:00" or
            clock.iloc[-1] != "15:00:00" or
            not np.isclose(float(bars.close.iloc[-1]), cap, atol=.005, rtol=0)):
        return empty
    for col in required - {"time"}:
        if pd.to_numeric(bars[col], errors="coerce").isna().any():
            return empty
    if bars.volume.lt(0).any():
        return empty
    at_high = np.isclose(bars.high.to_numpy(), cap, atol=.005, rtol=0)
    at_close = np.isclose(bars.close.to_numpy(), cap, atol=.005, rtol=0)
    touch = int(np.flatnonzero(at_high)[0]) if at_high.any() else None
    seal = int(np.flatnonzero(at_close)[0]) if at_close.any() else None
    reseal = np.flatnonzero(at_close & ~np.r_[False, at_close[:-1]])
    first_hour_fraction = np.nan
    amount_name = "amount" if "amount" in bars else "turnover" if "turnover" in bars else None
    if amount_name and pd.to_numeric(bars[amount_name], errors="coerce").notna().all():
        amount = pd.to_numeric(bars[amount_name])
        first_hour_fraction = ratio(amount.iloc[:60].sum(), amount.sum())
    return {
        "first_minute_complete": 1,
        "first_open_at_cap_proxy": int(np.isclose(bars.open.iloc[0], cap, atol=.005, rtol=0)),
        "first_touch_minute": touch + 1 if touch is not None else np.nan,
        "first_close_at_cap_minute": seal + 1 if seal is not None else np.nan,
        "first_last_reseal_minute": int(reseal[-1]) + 1 if len(reseal) else np.nan,
        "first_reseal_count": len(reseal),
        "first_below_cap_after_touch_minutes": int((bars.low.iloc[touch:].to_numpy() < cap - .005).sum()) if touch is not None else np.nan,
        "first_close_at_cap_fraction": float(at_close.mean()),
        "first_amount_first_hour_fraction": first_hour_fraction,
    }


def load_first_board_minutes(root: Path, events: pd.DataFrame, daily: pd.DataFrame) -> dict:
    """Read only first-board stock-days from optional raw MK/none daily archives."""
    by_day = events.groupby("first_limit_date").symbol.apply(set).to_dict()
    caps = daily[["trade_date", "stk_code", "close"]].copy()
    caps["trade_date"] = pd.to_datetime(caps.trade_date).dt.strftime("%Y-%m-%d")
    caps["symbol"] = caps.stk_code.astype(str).str[:6]
    caps = caps[caps.trade_date.isin(by_day)].set_index(["trade_date", "symbol"]).close.to_dict()
    found = {}
    for day, symbols in by_day.items():
        try:
            path = day_file(root, day)
        except FileNotFoundError:
            continue
        parts = []
        for chunk in pd.read_csv(path, dtype={"symbol": str}, chunksize=200_000):
            part = chunk[chunk.symbol.str[:6].isin(symbols)]
            if not part.empty:
                parts.append(part)
        if parts:
            for symbol, bars in pd.concat(parts).groupby("symbol"):
                key = (day, str(symbol)[:6])
                if key in caps:
                    found[key] = minute_features(bars, float(caps[key]))
    return found


def load_cached_minute_features(path: Path) -> dict:
    """Reuse already checked first-board features when adding daily variables."""
    columns = ["symbol", "first_limit_date", *FEATURES["first_board_minute"]]
    frame = pd.read_csv(path, usecols=columns, dtype={"symbol": str})
    frame = frame.drop_duplicates(columns)
    if frame.duplicated(["symbol", "first_limit_date"]).any():
        raise ValueError("Cached minute features disagree within a first-board event")
    return {(row.first_limit_date, row.symbol): {name: getattr(row, name) for name in FEATURES["first_board_minute"]}
            for row in frame.itertuples(index=False)}


def future_labels(by_day: dict, calendar: list[str], day_i: int, horizon: int) -> dict:
    future = []
    for step in range(1, max(5, horizon) + 1):
        j = day_i + step
        future.append(by_day.get(calendar[j]) if j < len(calendar) else None)
    next_day = future[0]
    next_known = next_day is not None
    next_up = bool(next_day.up) if next_known else None
    next_ge7 = bool(not next_day.up and next_day.ret >= .07) if next_known else None
    window = future[:horizon]
    window_known = all(row is not None for row in window)
    five = future[:5]
    five_known = all(row is not None for row in five)
    return {
        "next_date": calendar[day_i + 1] if day_i + 1 < len(calendar) else None,
        "label_next_observed": next_known,
        "label_next_limit_up": next_up,
        "label_next_ge7_nonlimit": next_ge7,
        "label_next_limit_or_ge7": next_up or next_ge7 if next_known else None,
        f"label_next{horizon}_observed": window_known,
        f"label_next{horizon}_any_limit_up": any(row.up for row in window) if window_known else None,
        "label_next5_observed": five_known,
        "label_next5_limit_count": sum(row.up for row in five) if five_known else None,
        "label_next5_two_limits": sum(row.up for row in five) >= 2 if five_known else None,
    }


def build_panel(daily: pd.DataFrame, events: pd.DataFrame, *, max_cooling: int = 3,
                horizon: int = 3, minute_bars: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 1 <= max_cooling <= 5 or not 1 <= horizon <= 5:
        raise ValueError("max_cooling and horizon must each be 1..5 market days")
    daily = prepare_daily(daily)
    calendar = sorted(daily.trade_date.unique())
    day_index = {day: i for i, day in enumerate(calendar)}
    market = daily.groupby("trade_date").agg(
        market_limit_up_count=("up", "sum"),
        market_ge7_count=("ret", lambda s: int((s.ge(.07) & ~daily.loc[s.index, "up"]).sum())),
        market_up_fraction=("ret", lambda s: float(s.gt(0).mean())),
        market_median_return=("ret", "median"),
    ).to_dict("index")
    for i, day in enumerate(calendar):
        previous = market[calendar[i - 1]]["market_limit_up_count"] if i else np.nan
        market[day]["market_prev_limit_up_count"] = previous
        market[day]["market_limit_up_count_change"] = market[day]["market_limit_up_count"] - previous
    symbols = {symbol: frame.reset_index(drop=True) for symbol, frame in daily.groupby("symbol", sort=False)}
    events = events.copy()
    events["symbol"] = events.symbol.astype(str).str.zfill(6)
    features, labels = [], []
    for event in events.itertuples(index=False):
        frame = symbols.get(event.symbol)
        if frame is None or event.first_limit_date not in day_index or event.streak_end_date not in day_index:
            continue
        by_day = {row.trade_date: row for row in frame.itertuples(index=False)}
        first = by_day.get(event.first_limit_date)
        last = by_day.get(event.streak_end_date)
        if first is None or last is None or not first.up or not last.up:
            continue
        first_loc = frame.index[frame.trade_date.eq(event.first_limit_date)][0]
        prior = frame.iloc[max(0, first_loc - 5):first_loc]
        # Require five consecutive market dates; suspensions must not create false baselines.
        expected = calendar[day_index[event.first_limit_date] - 5:day_index[event.first_limit_date]]
        if len(expected) != 5 or prior.trade_date.tolist() != expected:
            prior = frame.iloc[0:0]
        mean_vol = prior.volume.mean() if len(prior) == 5 and prior.volume.gt(0).all() else np.nan
        mean_amount = prior.amount.mean() if len(prior) == 5 and prior.amount.gt(0).all() else np.nan
        mean_turnover = prior.turn_ratio.mean() if len(prior) == 5 and prior.turn_ratio.notna().all() else np.nan
        streak = frame[frame.trade_date.between(event.first_limit_date, event.streak_end_date)]
        base = {
            "streak_length": event.streak_length,
            **candle(first, "first"),
            "first_one_price_proxy": int(first.open == first.high == first.low == first.close and first.volume > 0),
            "first_volume_ratio_pre5": ratio(first.volume, mean_vol),
            "first_amount_ratio_pre5": ratio(first.amount, mean_amount),
            "first_turnover_pct": first.turn_ratio,
            "pre5_return": float((prior.close / prior.preclose).prod() - 1)
            if len(prior) == 5 and prior.preclose.gt(0).all() and prior.close.gt(0).all() else np.nan,
            "pre5_mean_amount": mean_amount,
            "pre5_mean_turnover_pct": mean_turnover,
            "last_board_range": ratio(last.high - last.low, last.preclose),
            "last_board_open_gap": ratio(last.open, last.preclose) - 1,
            "last_board_volume": last.volume,
            "last_board_amount": last.amount,
            "last_board_turnover_pct": last.turn_ratio,
            "streak_return": ratio(last.close, first.preclose) - 1,
            "streak_total_turnover_pct": streak.turn_ratio.sum(min_count=len(streak)),
            "streak_last_first_volume_ratio": ratio(last.volume, first.volume),
        }
        base.update((minute_bars or {}).get((event.first_limit_date, event.symbol)) or minute_features(None, first.close))
        cooling = []
        end_i = day_index[event.streak_end_date]
        for gap in range(1, max_cooling + 1):
            i = end_i + gap
            if i >= len(calendar):
                break
            row = by_day.get(calendar[i])
            if row is None or row.up:
                break
            cooling.append(row)
            lows = [r.low for r in cooling]
            highs = [r.high for r in cooling]
            returns = [ratio(r.close, r.preclose) - 1 for r in cooling]
            ranges = [ratio(r.high - r.low, r.preclose) for r in cooling]
            volumes = [last.volume, *(r.volume for r in cooling)]
            turnovers = [r.turn_ratio for r in cooling]
            low = min(lows) if all(pd.notna(value) for value in lows) else np.nan
            high = max(highs) if all(pd.notna(value) for value in highs) else np.nan
            key = {"symbol": event.symbol, "first_limit_date": event.first_limit_date,
                   "streak_end_date": event.streak_end_date,
                   "asof_date": calendar[i], "cooling_day": gap}
            item = {
                **key, **base,
                **candle(row, "day"),
                "day_return": row.ret,
                "day_high_return": ratio(row.high, row.preclose) - 1,
                "day_low_return": ratio(row.low, row.preclose) - 1,
                "day_close_from_streak_end": ratio(row.close, last.close) - 1,
                "day_high_from_streak_end": ratio(row.high, last.close) - 1,
                "day_low_from_streak_end": ratio(row.low, last.close) - 1,
                "day_close_from_first_board": ratio(row.close, first.close) - 1,
                "day_volume": row.volume, "day_amount": row.amount,
                "day_turnover_pct": row.turn_ratio,
                "day_volume_ratio_last_board": ratio(row.volume, last.volume),
                "day_volume_ratio_pre5": ratio(row.volume, mean_vol),
                "day_amount_ratio_pre5": ratio(row.amount, mean_amount),
                "day_amount_ratio_last_board": ratio(row.amount, last.amount),
                "day_turnover_ratio_last_board": ratio(row.turn_ratio, last.turn_ratio),
                "day_turnover_ratio_pre5": ratio(row.turn_ratio, mean_turnover),
                "path_cumulative_return": ratio(row.close, last.close) - 1,
                "path_min_low_from_end": ratio(low, last.close) - 1,
                "path_max_high_from_end": ratio(high, last.close) - 1,
                "path_recovery_from_low": ratio(row.close, low) - 1,
                "path_pullback_from_high": ratio(row.close, high) - 1,
                "path_up_close_days": sum(value > 0 for value in returns) if all(pd.notna(value) for value in returns) else np.nan,
                "path_positive_body_days": sum(r.close > r.open for r in cooling)
                if all(pd.notna(r.close) and pd.notna(r.open) for r in cooling) else np.nan,
                "path_volume_contract_days": sum(volumes[j] < volumes[j - 1] for j in range(1, len(volumes)))
                if all(pd.notna(value) for value in volumes) else np.nan,
                "path_total_turnover_pct": sum(turnovers) if all(pd.notna(value) for value in turnovers) else np.nan,
                "path_mean_range": float(np.mean(ranges)) if all(pd.notna(value) for value in ranges) else np.nan,
                "path_return_std": float(np.std(returns, ddof=0)) if gap >= 2 and all(pd.notna(value) for value in returns) else np.nan,
                "path_last_first_volume_ratio": ratio(row.volume, cooling[0].volume),
                **market[calendar[i]],
            }
            features.append(item)
            labels.append({**key, **future_labels(by_day, calendar, i, horizon)})
    feature_frame = pd.DataFrame(features)
    label_frame = pd.DataFrame(labels)
    if not feature_frame.empty:
        expected = set(KEY) | {name for group in FEATURES.values() for name in group}
        if set(feature_frame.columns) != expected:
            raise AssertionError(f"Feature catalog mismatch: {set(feature_frame.columns) ^ expected}")
        if feature_frame.duplicated(KEY).any():
            raise AssertionError("Duplicate decision sample")
        feature_frame = feature_frame.sort_values(KEY).reset_index(drop=True)
        label_frame = label_frame.sort_values(KEY).reset_index(drop=True)
    return feature_frame, label_frame


def fetch_daily() -> pd.DataFrame:
    parts = []
    with db_connection() as conn:
        for month in pd.period_range(SOURCE_START[:7], SOURCE_END[:7], freq="M"):
            lo = max(SOURCE_START, str(month.start_time.date()))
            hi = min(SOURCE_END, str(month.end_time.date()))
            parts.append(query(conn, "SELECT trade_date,stk_code,preclose,open,high,low,close,"
                               "volume,amount,turn_ratio,is_limit_price,update_time "
                               "FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s", (lo, hi)))
        conn.rollback()
    return pd.concat(parts, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-csv", type=Path, help="Enriched daily CSV; absent means read-only DB fetch")
    parser.add_argument("--minute-root", type=Path, help="Optional unadjusted MK/none daily minute archive")
    parser.add_argument("--minute-features-csv", type=Path,
                        help="Reuse first-board minute columns from a prior features.csv.gz")
    parser.add_argument("--max-cooling", type=int, default=3)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    if args.minute_root and args.minute_features_csv:
        parser.error("choose either --minute-root or --minute-features-csv")
    daily = pd.read_csv(args.daily_csv, dtype={"stk_code": str}) if args.daily_csv else fetch_daily()
    events = pd.read_csv(R1_EVENTS, dtype={"symbol": str})
    minutes = (load_first_board_minutes(args.minute_root, events, daily) if args.minute_root else
               load_cached_minute_features(args.minute_features_csv) if args.minute_features_csv else None)
    feature_frame, label_frame = build_panel(daily, events, max_cooling=args.max_cooling,
                                             horizon=args.horizon, minute_bars=minutes)
    args.out.mkdir(parents=True, exist_ok=True)
    source_path = args.daily_csv or args.out / "daily_source.csv.gz"
    if args.daily_csv is None:
        daily.to_csv(source_path, index=False, compression="gzip")
    with source_path.open("rb") as source_file:
        digest = hashlib.file_digest(source_file, "sha256").hexdigest()
    with Path(__file__).open("rb") as script_file:
        script_digest = hashlib.file_digest(script_file, "sha256").hexdigest()
    with R1_EVENTS.open("rb") as event_file:
        cohort_digest = hashlib.file_digest(event_file, "sha256").hexdigest()
    minute_source = args.minute_features_csv or (
        args.minute_root / "compressed_manifest.json" if args.minute_root else None)
    minute_digest = None
    if minute_source and minute_source.is_file():
        with minute_source.open("rb") as minute_file:
            minute_digest = hashlib.file_digest(minute_file, "sha256").hexdigest()
    feature_frame.to_csv(args.out / "features.csv.gz", index=False, compression="gzip")
    label_frame.to_csv(args.out / "labels.csv.gz", index=False, compression="gzip")
    (args.out / "feature_catalog.json").write_text(json.dumps(FEATURES, ensure_ascii=False, indent=2) + "\n")
    manifest = {"source": "kingdomai.kcrp_stock_price" if args.daily_csv is None else str(args.daily_csv),
                "source_snapshot": str(source_path), "source_sha256": digest,
                "script_sha256": script_digest, "cohort_sha256": cohort_digest,
                "source_rows": len(daily),
                "source_max_update_time": str(daily.update_time.max()) if "update_time" in daily else None,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "cohort": str(R1_EVENTS), "source_end": SOURCE_END,
                "decision_time": "cooling day close, after final daily bar",
                "max_cooling": args.max_cooling, "future_horizon": args.horizon,
                "minute_source": str(args.minute_root or args.minute_features_csv) if minutes is not None else None,
                "minute_source_sha256": minute_digest,
                "samples": len(feature_frame),
                "minute_complete": int(feature_frame.first_minute_complete.sum()) if len(feature_frame) else 0}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
