"""Build July 2026 14:45 candidate/label rows from cached Sina 15m bars."""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.backfill_automl_august_minute_full import connection
from scripts.build_automl_tail_standard import load_daily, prior_profile
from scripts.experiment_automl_1450_grid import board_limit
from scripts.experiment_automl_tail_three_class import COMPACT_FEATURES


TIMES = ([f"{m//60:02d}:{m%60:02d}" for m in range(9*60+45,11*60+31,15)] +
         [f"{m//60:02d}:{m%60:02d}" for m in range(13*60+15,15*60+1,15)])
THROUGH_1445 = TIMES[:15]


def compute(cache: Path, env_file: Path, output: Path) -> dict:
    with connection(env_file) as db:
        daily, value = load_daily(db, history_start="2026-05-01", end="2026-08-03")
        profile, calendar = prior_profile(daily, value)
        with db.cursor() as cursor:
            cursor.execute("""SELECT trade_date AS date,LEFT(stk_code,6) AS symbol6,
                    preclose FROM kcrp_stock_price
                WHERE trade_date BETWEEN '2026-07-01' AND '2026-07-31'
                  AND preclose>0""")
            july_days = pd.DataFrame(cursor.fetchall())
            cursor.execute("SELECT LEFT(stk_code,6) AS symbol6,stk_name AS name FROM kcrp_stock_baseinfo")
            names = {r["symbol6"]: r["name"] or "" for r in cursor.fetchall()}
    next_day = {str(a)[:10]: str(b)[:10] for a, b in zip(calendar[:-1], calendar[1:])}
    wanted = defaultdict(set)
    references = {}
    for row in july_days.itertuples():
        day = str(row.date)[:10]
        wanted[row.symbol6].add(day)
        references[(day, row.symbol6)] = float(row.preclose)
    records = []
    status = Counter()
    for symbol, dates in wanted.items():
        path = cache / f"{symbol}.json.gz"
        if not path.exists():
            status["stock_cache_missing"] += 1
            continue
        with gzip.open(path, "rt", encoding="utf-8") as source:
            bars = json.load(source)
        by_day: dict[str, dict[str, dict]] = defaultdict(dict)
        for bar in bars:
            stamp = str(bar.get("day", ""))
            if len(stamp) < 16:
                continue
            by_day[stamp[:10]][stamp[11:16]] = bar
        for day in dates:
            status["source_stock_days"] += 1
            one = by_day.get(day, {})
            if not set(TIMES).issubset(one):
                status["signal_15m_incomplete"] += 1
                continue
            next_date = next_day.get(day)
            if not next_date:
                status["next_trading_date_missing"] += 1
                continue
            next_open_bar = by_day.get(next_date, {}).get("09:45")
            if next_open_bar is None:
                status["next_0945_missing"] += 1
                continue
            try:
                signal = float(one["14:45"]["close"])
                previous = references[(day, symbol)]
                opens = [float(one[t]["open"]) for t in THROUGH_1445]
                highs = [float(one[t]["high"]) for t in THROUGH_1445]
                lows = [float(one[t]["low"]) for t in THROUGH_1445]
                closes = [float(one[t]["close"]) for t in THROUGH_1445]
                volumes = [float(one[t]["volume"]) for t in THROUGH_1445]
                early_high = float(next_open_bar["high"])
                early_open = float(next_open_bar["open"])
            except (ValueError, TypeError, IndexError, KeyError, OverflowError):
                status["invalid_source_value"] += 1
                continue
            if (min([signal, previous, early_high, early_open, *opens, *lows, *closes]) <= 0 or
                    min(volumes) < 0 or early_high < early_open or
                    any(high < max(opn, close) or low > min(opn, close)
                        for opn, high, low, close in zip(opens, highs, lows, closes))):
                status["invalid_price_or_volume"] += 1
                continue
            gain = signal / previous - 1
            if not .03 <= gain <= .06:
                status["outside_3to6_1445"] += 1
                continue
            records.append({"signal_date": day, "next_date": next_date,
                            "symbol6": symbol, "name": names.get(symbol, ""),
                            "t_reference_preclose": previous, "signal_price": signal,
                            "signal_return": gain, "minute_volume_shares": sum(volumes),
                            "min_low_so_far": min(lows), "next_open": early_open,
                            "next_high15": early_high, "bars_signal": len(THROUGH_1445),
                            "bars_next": 1})
            status["raw_3to6_1445"] += 1
    if not records:
        raise ValueError("No July 14:45 candidate rows")
    candidates = pd.DataFrame(records)
    candidates["signal_date"] = pd.to_datetime(candidates.signal_date)
    candidates["next_date"] = pd.to_datetime(candidates.next_date)
    prior = profile[profile.signal_date.between("2026-07-01", "2026-07-31")]
    candidates = candidates.merge(prior, on=["signal_date", "symbol6"],
                                  how="left", validate="one_to_one")
    candidates["minute_complete"] = candidates.bars_signal.eq(15) & candidates.bars_next.eq(1)
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
    candidates["price_above_all_ma"] = candidates.signal_price.gt(highest_ma).astype("Int8")
    candidates["all_intraday_lows_above_ma"] = candidates.min_low_so_far.gt(highest_ma).astype("Int8")
    candidates["target_next_high15_return"] = candidates.next_high15 / candidates.signal_price - 1
    # Exact 1.00%/0.50% price ratios can straddle a float threshold by one ULP.
    target_for_class = candidates.target_next_high15_return.round(10)
    candidates["class"] = np.select((target_for_class > .01,
                                     target_for_class < .005), (1, -1), 0)
    # Current base-info names are not historical July ST status. Never let them
    # decide retrospective eligibility; use the exchange board only.
    candidates["limit_buffer"] = [board_limit(symbol, "") for symbol in candidates.symbol6]
    filter_steps = {
        "raw_3to6": len(candidates),
    }
    complete = pd.Series(True, index=candidates.index)
    for name, condition in (
        ("complete_15m", candidates.minute_complete),
        ("complete_tminus1_history", candidates.history_complete.eq(True)),
        ("no_recent_adjustment", candidates.adj_price_break_20d.eq(False)),
        ("no_t_day_reference_break",
         candidates.t_reference_preclose.div(candidates.prior_close).sub(1).abs().le(.01)),
        ("complete_tminus1_valuation", candidates.value_complete.eq(True)),
        ("not_near_limit", candidates.limit_buffer.notna() &
         candidates.signal_return.lt(candidates.limit_buffer)),
    ):
        complete &= condition
        filter_steps[name] = int(complete.sum())
    for field in COMPACT_FEATURES:
        complete &= pd.to_numeric(candidates[field], errors="coerce").notna()
    filter_steps["all_model_features_present"] = int(complete.sum())
    selected = candidates[complete].copy()
    for field in ("volume_4of5_increasing", "ma_bull_5_10_20",
                  "price_above_all_ma", "all_intraday_lows_above_ma"):
        selected[field] = selected[field].astype(int)
    if selected.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate candidate stock-day")
    if (selected.all_intraday_lows_above_ma.eq(1) & selected.price_above_all_ma.eq(0)).any():
        raise ValueError("Intraday-low factor contradicts price-above-MA factor")
    output.mkdir(parents=True, exist_ok=True)
    candidates.sort_values(["signal_date", "symbol6"]).to_csv(output / "all_candidates.csv", index=False)
    selected.sort_values(["signal_date", "symbol6"]).to_csv(output / "trainable_proxy_candidates.csv", index=False)
    result = {"source": "Sina unadjusted 15m cached bars; daily/valuation from KingdomAI",
              "decision_time": "14:45", "entry_proxy": "same completed 14:45 bar close",
              "label": "next trading day 09:45 15m bar high / entry proxy - 1",
              "first_signal_date": selected.signal_date.min().date().isoformat() if len(selected) else None,
              "last_signal_date": selected.signal_date.max().date().isoformat() if len(selected) else None,
              "raw_candidates": len(candidates), "complete_candidates": len(selected),
              "complete_days": int(selected.signal_date.nunique()),
              "source_symbols_expected": len(wanted),
              "fetch_cache_symbols": len(list(cache.glob("*.json.gz"))),
              "candidate_filter_steps": filter_steps,
              "status": dict(status)}
    (output / "build_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache", type=Path, default=Path("outputs/stock_automl/sina_15m_july/cache"))
    p.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    p.add_argument("--output", type=Path, default=Path("outputs/stock_automl/sina_15m_july"))
    a = p.parse_args()
    compute(a.cache, a.env_file, a.output)
