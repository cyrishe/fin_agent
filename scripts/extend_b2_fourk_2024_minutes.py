"""Four-K B2 extension into newly testable months, without changing frozen B2."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_b2 import evaluate_morning
from scripts.experiment_limit_rebound_four_minutes_r24 import (
    FEATURES, choose_all, entry_facts, first_features, reprice,
)
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.replay_limit_rebound_cash_r23 import replay, sha


SOURCE = Path("outputs/stock_automl/limit_rebound_2024_minute_extension_20261017")
REFERENCE = Path("outputs/stock_automl/limit_rebound_four_minutes_r24_20261015")
CALENDAR = Path("outputs/stock_automl/limit_rebound_segments_r18_20261015/calendar.json")
OUT = Path("outputs/stock_automl/b2_fourk_2024_extension_20261018")


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def minute_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    unique = frame.drop_duplicates(["symbol", "d0_date"]).copy()
    assert not unique.duplicated(["symbol", "d0_date"]).any()
    by_path = defaultdict(list)
    for row in unique.itertuples(index=False):
        if bool(row.audit_ok):
            by_path[row.minute_source].append(row)
    facts = []
    first10 = {}
    for index, (name, rows) in enumerate(sorted(by_path.items()), 1):
        path = Path(name)
        if not path.exists():
            raise FileNotFoundError(path)
        found = filtered_local(path, {row.symbol for row in rows})
        if not found.empty:
            found["symbol"] = found.symbol.str[:6]
        groups = {symbol: group.sort_values("time").reset_index(drop=True)
                  for symbol, group in found.groupby("symbol")}
        for row in rows:
            bar = groups.get(row.symbol)
            if bar is None or len(bar) != 240:
                raise ValueError(f"Incomplete bars: {row.d0_date} {row.symbol}")
            five = first_features(bar, row.d1_preclose, row.d1_cap, 5)
            for key in FEATURES:
                old = getattr(row, key)
                new = five[key]
                if isinstance(new, str):
                    assert new == old, (row.d0_date, row.symbol, key)
                else:
                    assert np.isclose(new, old, atol=1e-12, rtol=0), (row.d0_date, row.symbol, key)
            four = first_features(bar, row.d1_preclose, row.d1_cap, 4)
            facts.append({"symbol": row.symbol, "d0_date": row.d0_date,
                          **four, **entry_facts(bar, row.d1_cap, 4)})
            first10[(row.symbol, row.d0_date)] = bar.iloc[:10].copy()
        if index % 40 == 0:
            print(f"Four-K minute source days: {index}/{len(by_path)}", flush=True)
    fact_frame = pd.DataFrame(facts)
    drop = [key for key in [*FEATURES, "fill", "entry", "entry_time", "entry_whole_below"]
            if key in frame.columns]
    rebuilt = frame.drop(columns=drop).merge(fact_frame, on=["symbol", "d0_date"],
                                                how="left", validate="many_to_one")
    rebuilt = reprice(rebuilt)
    rebuilt["observation_bars"] = 4
    return rebuilt, first10


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    calendar = json.loads(CALENDAR.read_text())
    old_rules = json.loads((SOURCE / "all_rules_frozen.json").read_text())
    top1 = pd.read_pickle(SOURCE / "enriched_top1.pkl")
    top1_four, _ = minute_features(top1)
    top1_four.to_pickle(OUT / "top1_four.pkl")
    rules = choose_all(top1_four, old_rules, calendar)
    reference_rules = json.loads((REFERENCE / "four_rules_frozen.json").read_text())
    control = next(x for x in rules if x["month"] == "2025-09")
    control_expected = next(x for x in reference_rules if x["month"] == "2025-09")
    if json.loads(json.dumps(control)) != control_expected:
        raise AssertionError("2025-09 four-K training, validation or rule did not reproduce R24")
    control_top3 = pd.read_pickle(REFERENCE / "four_all_top3.pkl")
    control_top3 = control_top3[control_top3.part.eq("test") &
                                control_top3.d0_date.str.startswith("2025-09")]
    control_minutes = pd.read_pickle(REFERENCE / "first10_evidence.pkl")
    control_bars = {(symbol, d0): frame for (symbol, d0), frame in
                    control_minutes.groupby(["symbol", "d0_date"])}
    control_trades = []
    for day, group in control_top3.groupby("d1_date", sort=True):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=row.symbol, selection_rank=int(row.d0_rank),
                      preclose=float(row.d1_preclose), cap=float(row.d1_cap),
                      ordinary=bool(row.eligible)) for row in group.itertuples()]
        sources = {row.symbol: control_bars.get((row.symbol, d0), pd.DataFrame())
                   for row in group.itertuples()}
        for state in evaluate_morning(picks, sources, day,
                                      datetime.fromisoformat(f"{day} 09:40:00"), control["selected"]):
            if state["actionable"]:
                control_trades.append((d0, state["symbol6"], state["entry_bar_end"][11:16],
                                       float(state["entry_price"])))
    reference_picks = pd.read_pickle(REFERENCE / "policy_picks.pkl")
    reference_picks = reference_picks[reference_picks.policy.eq("four_fit_delay") &
                                      reference_picks.d0_date.str.startswith("2025-09")]
    reference_trades = [(row.d0_date, row.symbol, row.entry_time, float(row.entry))
                        for row in reference_picks.itertuples()]
    if sorted(control_trades) != sorted(reference_trades):
        raise AssertionError("2025-09 exact four-K stock, minute and buy price failed to reproduce")
    save(OUT / "four_rules_frozen.json", rules)
    print("Four-K monthly rules:",
          [(x["month"], x["selected"]["id"]) for x in rules], flush=True)

    top3 = pd.read_pickle(SOURCE / "new_month_candidates.pkl")
    top3_four, bars = minute_features(top3)
    top3_four.to_pickle(OUT / "top3_four.pkl")
    month_rules = {x["month"]: x["selected"] for x in rules}
    decision_rows = []
    signal_rows = []
    for day, group in top3_four.groupby("d1_date", sort=True):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=row.symbol, selection_rank=int(row.d0_rank),
                      preclose=float(row.d1_preclose), cap=float(row.d1_cap),
                      ordinary=bool(row.eligible)) for row in group.itertuples()]
        sources = {row.symbol: bars.get((row.symbol, d0), pd.DataFrame()) for row in group.itertuples()}
        states = evaluate_morning(picks, sources, day,
                                  datetime.fromisoformat(f"{day} 09:40:00"), month_rules[d0[:7]])
        by_symbol = {row["symbol6"]: row for row in states}
        for row in group.itertuples():
            state = by_symbol[row.symbol]
            f = state.get("shape_features") or {}
            decision_rows.append({
                "d0_date": d0, "d1_date": day, "symbol": row.symbol,
                "d0_rank": int(row.d0_rank), "score": float(row.score),
                "open_gap": f.get("open_gap"), "body4": f.get("body5"),
                "shape_pass": bool(state["shape_pass"]), "chosen": bool(state["chosen"]),
                "activation_end": state.get("activation_bar_end", "")[11:16] or None,
                "body_pass": f.get("body5", -1) > -.08 + 1e-12 if state["chosen"] else None,
                "actionable": bool(state["actionable"]),
                "entry_end": state.get("entry_bar_end", "")[11:16] or None,
                "buy": state.get("entry_price"), "reason": state.get("reason"),
                "label3": int(row.label_3) if row.observed_3 else None,
            })
            if state["actionable"]:
                record = row._asdict()
                record["entry"] = float(state["entry_price"])
                record["entry_time"] = state["entry_bar_end"][11:16]
                record["fill"] = True
                signal_rows.append(record)
    decisions = pd.DataFrame(decision_rows).sort_values(["d0_date", "d0_rank"])
    signals = reprice(pd.DataFrame(signal_rows).sort_values("d0_date"))
    assert len(decisions) == 249 and not signals.d1_date.duplicated().any()
    assert decisions.actionable.sum() == len(signals)
    decisions.to_pickle(OUT / "decisions.pkl")
    signals.to_pickle(OUT / "signals.pkl")
    summary, monthly, nav, ledger, excluded = replay(
        signals, calendar, start="2025-05-01", end="2025-09-05", fee_rate=.001)
    if excluded:
        raise AssertionError(f"Excluded signals require manual examination: {excluded}")
    position = {day: index for index, day in enumerate(calendar)}
    for row in ledger.itertuples():
        assert row.exit_date == calendar[position[row.entry_date] + 1]
        assert np.isclose(row.sell, row.d2_close, atol=1e-12, rtol=0)
        assert np.isclose(row.return_, row.sell / row.buy * .999 / 1.001 - 1,
                          atol=1e-12, rtol=0)
    for row in nav.itertuples():
        bought = ledger[ledger.entry_date.le(row.date)]
        sold = ledger[ledger.exit_date.le(row.date)]
        held = bought[bought.exit_date.gt(row.date)]
        cash = 1_000_000 - bought.cost.sum() + sold.proceeds.sum()
        marked = (held.shares * held.d1_close).sum()
        assert np.isclose(row.cash, cash, atol=1e-7, rtol=0)
        assert np.isclose(row.nav, cash + marked, atol=1e-7, rtol=0)
    nav.to_pickle(OUT / "daily_nav.pkl")
    ledger.to_pickle(OUT / "ledger.pkl")
    save(OUT / "summary.json", summary)
    save(OUT / "monthly.json", monthly)
    receipt = {
        "status": "PASS", "scope": "new 2025-05..08 four-K monthly extension, independent of frozen B2 baseline",
        "candidate_rows": len(decisions), "days": decisions.d0_date.nunique(),
        "shape_pass_rows": int(decisions.shape_pass.sum()),
        "locked_days": int(decisions.chosen.sum()),
        "body_reject_days": int((decisions.chosen & decisions.body_pass.eq(False)).sum()),
        "signals": len(signals), "account_entries": len(ledger),
        "independent_daily_cash_checks": len(nav),
        "control_2025_09_rule_id": control["selected"]["id"],
        "control_matches_original_R24": True,
        "control_2025_09_trade_count": len(control_trades),
        "control_stock_minute_buy_exact": True,
        "source_top1_sha256": sha(SOURCE / "enriched_top1.pkl"),
        "source_top3_sha256": sha(SOURCE / "new_month_candidates.pkl"),
        "source_calendar_sha256": sha(CALENDAR),
        "rules_sha256": sha(OUT / "four_rules_frozen.json"),
        "summary": summary,
    }
    save(OUT / "receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
