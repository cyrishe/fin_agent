"""Read-only, exact B2 decision and account replay from the frozen research assets."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_b2 import evaluate_morning
from scripts.automl_b2_data import model_bundle
from scripts.replay_limit_rebound_cash_r23 import replay, sha


ASSETS = Path("outputs/stock_automl/b2_assets_20261015")
R24 = Path("outputs/stock_automl/limit_rebound_four_minutes_r24_20261015")
CALENDAR = Path("outputs/stock_automl/limit_rebound_segments_r18_20261015/calendar.json")


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("outputs/stock_automl/b2_full_replay_20261018"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    ref = ASSETS / "reference"
    cohort = pd.read_pickle(ref / "four_all_top3.pkl")
    cohort = cohort[cohort.part.eq("test") & cohort.d1_date.ge("2026-01-01")].copy()
    minutes = pd.read_pickle(ref / "first10_evidence.pkl")
    bars = {(symbol, d0): group for (symbol, d0), group in minutes.groupby(["symbol", "d0_date"])}
    rule_cache = {}
    decision_rows = []
    days = sorted(cohort.d1_date.unique())
    months = sorted({day[:7] for day in days})
    day_no = {day: i + 1 for i, day in enumerate(days)}
    month_no = {month: i + 1 for i, month in enumerate(months)}
    for day, frame in cohort.groupby("d1_date", sort=True):
        d0 = frame.d0_date.iloc[0]
        month = d0[:7]
        if month not in rule_cache:
            rule_cache[month] = model_bundle(ASSETS, month)[1]["selected"]
        picks = [dict(symbol6=row.symbol, selection_rank=int(row.d0_rank),
                      preclose=float(row.d1_preclose), cap=float(row.d1_cap),
                      ordinary=bool(row.eligible)) for row in frame.itertuples()]
        sources = {row.symbol: bars.get((row.symbol, d0), pd.DataFrame()) for row in frame.itertuples()}
        result = evaluate_morning(picks, sources, day,
                                  datetime.fromisoformat(f"{day} 09:40:00"), rule_cache[month])
        by_symbol = {row["symbol6"]: row for row in result}
        for row in frame.itertuples():
            state = by_symbol[row.symbol]
            features = state.get("shape_features") or {}
            decision_rows.append(dict(
                date=day, month=month_no[day[:7]], day=day_no[day],
                symbol=row.symbol, rank=int(row.d0_rank), score=float(row.score),
                open_gap=features.get("open_gap"), body4=features.get("body5"),
                shape_pass=bool(state["shape_pass"]),
                activation_end=state.get("activation_bar_end", "")[11:16] or None,
                chosen=bool(state["chosen"]), body_pass=(features.get("body5", -1) > -.08 + 1e-12)
                if state["chosen"] else None,
                entry_end=state.get("entry_bar_end", "")[11:16] or None,
                actionable=bool(state["actionable"]), buy=state.get("entry_price"),
                reason=state.get("reason"), label3=int(row.label_3) if row.observed_3 else None,
            ))
    decisions = pd.DataFrame(decision_rows).sort_values(["day", "rank"])
    assert len(days) == 186 and len(decisions) == 557
    assert decisions.groupby("day").chosen.sum().le(1).all()
    assert decisions.groupby("day").actionable.sum().le(1).all()
    signals = decisions[decisions.actionable]
    assert len(signals) == 122

    picks = pd.read_pickle(ref / "policy_picks.pkl")
    picks = picks[picks.policy.eq("four_fit_delay") & picks.d1_date.ge("2026-01-01")].copy()
    actual = signals[["date", "symbol", "entry_end", "buy"]].rename(
        columns={"date": "d1_date", "entry_end": "entry_time", "buy": "entry"})
    expected = picks[["d1_date", "symbol", "entry_time", "entry"]]
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True),
                                  check_dtype=False, check_exact=True)

    calendar = json.loads(CALENDAR.read_text())
    summary, monthly, nav, ledger, excluded = replay(picks, calendar, fee_rate=.001)
    assert not excluded and len(ledger) == 122 and summary["closed"] == 121
    reference_summary = next(row for row in json.loads((ref / "accounts.json").read_text())
                             if row["policy"] == "four_fit_delay")
    for key in ("entries", "closed", "pending", "final_nav", "total_fees", "max_drawdown",
                "closed_mean_return", "closed_win_rate"):
        assert np.isclose(summary[key], reference_summary[key], atol=1e-7, rtol=0), key
    reference_ledger = pd.read_pickle(ref / "ledger.pkl")
    reference_ledger = reference_ledger[reference_ledger.policy.eq("four_fit_delay")].reset_index(drop=True)
    for key in ("symbol", "entry_date", "entry_time", "exit_date"):
        assert ledger[key].fillna("").reset_index(drop=True).equals(
            reference_ledger[key].fillna("").reset_index(drop=True)), key
    for key in ("buy", "sell", "cost", "shares", "buy_fee", "sell_fee", "pnl", "return_"):
        assert np.allclose(ledger[key], reference_ledger[key], atol=1e-7, rtol=0, equal_nan=True), key
    old_nav = pd.read_pickle(R24 / "daily_nav.pkl")
    old_nav = old_nav[old_nav.policy.eq("four_fit_delay")].reset_index(drop=True)
    assert nav.date.equals(old_nav.date)
    for key in ("cash", "market_value", "nav", "fees", "drawdown"):
        assert np.allclose(nav[key], old_nav[key], atol=1e-7, rtol=0), key

    trades = ledger.merge(decisions[["date", "symbol", "rank", "activation_end", "entry_end", "label3"]],
                          left_on=["entry_date", "symbol"], right_on=["date", "symbol"],
                          how="left", validate="one_to_one")
    assert trades["rank"].notna().all()
    daily = nav.copy()
    daily["day"] = daily.date.map(day_no)
    daily["month"] = daily.date.str[:7].map(month_no)
    monthly = [{**row, "month_no": month_no[row["month"]]} for row in monthly]
    receipt = dict(status="PASS", source="frozen_B2_four_fit_delay", candidates=len(decisions),
                   days=len(days), minute_prefixes=1302, signals=len(signals),
                   entries=len(ledger), closed=summary["closed"], pending=summary["pending"],
                   stock_time_buy_exact=True, account_daily_exact=True,
                   reference_ledger_sha256=sha(ref / "ledger.pkl"),
                   reference_candidates_sha256=sha(ref / "four_all_top3.pkl"),
                   reference_minutes_sha256=sha(ref / "first10_evidence.pkl"),
                   summary=summary)
    output = dict(receipt=receipt, monthly=monthly,
                  candidates=clean(decisions.to_dict("records")),
                  trades=clean(trades.to_dict("records")),
                  daily=clean(daily.to_dict("records")))
    (args.output / "replay.json").write_text(json.dumps(output, ensure_ascii=False, allow_nan=False))
    (args.output / "receipt.json").write_text(json.dumps(clean(receipt), ensure_ascii=False, indent=2))
    print(json.dumps(clean(receipt), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
