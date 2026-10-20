"""Find realized three-loss runs in active months of the frozen R57 B2 account."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

OUT = Path("docs/stock_automl_runs/20261020_b2_active_loss_streak_r58")
R57 = Path("docs/stock_automl_runs/20261020_b2_sum_loss_r57")
SOURCE = Path("outputs/stock_automl/b2_2022_minute_r41_20261019")
START, END = "2023-05-01", "2026-09-30"


def runs_of_losses(frame: pd.DataFrame) -> list[pd.DataFrame]:
    ordered = frame.sort_values(["exit_date", "entry_date", "symbol"])
    groups: list[pd.DataFrame] = []
    current = []
    for idx, row in ordered.iterrows():
        if row.return_ < 0:
            current.append(idx)
        elif current:
            groups.append(ordered.loc[current])
            current = []
    if current:
        groups.append(ordered.loc[current])
    return groups


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    month_path = R57 / "逐月.csv"
    signal_path = SOURCE / "signals.pkl"
    calendar_path = SOURCE / "calendar.json"
    months = pd.read_csv(month_path)
    active = months.loc[~months.月初暂停, "月份"].tolist()
    study = [month for month in active if "2024-03" <= month <= "2026-09"]
    assert len(study) == 14

    signals = pd.read_pickle(signal_path)
    signals = signals[signals.d1_date.between(START, END) &
                      signals.d0_date.str[:7].isin(active)]
    calendar = json.loads(calendar_path.read_text())
    account, _, _, ledger, excluded = replay(
        signals, calendar, start=START, end=END, initial=1_000_000, fee_rate=.001)
    assert not excluded and account["entries"] == len(ledger) == 253
    saved = pd.read_csv(R57 / "总体.csv").set_index("方案").loc["亏损月全停"]
    np.testing.assert_allclose(account["final_nav"], saved["期末资金"], rtol=0, atol=1e-7)
    assert ledger.exit_date.notna().all()

    monthly_rows = []
    streak_rows = []
    monthly_lookup = months.set_index("月份")
    for month in study:
        closed = ledger[ledger.exit_date.str.startswith(month)]
        streaks = [part for part in runs_of_losses(closed) if len(part) >= 3]
        first = streaks[0] if streaks else None
        monthly_rows.append(dict(
            月份=month, 已卖笔数=len(closed), 亏损笔数=int(closed.return_.lt(0).sum()),
            三连亏段数=len(streaks), 最长连亏=max(
                (len(part) for part in runs_of_losses(closed)), default=0),
            首次第三亏确认日=first.iloc[2].exit_date if first is not None else None,
            策略月收益=float(monthly_lookup.loc[month, "亏损月全停月收益"])))
        for number, part in enumerate(streaks, start=1):
            for sequence, item in enumerate(part.itertuples(), start=1):
                streak_rows.append(dict(
                    月份=month, 段号=number, 段内序号=sequence, 连亏总长=len(part),
                    D0=item.d0_date, D1=item.entry_date, D2卖出=item.exit_date,
                    股票=item.symbol, D2费后收益=float(item.return_),
                    账户盈亏额=float(item.pnl)))
    summary = pd.DataFrame(monthly_rows)
    detail = pd.DataFrame(streak_rows)
    assert int(summary.三连亏段数.gt(0).sum()) == 9
    assert int(summary.三连亏段数.sum()) == 11
    assert int(summary.loc[summary.三连亏段数.gt(0), "策略月收益"].lt(0).sum()) == 3
    assert int(summary.loc[summary.三连亏段数.eq(0), "策略月收益"].lt(0).sum()) == 0
    assert detail.D2费后收益.lt(0).all()

    # D0 cohort attribution is useful for historical tables, but a loss becomes
    # observable only on D2. April 2026 differs because its third exit is May 6.
    d0_triple_months = []
    for month in study:
        cohort = ledger[ledger.d0_date.str.startswith(month)]
        if any(len(part) >= 3 for part in runs_of_losses(cohort)):
            d0_triple_months.append(month)
    assert len(d0_triple_months) == 10
    assert set(d0_triple_months) - set(summary.loc[summary.三连亏段数.gt(0), "月份"]) == {
        "2026-04"}
    all_active_triple = sum(
        any(len(part) >= 3 for part in runs_of_losses(
            ledger[ledger.exit_date.str.startswith(month)]))
        for month in active)
    assert len(active) == 19 and all_active_triple == 12

    summary.to_csv(OUT / "未停摆月三连亏.csv", index=False, encoding="utf-8-sig")
    detail.to_csv(OUT / "三连亏逐笔.csv", index=False, encoding="utf-8-sig")
    dump(OUT / "audit.json", dict(
        source_hashes={str(p): sha(p) for p in (month_path, signal_path, calendar_path)},
        R57_full_pause_account_replayed_exact=True,
        active_months=len(study), months_with_realized_triple=9,
        realized_streak_segments=11, d0_cohort_months_with_triple=len(d0_triple_months),
        full_history_active_months=len(active), full_history_realized_triple=all_active_triple,
        d0_only_month="2026-04", no_future_information_in_D2_trigger=True))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
