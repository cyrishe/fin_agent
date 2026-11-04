"""Rank each rolling 20-day test cohort by lowest predicted critical risk."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from scripts.audit_automl_minute_api_backfill import request_bars
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_fuzzy_boundary import C, PRICES, prepare, targets


OCT8 = Path("docs/stock_automl_runs/20261009_seven_factor_cash_backtest/20261008_exact_candidates.csv")
OUT = Path("docs/stock_automl_runs/20261009_low_critical_top10")


def score_fold(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    x_train, x_test = prepare(train, test)
    q, weights = targets(train.max_high / train.entry_1440 - 1, "边界降权")
    model = LogisticRegression(C=C, max_iter=2000, solver="lbfgs")
    model.fit(x_train[:, 1:], np.array([-1, 0, 1])[q.argmax(axis=1)],
              sample_weight=weights / weights.mean())
    probabilities = model.predict_proba(x_test[:, 1:])
    if not np.array_equal(model.classes_, [-1, 0, 1]):
        raise ValueError("Unexpected class order")
    ranked = test.copy()
    ranked["p_critical"] = probabilities[:, 0]
    ranked["p_neutral"] = probabilities[:, 1]
    ranked["p_hit"] = probabilities[:, 2]
    ranked["old_score"] = ranked.p_hit - ranked.p_critical
    ranked = ranked.sort_values(["p_critical", "symbol6"], ascending=[True, True])
    ranked["risk_rank"] = np.arange(1, len(ranked) + 1)
    return ranked[ranked.risk_rank.le(10) & ranked.p_critical.le(.20)].copy()


def october_prices(conn, rows: pd.DataFrame) -> pd.DataFrame:
    codes = rows.symbol6.tolist()
    holders = ",".join(["%s"] * len(codes))
    entries = query(conn, "SELECT LEFT(stk_code,6) symbol6, latest_price, "
                    "is_finalized, is_fallback, bar_end_time, source_snapshot_time "
                    "FROM aiia_stock_realtime_minute_snapshot_full "
                    "WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1 "
                    f"AND bar_end_time=%s AND LEFT(stk_code,6) IN ({holders})",
                    ("2026-10-08", "2026-10-08 14:40:00", *codes))
    if (len(entries) != len(codes) or entries.symbol6.duplicated().any() or
        not (entries.is_finalized.eq(1) & entries.is_fallback.eq(0) &
             pd.to_datetime(entries.bar_end_time).eq(
                 pd.to_datetime(entries.source_snapshot_time))).all()):
        raise ValueError("October 8 entries are not exact")
    entry_map = dict(zip(entries.symbol6, entries.latest_price.astype(float)))
    result = []
    for code in codes:
        raw = request_bars(code, 0, 300)
        bars = [bar for bar in raw if int(bar["sttDateTime"]["iDate"]) == 20261009
                and 571 <= int(bar["sttDateTime"]["shtTime"]) <= 580]
        if sorted(int(bar["sttDateTime"]["shtTime"]) for bar in bars) != list(range(571, 581)):
            raise ValueError(f"Missing October 9 ten bars: {code}")
        result.append({"symbol6": code, "entry_1440": entry_map[code],
                       "max_open": max(float(bar["fOpen"]) for bar in bars),
                       "max_close": max(float(bar["fClose"]) for bar in bars),
                       "max_high": max(float(bar["fHigh"]) for bar in bars)})
    return pd.DataFrame(result)


def signal_day_closes(conn, rows: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for signal_date, group in rows.groupby("signal_date", sort=True):
        codes = group.symbol6.tolist()
        holders = ",".join(["%s"] * len(codes))
        daily = query(conn, "SELECT LEFT(stk_code,6) symbol6, preclose, open, high, "
                      "low, close, volume, amount, turn_ratio FROM kcrp_stock_price "
                      f"WHERE trade_date=%s AND LEFT(stk_code,6) IN ({holders})",
                      (signal_date, *codes))
        if len(daily) != len(codes) or daily.symbol6.duplicated().any():
            raise ValueError(f"Missing or duplicate signal-day close: {signal_date}")
        daily["signal_date"] = signal_date
        pieces.append(daily)
    return pd.concat(pieces, ignore_index=True)


def main() -> None:
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    extrema = pd.read_csv(PRICES, dtype={"symbol6": str})
    history = base.merge(extrema, on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(history.signal_date.unique())
    if len(dates) != 40 or len(history) != 14292:
        raise ValueError("Historical rolling cohort changed")
    selected = []
    for index in range(20, len(dates)):
        train_dates = dates[index - 20:index]
        train = history[history.signal_date.isin(train_dates)]
        test = history[history.signal_date.eq(dates[index])]
        picks = score_fold(train, test)
        picks["signal_date"] = dates[index]
        selected.append(picks)
    current = pd.read_csv(OCT8, dtype={"symbol6": str})
    if len(current) != 178 or not current.signal_return.between(.03, .06).all():
        raise ValueError("October 8 candidate source changed")
    current["signal_date"] = "2026-10-08"
    current["next_date"] = "2026-10-09"
    latest_train = history[history.signal_date.isin(dates[-20:])]
    selected.append(score_fold(latest_train, current))
    picks = pd.concat(selected, ignore_index=True)
    if picks.signal_date.nunique() != 21 or picks.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Unexpected selected batches")
    conn = db_connection(Path("/Volumes/ext/fin_agent/.env"))
    try:
        oct8 = picks[picks.signal_date.eq("2026-10-08")].copy()
        october = october_prices(conn, oct8)
        for col in ("entry_1440", "max_open", "max_close", "max_high"):
            oct8[col] = oct8.symbol6.map(october.set_index("symbol6")[col])
        picks.loc[oct8.index, ["entry_1440", "max_open", "max_close", "max_high"]] = \
            oct8[["entry_1440", "max_open", "max_close", "max_high"]].to_numpy()
        closes = signal_day_closes(conn, picks)
    finally:
        conn.rollback()
        conn.close()
    picks = picks.merge(closes, on=["signal_date", "symbol6"], validate="one_to_one")
    numeric = ["entry_1440", "max_open", "max_close", "max_high", "preclose",
               "open", "high", "low", "close", "volume", "amount", "turn_ratio"]
    for col in numeric:
        picks[col] = pd.to_numeric(picks[col], errors="raise")
    if picks[numeric].isna().any().any() or not np.allclose(
            picks[["p_critical", "p_neutral", "p_hit"]].sum(axis=1), 1):
        raise ValueError("Missing price or invalid probabilities")
    picks["prior_close_return"] = picks.close / picks.preclose - 1
    for field in ("open", "close", "high"):
        picks[f"next_max_{field}_return"] = picks[f"max_{field}"] / picks.entry_1440 - 1
    picks["max_high_class"] = np.select(
        [picks.next_max_high_return.gt(.01), picks.next_max_high_return.lt(0)],
        [1, -1], default=0)
    picks = picks.sort_values(["signal_date", "risk_rank"])
    picks["batch"] = picks.signal_date.map({date: i + 1 for i, date in enumerate(
        sorted(picks.signal_date.unique()))})
    columns = ["batch", "risk_rank", "symbol6", "name", "p_critical", "p_neutral",
               "p_hit", "old_score", "entry_1440", "max_high", "next_max_high_return",
               "max_open", "next_max_open_return", "max_close", "next_max_close_return",
               "max_high_class", "preclose", "open", "high", "low", "close",
               "prior_close_return", "volume", "amount", "turn_ratio"]
    detail = picks[columns].copy()
    summary = []
    for batch, group in detail.groupby("batch"):
        summary.append({"batch": int(batch), "chosen": len(group),
                        "high_hit": int(group.max_high_class.eq(1).sum()),
                        "high_neutral": int(group.max_high_class.eq(0).sum()),
                        "high_critical": int(group.max_high_class.eq(-1).sum()),
                        "close_hit": int(group.next_max_close_return.gt(.01).sum()),
                        "min_critical_probability": float(group.p_critical.min()),
                        "max_critical_probability": float(group.p_critical.max())})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "data.json").write_text(json.dumps({"detail": detail.to_dict("records"),
                                              "summary": summary}, ensure_ascii=False,
                                              indent=2) + "\n")
    print(json.dumps({"batches": len(summary), "selected": len(detail),
                      "by_batch": [s["chosen"] for s in summary],
                      "high_hit": int(detail.max_high_class.eq(1).sum()),
                      "high_neutral": int(detail.max_high_class.eq(0).sum()),
                      "high_critical": int(detail.max_high_class.eq(-1).sum()),
                      "close_hit": int(detail.next_max_close_return.gt(.01).sum())},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
