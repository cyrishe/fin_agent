"""Compare frozen B2 and its R57 monthly pause on identical D0 cohorts.

Read-only with respect to training, signals and KingdomAI. Writes a local audit.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_b2_v2 import definition as v2_definition, month_decision
from scripts.automl_b2 import digest
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

SOURCE = Path("outputs/stock_automl/b2_2021_minute_r60_20261020")
R60 = Path("docs/stock_automl_runs/20261020_b2_2021_minutes_r60")
RECEIPT = Path("docs/stock_automl_runs/20261018_b2_full_replay/continuous_receipt.json")
OUT = Path("docs/stock_automl_runs/20261020_b2_pause_best_r63")
START = "2025-05-01"
END = "2026-10-14"
SETTLED_END = "2026-10-09"
INITIAL = 1_000_000.
FEE = .001


def account(signals: pd.DataFrame, calendar: list[str], start: str, end: str):
    summary, monthly, nav, ledger, excluded = replay(
        signals, calendar, start=start, end=end, initial=INITIAL, fee_rate=FEE
    )
    assert not excluded and summary["entries"] == len(signals)
    return summary, pd.DataFrame(monthly), nav, ledger


def phase_return(nav: pd.DataFrame, start: str, end: str) -> float:
    block = nav[nav.date.between(start, end)]
    assert len(block)
    first = nav.index[nav.date.eq(block.iloc[0].date)][0]
    prior = INITIAL if first == 0 else float(nav.iloc[first - 1].nav)
    return float(block.iloc[-1].nav / prior - 1)


def comparable_summary(summary: dict) -> dict:
    return {k: summary[k] for k in (
        "start", "end", "market_days", "initial", "fee_rate", "signals",
        "entries", "closed", "pending", "final_nav", "total_return",
        "max_drawdown", "sharpe", "total_fees", "closed_win_rate",
        "closed_mean_return",
    )}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    signals = pd.read_pickle(SOURCE / "signals.pkl")
    calendar = json.loads((SOURCE / "calendar.json").read_text())
    assert not signals.d0_date.duplicated().any()
    signals["model_month"] = signals.d0_date.str[:7]

    saved_months = pd.read_csv(R60 / "逐月.csv").set_index("月份")
    saved_nav = pd.read_csv(R60 / "逐日净值.csv")
    shadow = saved_nav[["date", "原B2净值"]].rename(columns={"原B2净值": "nav"}).set_index("date")
    full_counts = signals.groupby("model_month").size().to_dict()
    checked_existing_gates = 0
    for month, saved in saved_months.iterrows():
        if pd.isna(saved["前月候选"]):
            continue
        prior = str(pd.Period(month) - 1)
        actual = month_decision(month, full_counts[prior], shadow)
        assert int(saved["前月候选"]) == actual["prior_D0_candidates"], month
        assert np.isclose(float(saved["前月末5日"]), actual["prior_last5_return"], atol=1e-12), month
        assert np.isclose(float(saved["前月末10日"]), actual["prior_last10_return"], atol=1e-12), month
        assert bool(saved["月初暂停"]) == actual["pause"], month
        checked_existing_gates += 1
    assert checked_existing_gates == 52
    pause = {}
    gate_rows = []
    for month in sorted(set(signals.model_month)):
        if not "2025-05" <= month <= "2026-10":
            continue
        previous = (pd.Period(month) - 1).strftime("%Y-%m")
        count = full_counts.get(previous, 0)
        decision_row = month_decision(month, count, shadow)
        r5, r10, decision = (decision_row["prior_last5_return"],
                             decision_row["prior_last10_return"],
                             decision_row["pause"])
        if month in saved_months.index:
            row = saved_months.loc[month]
            assert bool(row["月初暂停"]) == decision, month
            assert np.isclose(float(row["前月末5日"]), r5, atol=1e-12), month
            assert np.isclose(float(row["前月末10日"]), r10, atol=1e-12), month
            assert int(row["前月候选"]) == count, month
        pause[month] = decision
        gate_rows.append({**decision_row, "D0_candidates": full_counts.get(month, 0)})
    # R60 stopped at September D1. The October decision uses all 16 September D0s,
    # including the September 30 signal settled in October.
    assert full_counts["2026-09"] == 16
    assert pause["2026-10"] is False

    chosen = signals[signals.model_month.between("2025-05", "2026-10")].copy()
    chosen["pause"] = chosen.model_month.map(pause)
    assert chosen.pause.notna().all() and len(chosen) == 226
    active = chosen[~chosen.pause].copy()
    original, orig_monthly, orig_nav, orig_ledger = account(chosen, calendar, START, END)
    gated, gate_monthly, gate_nav, gate_ledger = account(active, calendar, START, END)

    receipt = json.loads(RECEIPT.read_text())["summary"]
    for key in ("start", "end", "market_days", "signals", "entries", "closed", "pending"):
        assert original[key] == receipt[key], (key, original[key], receipt[key])
    for key in ("final_nav", "total_return", "max_drawdown", "sharpe", "total_fees"):
        assert np.isclose(original[key], receipt[key], rtol=0, atol=1e-8), key
    assert gated["entries"] == 153 and gated["closed"] == 152 and gated["pending"] == 1
    assert len(orig_nav) == len(gate_nav) == 351
    assert orig_ledger.iloc[-1].d0_date == gate_ledger.iloc[-1].d0_date == "2026-10-13"

    month_rows = []
    for month in orig_monthly.month:
        o = orig_monthly.set_index("month").loc[month]
        g = gate_monthly.set_index("month").loc[month]
        month_rows.append(dict(calendar_month=month,
                               D0_candidates=full_counts.get(month, 0),
                               D0_paused=pause.get(month, False),
                               original_entries=int(o.entries), paused_entries=int(g.entries),
                               original_return=float(o.return_), paused_return=float(g.return_),
                               original_closing_nav=float(o.closing_nav),
                               paused_closing_nav=float(g.closing_nav)))

    settled = {}
    for label, first, last in (("strong_D0_months", "2025-05", "2026-09"),
                                ("all_53_D0_months", "2022-05", "2026-09")):
        cohort = signals[signals.model_month.between(first, last)].copy()
        a, _, _, _ = account(cohort, calendar, first + "-01", SETTLED_END)
        b, _, _, _ = account(cohort[~cohort.model_month.map(
            lambda m: pause[m] if m in pause else bool(saved_months.loc[m, "月初暂停"]))],
            calendar, first + "-01", SETTLED_END)
        assert a["pending"] == b["pending"] == 0
        settled[label] = dict(original=comparable_summary(a), paused=comparable_summary(b),
                              D0_months=int(pd.Period(last).ordinal-pd.Period(first).ordinal+1))

    phases = {name: dict(original=phase_return(orig_nav, begin, finish),
                         paused=phase_return(gate_nav, begin, finish))
              for name, begin, finish in (("2025-05_to_2025-12", "2025-05-01", "2025-12-31"),
                                          ("2026-01_to_2026-10-14", "2026-01-01", END))}

    audit = dict(status="PASS", name="涨停冷静 V2 - 月度停摆", basis="R57 monthly gate over frozen B2",
                 v2_strategy_id="B2_V2", v2_definition_sha256=digest(v2_definition()),
                 source_sha256={str(p): sha(p) for p in (
                     SOURCE / "signals.pkl", SOURCE / "calendar.json", R60 / "逐月.csv",
                     R60 / "逐日净值.csv", RECEIPT)},
                 baseline_receipt_reproduced=True, existing_month_gates_reproduced=checked_existing_gates,
                 primary_original=comparable_summary(original),
                 primary_paused=comparable_summary(gated),
                 primary_return_advantage_pp=(gated["total_return"]-original["total_return"])*100,
                 primary_drawdown_reduction_pp=(original["max_drawdown"]-gated["max_drawdown"])*100,
                 primary_paused_D0_months=[m for m, yes in pause.items() if yes],
                 primary_skipped_signals=int(chosen.pause.sum()),
                 october_gate=gate_rows[-1], settled=settled, phases=phases,
                 limitations="Historical research; selected strong period; minute open proxy, no live fill proof.")
    dump(OUT / "audit.json", audit)
    pd.DataFrame(gate_rows).to_csv(OUT / "月初门槛.csv", index=False)
    pd.DataFrame(month_rows).to_csv(OUT / "逐月账户.csv", index=False)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
