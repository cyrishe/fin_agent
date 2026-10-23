"""R65: verify V2 NAV windows equal compounded daily account returns.

Read-only local audit. This is an equivalent calculation of registered B2_V2,
not a new monthly pause rule.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.automl_b2_v2 import FROZEN_DEFINITION_SHA256, month_decision
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha


SOURCE = Path("outputs/stock_automl/b2_2021_minute_r60_20261020")
R60 = Path("docs/stock_automl_runs/20261020_b2_2021_minutes_r60")
OUT = Path("docs/stock_automl_runs/20261021_b2_daily_compound_r65")


def compounded(daily_returns: pd.Series, days: int) -> float:
    """The anchor 1.0 is the close immediately before the included days."""
    assert len(daily_returns) >= days
    value = 1.0
    for daily_return in daily_returns.iloc[-days:]:
        value *= 1.0 + float(daily_return)
    return value - 1.0


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    daily_path = R60 / "逐日净值.csv"
    month_path = R60 / "逐月.csv"
    signal_path = SOURCE / "signals.pkl"
    calendar_path = SOURCE / "calendar.json"
    daily = pd.read_csv(daily_path).set_index("date")
    months = pd.read_csv(month_path).set_index("月份")
    signals = pd.read_pickle(signal_path)
    calendar = json.loads(calendar_path.read_text())
    calendar_index = {day: i for i, day in enumerate(calendar)}
    expected_dates = [day for day in calendar if "2022-05-01" <= day <= "2026-09-30"]
    assert daily.index.tolist() == expected_dates and len(expected_dates) == 1073
    active_signals = signals[signals.d1_date.between("2022-05-01", "2026-09-30")]
    transaction_dates = set(active_signals.d1_date)
    transaction_dates.update(calendar[calendar_index[day] + 1]
                             for day in active_signals.d1_date)
    counts = signals.d0_date.str[:7].value_counts().to_dict()
    shadow_nav = daily[["原B2净值"]].rename(columns={"原B2净值": "nav"})
    shadow_return = daily["原B2日收益"]
    expected_daily = shadow_nav.nav.div(shadow_nav.nav.shift()).sub(1)
    np.testing.assert_allclose(shadow_return.iloc[1:], expected_daily.iloc[1:],
                               rtol=0, atol=1e-15)

    targets = list(months.index[1:]) + ["2026-10"]
    assert len(targets) == 53 and targets[0] == "2022-06"
    rows = []
    for target in targets:
        prior = str(pd.Period(target, freq="M") - 1)
        prior_candidates = int(counts[prior])
        reference = month_decision(target, prior_candidates, shadow_nav)
        if target in months.index:
            saved = months.loc[target]
            assert int(saved.前月候选) == prior_candidates
            assert bool(saved.月初暂停) == reference["pause"]
        prior_daily = shadow_return[shadow_return.index.str[:7] == prior]
        calendar_days = [day for day in calendar if day.startswith(prior)]
        assert prior_daily.index.tolist() == calendar_days
        last5_dates, last10_dates = calendar_days[-5:], calendar_days[-10:]
        by_daily5 = compounded(prior_daily, 5)
        by_daily10 = compounded(prior_daily, 10)
        # Control: anchoring at the first included close omits its daily return.
        omitted5 = compounded(prior_daily, 4)
        omitted10 = compounded(prior_daily, 9)
        equivalent_pause = bool(prior_candidates == 0 or by_daily5 + by_daily10 < 0)
        omitted_pause = bool(prior_candidates == 0 or omitted5 + omitted10 < 0)
        error5 = abs(by_daily5 - reference["prior_last5_return"])
        error10 = abs(by_daily10 - reference["prior_last10_return"])
        assert error5 < 1e-12 and error10 < 1e-12
        assert equivalent_pause == reference["pause"]
        rows.append(dict(
            目标月=target, 前月=prior, 前月D0候选=prior_candidates,
            净值末5收益=reference["prior_last5_return"],
            日收益复利末5=by_daily5, 末5绝对误差=error5,
            净值末10收益=reference["prior_last10_return"],
            日收益复利末10=by_daily10, 末10绝对误差=error10,
            V2停摆=reference["pause"], 日收益复利停摆=equivalent_pause,
            末5交易日=" ".join(last5_dates), 末10交易日=" ".join(last10_dates),
            末5无买卖日数=sum(day not in transaction_dates for day in last5_dates),
            末10无买卖日数=sum(day not in transaction_dates for day in last10_dates),
            漏首日末5=omitted5, 漏首日末10=omitted10,
            漏首日停摆=omitted_pause,
            漏首日改变决定=omitted_pause != reference["pause"],
        ))
    result = pd.DataFrame(rows)
    assert int(result.漏首日改变决定.sum()) == 5
    sept_last = signals[signals.d0_date.eq("2026-09-30")]
    assert len(sept_last) == 1 and sept_last.iloc[0].d1_date == "2026-10-08"
    _, _, with_last, ledger, excluded = replay(
        signals, calendar, start="2022-05-01", end="2026-09-30",
        initial=1_000_000., fee_rate=.001,
    )
    _, _, without_last, _, excluded_without = replay(
        signals[signals.d0_date.ne("2026-09-30")], calendar,
        start="2022-05-01", end="2026-09-30",
        initial=1_000_000., fee_rate=.001,
    )
    assert not excluded and not excluded_without
    max_month_end_D0_nav_effect = float(np.max(np.abs(with_last.nav - without_last.nav)))
    assert max_month_end_D0_nav_effect == 0
    sept_close = with_last[with_last.date.eq("2026-09-30")].iloc[0]
    sept_sold = ledger[ledger.exit_date.eq("2026-09-30")]
    assert sept_close.buy == 0 and len(sept_sold) == 1
    assert sept_sold.iloc[0].d0_date == "2026-09-28"
    audit = dict(
        status="PASS", strategy_id="B2_V2",
        definition_sha256=FROZEN_DEFINITION_SHA256,
        calculation="For each of the last 5 or 10 trading days, start at 1.0 at the previous trading day's close and multiply (1 + shadow account daily return); subtract 1.",
        compared_target_months=len(result),
        exact_pause_matches=int(result.V2停摆.eq(result.日收益复利停摆).sum()),
        daily_calendar_coverage_days=len(expected_dates),
        months_with_no_transaction_in_last5=int(result.末5无买卖日数.gt(0).sum()),
        months_with_no_transaction_in_last10=int(result.末10无买卖日数.gt(0).sum()),
        september_last_D0=dict(
            D0="2026-09-30", D1="2026-10-08",
            removed_signal_max_nav_effect_through_september=max_month_end_D0_nav_effect,
            september_30_buy=float(sept_close.buy),
            september_30_sold_D0=str(sept_sold.iloc[0].d0_date),
        ),
        max_absolute_error_5=float(result.末5绝对误差.max()),
        max_absolute_error_10=float(result.末10绝对误差.max()),
        omitted_first_day_changed_months=result.loc[
            result.漏首日改变决定, "目标月"].tolist(),
        source_sha256={str(p): sha(p) for p in
                       (signal_path, calendar_path, daily_path, month_path)},
        scope="Equivalent historical calculation only; no database or strategy definition change.",
    )
    result.to_csv(OUT / "逐月复利核对.csv", index=False, encoding="utf-8-sig")
    dump(OUT / "audit.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
