"""Combine new four-K months and original R24 picks into one continuous B2 account."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_b2 import evaluate_morning
from scripts.replay_limit_rebound_cash_r23 import replay, sha
from scripts.report_b2_frozen_full_replay import clean


NEW = Path("outputs/stock_automl/b2_fourk_2024_extension_20261018")
OLD = Path("outputs/stock_automl/limit_rebound_four_minutes_r24_20261015")
CALENDAR = Path("outputs/stock_automl/limit_rebound_segments_r18_20261015/calendar.json")
OUT = Path("outputs/stock_automl/b2_continuous_20261018")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    calendar = json.loads(CALENDAR.read_text())
    position = {day: index for index, day in enumerate(calendar)}
    old_frame = pd.read_pickle(OLD / "four_all_top3.pkl")
    old_frame = old_frame[old_frame.part.eq("test")].copy()
    assert len(old_frame) == 801
    missing_day = old_frame.d1_date.isna()
    old_frame.loc[missing_day, "d1_date"] = old_frame.loc[missing_day, "d0_date"].map(
        lambda day: calendar[position[day] + 1])
    minutes = pd.read_pickle(OLD / "first10_evidence.pkl")
    bars = {(symbol, d0): group for (symbol, d0), group in
            minutes.groupby(["symbol", "d0_date"])}
    rules = {row["month"]: row["selected"] for row in
             json.loads((OLD / "four_rules_frozen.json").read_text())}
    old_decisions = []
    for day, frame in old_frame.groupby("d1_date", sort=True):
        d0 = frame.d0_date.iloc[0]
        picks = [dict(symbol6=row.symbol, selection_rank=int(row.d0_rank),
                      preclose=float(row.d1_preclose), cap=float(row.d1_cap),
                      ordinary=bool(row.eligible)) for row in frame.itertuples()]
        sources = {row.symbol: bars.get((row.symbol, d0), pd.DataFrame())
                   for row in frame.itertuples()}
        states = evaluate_morning(picks, sources, day,
                                  datetime.fromisoformat(f"{day} 09:40:00"), rules[d0[:7]])
        by_symbol = {state["symbol6"]: state for state in states}
        for row in frame.itertuples():
            state = by_symbol[row.symbol]
            feature = state.get("shape_features") or {}
            old_decisions.append(dict(
                d0_date=d0, d1_date=day, symbol=row.symbol, d0_rank=int(row.d0_rank),
                score=float(row.score), open_gap=feature.get("open_gap"),
                body4=feature.get("body5"), shape_pass=bool(state["shape_pass"]),
                chosen=bool(state["chosen"]),
                activation_end=state.get("activation_bar_end", "")[11:16] or None,
                body_pass=feature.get("body5", -1) > -.08 + 1e-12
                if state["chosen"] else None,
                actionable=bool(state["actionable"]),
                entry_end=state.get("entry_bar_end", "")[11:16] or None,
                buy=state.get("entry_price"), reason=state.get("reason"),
                label3=int(row.label_3) if row.observed_3 else None,
            ))
    old_decisions = pd.DataFrame(old_decisions)
    old_picks = pd.read_pickle(OLD / "policy_picks.pkl")
    old_picks = old_picks[old_picks.policy.eq("four_fit_delay")].copy()
    selected = old_decisions[old_decisions.actionable]
    actual = sorted((row.d0_date, row.symbol, row.entry_end, float(row.buy))
                    for row in selected.itertuples())
    expected = sorted((row.d0_date, row.symbol, row.entry_time, float(row.entry))
                      for row in old_picks.itertuples())
    assert len(actual) == len(expected) == 165 and actual == expected

    new_decisions = pd.read_pickle(NEW / "decisions.pkl")
    new_picks = pd.read_pickle(NEW / "signals.pkl")
    decisions = pd.concat([new_decisions, old_decisions], ignore_index=True)
    decisions = decisions.sort_values(["d0_date", "d0_rank"]).reset_index(drop=True)
    picks = pd.concat([new_picks, old_picks], ignore_index=True)
    assert len(decisions) == 1050 and len(picks) == 226, (len(decisions), len(picks))
    assert decisions.d0_date.nunique() == 350
    assert not picks.d1_date.duplicated().any()
    assert decisions.actionable.sum() == len(picks)
    summary, monthly, nav, ledger, excluded = replay(
        picks, calendar, start="2025-05-01", end=calendar[-1], fee_rate=.001)
    assert not excluded and len(ledger) == len(picks)
    for row in ledger[ledger.exit_date.notna()].itertuples():
        assert row.exit_date == calendar[position[row.entry_date] + 1]
        assert np.isclose(row.sell, row.d2_close, atol=1e-12, rtol=0)
        assert np.isclose(row.return_, row.sell / row.buy * .999 / 1.001 - 1,
                          atol=1e-12, rtol=0)
    for row in nav.itertuples():
        bought = ledger[ledger.entry_date.le(row.date)]
        sold = ledger[ledger.exit_date.fillna("9999").le(row.date)]
        held = bought[bought.exit_date.fillna("9999").gt(row.date)]
        cash = 1_000_000 - bought.cost.sum() + sold.proceeds.sum()
        mark = (held.shares * held.d1_close).sum()
        assert np.isclose(row.cash, cash, atol=1e-7, rtol=0)
        assert np.isclose(row.nav, cash + mark, atol=1e-7, rtol=0)

    day_no = {day: index + 1 for index, day in enumerate(nav.date.tolist())}
    months = sorted({day[:7] for day in nav.date})
    month_no = {month: index + 1 for index, month in enumerate(months)}
    decisions["day"] = decisions.d1_date.map(day_no)
    decisions["month"] = decisions.d1_date.str[:7].map(month_no)
    trades = ledger.merge(decisions[["d1_date", "symbol", "activation_end", "entry_end"]],
                          left_on=["entry_date", "symbol"], right_on=["d1_date", "symbol"],
                          validate="one_to_one")
    trades["rank"] = trades.d0_rank
    trades["day"] = trades.entry_date.map(day_no)
    nav["day"] = nav.date.map(day_no)
    nav["month"] = nav.date.str[:7].map(month_no)
    monthly = [{**row, "month_no": month_no[row["month"]]} for row in monthly]
    receipt = dict(status="PASS", scope="continuous four-K simulation from new months through original R24",
                   source_new_signals_sha256=sha(NEW / "signals.pkl"),
                   source_old_picks_sha256=sha(OLD / "policy_picks.pkl"),
                   source_new_rules_sha256=sha(NEW / "four_rules_frozen.json"),
                   source_old_rules_sha256=sha(OLD / "four_rules_frozen.json"),
                   original_old_165_stock_minute_buy_exact=True,
                   all_closed_d2_price_fee_exact=True, daily_cash_checks=len(nav),
                   candidate_rows=len(decisions), candidate_days=decisions.d0_date.nunique(),
                   signals=len(picks), new_signals=len(new_picks), old_signals=len(old_picks),
                   summary=summary)
    output = dict(receipt=receipt, monthly=monthly,
                  candidates=clean(decisions.to_dict("records")),
                  trades=clean(trades.to_dict("records")),
                  daily=clean(nav.to_dict("records")))
    (OUT / "replay.json").write_text(json.dumps(output, ensure_ascii=False, allow_nan=False))
    (OUT / "receipt.json").write_text(json.dumps(clean(receipt), ensure_ascii=False, indent=2))
    print(json.dumps(clean(receipt), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
