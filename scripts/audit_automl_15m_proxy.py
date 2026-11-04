"""Paired 15-minute proxy audit against exact 14:40/next-10-minute samples."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.backfill_automl_august_minute_full import connection
from scripts.experiment_automl_tail_three_class import COMPACT_FEATURES, read_samples
from scripts.run_automl_tail_today import fitted_model


def classify(values: pd.Series) -> np.ndarray:
    return np.select((values > .01, values < .005), (1, -1), default=0)


def run(expanded: Path, sep30: Path, env_file: Path, output: Path) -> dict:
    rows = pd.concat([read_samples(expanded), read_samples(sep30)], ignore_index=True)
    audit = pd.concat([pd.read_csv(p.parent / "candidates_1440.csv", dtype={"symbol6": str})
                       for p in (expanded, sep30)], ignore_index=True)
    audit["signal_date"] = pd.to_datetime(audit.signal_date)
    audit = audit[["signal_date", "symbol6", "entry_1450"]]
    rows = rows.merge(audit, on=["signal_date", "symbol6"], validate="one_to_one")
    signal_points = []
    tail_points = []
    morning_highs = []
    with connection(env_file) as db:
        with db.cursor() as cur:
            for day in sorted(rows.signal_date.dt.date.unique()):
                cur.execute("""SELECT stk_code AS symbol6,bar_end_time,latest_price,
                        is_finalized,is_fallback,source_snapshot_time
                    FROM aiia_stock_realtime_minute_snapshot_full
                    WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                      AND bar_end_time IN (%s,%s)""",
                            (day, f"{day} 14:30:00", f"{day} 14:45:00"))
                signal_points.extend({"signal_date": day, **r} for r in cur.fetchall())
                cur.execute("""SELECT stk_code AS symbol6,SUM(volume) AS tail_volume_hands,
                        MIN(low_price) AS tail_low,COUNT(*) AS tail_bars,
                        MAX(is_fallback) AS tail_fallback,MIN(is_finalized) AS tail_finalized,
                        SUM(source_snapshot_time=bar_end_time) AS exact_tail_bars
                    FROM aiia_stock_realtime_minute_snapshot_full
                    WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                      AND bar_end_time BETWEEN %s AND %s GROUP BY stk_code""",
                            (day, f"{day} 14:41:00", f"{day} 14:45:00"))
                tail_points.extend({"signal_date": day, **r} for r in cur.fetchall())
            for day in sorted(rows.next_date.dt.date.unique()):
                cur.execute("""SELECT stk_code AS symbol6,MAX(high_price) AS extra_high,
                        COUNT(*) AS extra_bars,MAX(is_fallback) AS extra_fallback,
                        MIN(is_finalized) AS extra_finalized,
                        SUM(source_snapshot_time=bar_end_time) AS exact_extra_bars
                    FROM aiia_stock_realtime_minute_snapshot_full
                    WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
                      AND bar_end_time BETWEEN %s AND %s GROUP BY stk_code""",
                            (day, f"{day} 09:41:00", f"{day} 09:45:00"))
                morning_highs.extend({"next_date": day, **r} for r in cur.fetchall())
    signal = pd.DataFrame(signal_points)
    signal["signal_date"] = pd.to_datetime(signal.signal_date)
    signal["minute"] = pd.to_datetime(signal.bar_end_time).dt.strftime("%H:%M")
    signal = signal[(signal.is_finalized.eq(1)) & (signal.is_fallback.eq(0)) &
                    pd.to_datetime(signal.source_snapshot_time).eq(
                        pd.to_datetime(signal.bar_end_time))]
    signal["latest_price"] = pd.to_numeric(signal.latest_price, errors="coerce")
    signal = signal.pivot(index=["signal_date", "symbol6"], columns="minute",
                          values="latest_price").rename(columns={"14:30": "price_1430",
                                                         "14:45": "price_1445"}).reset_index()
    tail = pd.DataFrame(tail_points)
    tail["signal_date"] = pd.to_datetime(tail.signal_date)
    complete_tail = (tail.tail_bars.eq(5) & tail.exact_tail_bars.eq(5) &
                     tail.tail_fallback.eq(0) & tail.tail_finalized.eq(1))
    tail.loc[~complete_tail, ["tail_volume_hands", "tail_low"]] = np.nan
    for field in ("tail_volume_hands", "tail_low"):
        tail[field] = pd.to_numeric(tail[field], errors="coerce")
    morning = pd.DataFrame(morning_highs)
    morning["next_date"] = pd.to_datetime(morning.next_date)
    complete = (morning.extra_bars.eq(5) & morning.exact_extra_bars.eq(5) &
                morning.extra_fallback.eq(0) & morning.extra_finalized.eq(1))
    morning.loc[~complete, "extra_high"] = np.nan
    morning["extra_high"] = pd.to_numeric(morning.extra_high, errors="coerce")
    paired = rows.merge(signal, on=["signal_date", "symbol6"], validate="one_to_one")
    paired = paired.merge(tail[["signal_date", "symbol6", "tail_volume_hands", "tail_low"]],
                          on=["signal_date", "symbol6"], validate="one_to_one")
    paired = paired.merge(morning[["next_date", "symbol6", "extra_high"]],
                          on=["next_date", "symbol6"], validate="one_to_one")
    paired = paired.dropna(subset=["price_1430", "price_1445", "extra_high", "entry_1450",
                                    "tail_volume_hands", "tail_low"])
    if len(paired) < .99 * len(rows):
        raise ValueError("Fewer than 99% of exact rows have proxy comparison data")
    high10 = paired.entry_1450 * (1 + paired.target_next_high10_return)
    high15 = np.maximum(high10, paired.extra_high)
    class10 = paired["class"].to_numpy()
    class15 = classify(high15 / paired.entry_1450 - 1)
    class15_entry1445 = classify(high15 / paired.price_1445 - 1)
    report = {"exact_sample_rows": len(rows), "paired_rows": len(paired),
              "signal_dates": int(paired.signal_date.nunique()),
              "cutoff_proxy": {},
              "label_proxy": {
                  "true_10m_strong": int((class10 == 1).sum()),
                  "first_15m_strong": int((class15 == 1).sum()),
                  "false_strong_from_extra_5m": int(((class15 == 1) & (class10 != 1)).sum()),
                  "changed_any_class": int((class15 != class10).sum()),
                  "true_10m_critical": int((class10 == -1).sum()),
                  "first_15m_critical": int((class15 == -1).sum()),
                  "extra_5m_becomes_strong": int(((class15 == 1) & (class10 == -1)).sum()),
                  "entry_1445_vs_1450_median_abs_return_pp": float(
                      (paired.price_1445 / paired.entry_1450 - 1).abs().median() * 100),
                  "entry_1445_vs_1450_p90_abs_return_pp": float(
                      (paired.price_1445 / paired.entry_1450 - 1).abs().quantile(.9) * 100),
                  "first_15m_and_entry_1445_changed_class": int((class15_entry1445 != class10).sum()),
                  "first_15m_and_entry_1445_strong": int((class15_entry1445 == 1).sum()),
                  "first_15m_and_entry_1445_false_strong": int(((class15_entry1445 == 1) &
                                                                  (class10 != 1)).sum()),
                  "first_15m_and_entry_1445_missed_strong": int(((class15_entry1445 != 1) &
                                                                   (class10 == 1)).sum()),
              }}
    for clock, name in (("price_1430", "14:30 complete bar, causal"),
                        ("price_1445", "14:45 second-last bar, five-minute future")):
        pct = paired[clock] / paired.t_reference_preclose - 1
        diff = (pct - paired.signal_return).abs()
        report["cutoff_proxy"][name] = {
            "median_abs_return_error_pp": float(diff.median() * 100),
            "p90_abs_return_error_pp": float(diff.quantile(.9) * 100),
            "original_3to6_candidates_outside_proxy": int((~pct.between(.03, .06)).sum()),
            "outside_proxy_rate": float((~pct.between(.03, .06)).mean())}
    # A full eight-factor 14:45 reconstruction inside the original 14:40
    # candidate universe. New stocks crossing into the band after 14:40 are
    # absent by design; this is a controlled paired diagnostic only.
    cohort = paired.copy()
    cohort["signal_return"] = cohort.price_1445 / cohort.t_reference_preclose - 1
    cohort = cohort[cohort.signal_return.between(.03, .06)].copy()
    shares_1445 = cohort.minute_volume_shares + cohort.tail_volume_hands * 100
    cohort["volume_ratio"] = shares_1445 * 240 / (225 * cohort.avg_volume5_shares)
    cohort["turnover_so_far_pct"] = cohort.turnover_so_far_pct * shares_1445 / cohort.minute_volume_shares
    highest_ma = cohort[["ma5", "ma10", "ma20"]].max(axis=1)
    cohort["price_above_all_ma"] = (cohort.price_1445 > highest_ma).astype(int)
    cohort["all_intraday_lows_above_ma"] = (
        cohort.all_intraday_lows_above_ma.eq(1) & cohort.tail_low.gt(highest_ma)).astype(int)
    cohort["original_class"] = cohort["class"]
    high15_cohort = np.maximum(cohort.entry_1450 * (1 + cohort.target_next_high10_return),
                               cohort.extra_high)
    cohort["class"] = classify(high15_cohort / cohort.price_1445 - 1)
    train = cohort[cohort.signal_date.between("2026-08-25", "2026-09-21")].copy()
    test = cohort[~cohort.signal_date.isin(train.signal_date.unique())].copy()
    if train.signal_date.nunique() != 20 or test.signal_date.nunique() != 17:
        raise ValueError("Proxy cohort lost training or test dates")
    model = fitted_model(train)
    test["score"] = model.predict_proba(test[list(COMPACT_FEATURES)])[:, 1]
    test = test.sort_values(["signal_date", "score", "symbol6"],
                            ascending=[True, False, True])
    test["rank"] = test.groupby("signal_date").cumcount() + 1
    report["controlled_1445_model_within_1440_pool"] = {
        "training_days": int(train.signal_date.nunique()), "training_rows": len(train),
        "test_days": int(test.signal_date.nunique()), "test_candidates": len(test),
        "excluded_1440_candidates_outside_1445_band": len(paired) - len(cohort),
        "new_1445_only_candidates_included": False}
    for k in (2, 5):
        selected = test[test["rank"].le(k)]
        report["controlled_1445_model_within_1440_pool"][f"top{k}"] = {
            "selections": len(selected),
            "proxy_15m_strong": int(selected["class"].eq(1).sum()),
            "proxy_15m_critical": int(selected["class"].eq(-1).sum()),
            "original_10m_strong": int(selected.original_class.eq(1).sum()),
            "original_10m_critical": int(selected.original_class.eq(-1).sum())}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expanded", type=Path, default=Path(
        "outputs/stock_automl/tail_expanded_august/trainable_1440.csv"))
    p.add_argument("--sep30", type=Path, default=Path(
        "outputs/stock_automl/tail_sep30_check/trainable_1440.csv"))
    p.add_argument("--env-file", type=Path, default=Path("/Volumes/ext/fin_agent/.env"))
    p.add_argument("--output", type=Path, default=Path(
        "docs/stock_automl_runs/20261008_sina_15m_proxy/summary.json"))
    a = p.parse_args()
    print(json.dumps(run(a.expanded, a.sep30, a.env_file, a.output),
                     ensure_ascii=False, indent=2))
