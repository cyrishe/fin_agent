"""Locate >=3 fee-net losses among the first five D2 exits of active R57 months."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

OUT = Path("docs/stock_automl_runs/20261020_b2_first5_losses_r59")
R57 = Path("docs/stock_automl_runs/20261020_b2_sum_loss_r57")
SOURCE = Path("outputs/stock_automl/b2_2022_minute_r41_20261019")
START, END = "2023-05-01", "2026-09-30"


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
        signals, calendar, start=START, end=END,
        initial=1_000_000, fee_rate=.001)
    assert not excluded and account["entries"] == len(ledger) == 253
    saved = pd.read_csv(R57 / "总体.csv").set_index("方案").loc["亏损月全停"]
    np.testing.assert_allclose(account["final_nav"], saved["期末资金"], rtol=0, atol=1e-7)
    assert ledger.exit_date.notna().all()

    summary_rows = []
    detail_rows = []
    month_lookup = months.set_index("月份")
    for month in study:
        first = ledger[ledger.exit_date.str.startswith(month)].sort_values(
            ["exit_date", "entry_date", "symbol"]).head(5)
        assert len(first) == 5
        loss4 = int(first.head(4).return_.lt(0).sum())
        loss5 = int(first.return_.lt(0).sum())
        decision_number = 4 if loss4 >= 3 else (5 if loss5 >= 3 else None)
        decision_date = str(first.iloc[decision_number - 1].exit_date) if decision_number else None
        trading_days = [d for d in calendar if d.startswith(month)]
        day_number = trading_days.index(decision_date) + 1 if decision_date else None
        pattern = "".join("亏" if x < 0 else "盈" for x in first.return_)
        summary_rows.append(dict(
            月份=month, 前五盈亏=pattern, 前四亏数=loss4, 前五亏数=loss5,
            判断笔数=decision_number, 判断日期=decision_date,
            当月第几个交易日=day_number,
            R57账户月收益=float(month_lookup.loc[month, "亏损月全停月收益"])))
        for number, row in enumerate(first.itertuples(), start=1):
            detail_rows.append(dict(
                月份=month, 序号=number, D0=row.d0_date, D1买入=row.entry_date,
                D2卖出=row.exit_date, 股票=row.symbol,
                D2费后收益=float(row.return_)))

    summary = pd.DataFrame(summary_rows)
    detail = pd.DataFrame(detail_rows)
    assert len(detail) == 70
    assert int(summary.判断笔数.notna().sum()) == 6
    assert int(summary.判断笔数.eq(4).sum()) == 3
    assert int(summary.判断笔数.eq(5).sum()) == 3
    assert int(summary.loc[summary.判断笔数.notna(), "R57账户月收益"].lt(0).sum()) == 1
    assert int(summary.loc[summary.判断笔数.isna(), "R57账户月收益"].lt(0).sum()) == 2
    summary.to_csv(OUT / "逐月前五笔.csv", index=False, encoding="utf-8-sig")
    detail.to_csv(OUT / "前五笔逐笔.csv", index=False, encoding="utf-8-sig")
    dump(OUT / "audit.json", dict(
        source_hashes={str(p): sha(p) for p in (month_path, signal_path, calendar_path)},
        R57_full_pause_account_replayed_exact=True,
        active_months=len(study), first_five_trades=len(detail),
        triggered_months=6, fourth_trade_decisions=3, fifth_trade_decisions=3,
        triggered_months_eventually_negative=1,
        decision_requires_D2_close=True))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
