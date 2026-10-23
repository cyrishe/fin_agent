"""R64: compare frozen NAV gate with a realized per-trade return sum.

Local, read-only research. The alternative is not the registered B2_V2 rule.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha


SOURCE = Path("outputs/stock_automl/b2_2021_minute_r60_20261020")
R60 = Path("docs/stock_automl_runs/20261020_b2_2021_minutes_r60")
OUT = Path("docs/stock_automl_runs/20261021_b2_trade_sum_gate_r64")
INITIAL = 1_000_000.
FEE = .001


def account(frame: pd.DataFrame, calendar: list[str], start: str) -> dict:
    result, _, daily, ledger, excluded = replay(
        frame, calendar, start=start, end="2026-10-09", initial=INITIAL, fee_rate=FEE
    )
    assert not excluded and result["entries"] == result["closed"] == len(frame)
    assert result["pending"] == 0
    out = {key: result[key] for key in (
        "entries", "closed", "final_nav", "total_return", "max_drawdown", "sharpe"
    )}
    opening = INITIAL
    out["yearly"] = []
    for year, days in daily.groupby(daily.date.str[:4], sort=True):
        closing = float(days.nav.iloc[-1])
        out["yearly"].append(dict(year=year, opening_nav=opening,
                                  closing_nav=closing, return_=closing / opening - 1,
                                  entries=int(ledger.entry_date.str.startswith(year).sum())))
        opening = closing
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = SOURCE / "signals.pkl"
    calendar_path = SOURCE / "calendar.json"
    month_path = R60 / "逐月.csv"
    nav_path = R60 / "逐日净值.csv"
    calendar = json.loads(calendar_path.read_text())
    day_index = {day: i for i, day in enumerate(calendar)}
    signals = pd.read_pickle(source)
    signals = signals[signals.d0_date.str[:7].between("2022-05", "2026-09")].copy()
    assert len(signals) == 709 and signals.d0_date.is_unique
    signals["model_month"] = signals.d0_date.str[:7]
    signals["d2_date"] = signals.d1_date.map(lambda day: calendar[day_index[day] + 1])
    signals["fee_net_return"] = signals.d2_close * (1 - FEE) / (
        signals.entry * (1 + FEE)) - 1
    assert signals.d2_date.le("2026-10-09").all()
    assert np.isfinite(signals.fee_net_return.to_numpy()).all()

    months = pd.read_csv(month_path)
    nav = pd.read_csv(nav_path)
    assert len(months) == 53 and len(nav.date) == len(set(nav.date))
    assert months.月份.iloc[0] == "2022-05" and months.月份.iloc[-1] == "2026-09"
    counts = signals.model_month.value_counts().to_dict()
    rows = []
    trade_pause = {"2022-05": False}  # no preceding model month or gate history
    for month in months.月份.iloc[1:]:
        row = months.set_index("月份").loc[month]
        prior = str(pd.Period(month) - 1)
        prior_days = nav.loc[nav.date.str[:7].eq(prior), "date"].tolist()
        assert len(prior_days) >= 11 and int(row.前月候选) == counts.get(prior, 0)
        last5, last10 = set(prior_days[-5:]), set(prior_days[-10:])
        sold5 = signals[signals.d2_date.isin(last5)]
        sold10 = signals[signals.d2_date.isin(last10)]
        sum5 = float(sold5.fee_net_return.sum())
        sum10 = float(sold10.fee_net_return.sum())
        alternative = bool(sum5 + sum10 < 0 or counts.get(prior, 0) == 0)
        frozen = bool(row.月初暂停)
        trade_pause[month] = alternative
        rows.append(dict(
            目标月=month, 前月=prior, 前月D0候选=int(counts.get(prior, 0)),
            V2末5净值收益=float(row.前月末5日), V2末10净值收益=float(row.前月末10日),
            V2停摆=frozen, 末5已卖笔数=len(sold5), 末5逐笔费后收益和=sum5,
            末10已卖笔数=len(sold10), 末10逐笔费后收益和=sum10,
            逐笔收益和停摆=alternative, 决定不同=frozen != alternative,
        ))
    comparison = pd.DataFrame(rows)
    assert len(comparison) == 52 and len(trade_pause) == 53
    # A different gate changes seven already observed monthly decisions.
    assert int(comparison.决定不同.sum()) == 7

    comparison["V2两窗收益和"] = comparison.V2末5净值收益 + comparison.V2末10净值收益
    comparison["逐笔两窗收益和"] = (comparison.末5逐笔费后收益和
                              + comparison.末10逐笔费后收益和)
    association = dict(
        pearson=float(comparison[["V2两窗收益和", "逐笔两窗收益和"]].corr().iloc[0, 1]),
        spearman=float(comparison[["V2两窗收益和", "逐笔两窗收益和"]]
                       .corr(method="spearman").iloc[0, 1]),
        same_pause=int((~comparison.决定不同).sum()),
    )

    shadow_summary, _, shadow_nav, shadow_ledger, excluded = replay(
        signals, calendar, start="2022-05-01", end="2026-09-30",
        initial=INITIAL, fee_rate=FEE,
    )
    assert not excluded and shadow_summary["entries"] == 708
    saved_nav = pd.read_csv(nav_path)
    assert shadow_nav.date.tolist() == saved_nav.date.tolist()
    np.testing.assert_allclose(shadow_nav.nav, saved_nav["原B2净值"], rtol=0, atol=1e-8)
    attribution = []
    for row in comparison.itertuples():
        prior_days = shadow_nav.loc[
            shadow_nav.date.str[:7].eq(row.前月), "date"
        ].tolist()
        combined = dict(目标月=row.目标月, 前月=row.前月,
                        V2两窗收益和=row.V2两窗收益和,
                        逐笔两窗收益和=row.逐笔两窗收益和)
        for width in (5, 10):
            days = prior_days[-width:]
            start = shadow_nav.loc[shadow_nav.date.eq(prior_days[-width - 1])].iloc[0]
            end = shadow_nav.loc[shadow_nav.date.eq(prior_days[-1])].iloc[0]
            sold = shadow_ledger[shadow_ledger.exit_date.isin(days)]
            realized = float(sold.pnl.sum() / start.nav)
            mark_change = float((end.unrealized - start.unrealized) / start.nav)
            actual = float(end.nav / start.nav - 1)
            assert np.isclose(realized + mark_change, actual, atol=1e-12)
            expected = getattr(row, f"V2末{width}净值收益")
            trade_sum = getattr(row, f"末{width}逐笔费后收益和")
            assert np.isclose(actual, expected, atol=1e-12)
            assert np.isclose(float(sold.return_.sum()), trade_sum, atol=1e-12)
            combined[f"末{width}已卖资金加权贡献"] = realized
            combined[f"末{width}未平仓估值变化贡献"] = mark_change
            combined[f"末{width}逐日账户收益简单和"] = float(
                shadow_nav.loc[shadow_nav.date.isin(days), "return_"].sum()
            )
        combined["两窗已卖资金加权贡献"] = (
            combined["末5已卖资金加权贡献"] + combined["末10已卖资金加权贡献"]
        )
        combined["两窗未平仓估值变化贡献"] = (
            combined["末5未平仓估值变化贡献"] + combined["末10未平仓估值变化贡献"]
        )
        combined["两窗逐日账户收益简单和"] = (
            combined["末5逐日账户收益简单和"] + combined["末10逐日账户收益简单和"]
        )
        assert np.isclose(combined["两窗已卖资金加权贡献"]
                          + combined["两窗未平仓估值变化贡献"],
                          row.V2两窗收益和, atol=1e-12)
        month_end = prior_days[-1]
        open_lots = shadow_ledger[
            shadow_ledger.entry_date.le(month_end)
            & shadow_ledger.exit_date.gt(month_end)
        ]
        assert len(open_lots) <= 1
        combined["月末未卖D0"] = str(open_lots.iloc[0].d0_date) if len(open_lots) else None
        combined["月末未卖D1"] = str(open_lots.iloc[0].entry_date) if len(open_lots) else None
        combined["月末未卖浮盈亏元"] = float(end.unrealized)
        attribution.append(combined)
    attribution = pd.DataFrame(attribution)
    decomposition = dict(
        frozen_shadow_nav_replayed_exact=True,
        weighted_realized_only_different_months=int((
            attribution.两窗已卖资金加权贡献.lt(0)
            != attribution.V2两窗收益和.lt(0)).sum()),
        simple_daily_sum_different_months=int((
            attribution.两窗逐日账户收益简单和.lt(0)
            != attribution.V2两窗收益和.lt(0)).sum()),
        flipped_months_with_month_end_position=int(attribution.loc[
            comparison.决定不同, "月末未卖D0"].notna().sum()),
    )
    flip_cohorts = []
    for month in comparison.loc[comparison.决定不同, "目标月"]:
        cohort = signals[signals.model_month.eq(month)]
        last_exit = calendar[day_index[str(cohort.d1_date.max())] + 1]
        summary, _, _, ledger, excluded = replay(
            cohort, calendar, start=month + "-01", end=last_exit,
            initial=INITIAL, fee_rate=FEE,
        )
        assert not excluded and summary["closed"] == len(cohort)
        flip_cohorts.append(dict(month=month, D0_signals=len(cohort),
                                 independent_month_cohort_return=summary["total_return"],
                                 mean_fee_net_trade_return=float(ledger.return_.mean())))

    frozen_pause = months.set_index("月份").月初暂停.to_dict()
    accounts = {}
    for label, first in (("全53个D0月", "2022-05"),
                         ("2025-05至2026-09的D0月", "2025-05")):
        cohort = signals[signals.model_month.ge(first)]
        accounts[label] = dict(
            原B2=account(cohort, calendar, first + "-01"),
            V2净值门槛=account(cohort[~cohort.model_month.map(frozen_pause)],
                             calendar, first + "-01"),
            逐笔收益和门槛=account(cohort[~cohort.model_month.map(trade_pause)],
                               calendar, first + "-01"),
        )

    audit = dict(
        status="PASS", experiment="R64 逐笔收益和与冻结V2净值门槛对照",
        alternative="各取前月末5/10个交易日内D2卖出的原B2影子交易，分别等权加总单笔双边费后收益；两和之和<0或上月D0零候选则停摆",
        alternative_is_registered_v2=False,
        compared_months=52, different_months=int(comparison.决定不同.sum()),
        association=association, decomposition=decomposition,
        flipped_D0_cohorts=flip_cohorts,
        different_decisions=comparison.loc[comparison.决定不同,
            ["目标月", "V2停摆", "逐笔收益和停摆"]].to_dict("records"),
        accounts=accounts,
        source_sha256={str(p): sha(p) for p in
                       (source, calendar_path, month_path, nav_path)},
        limitation="已见历史；逐笔收益和只计窗口内卖出，不计未卖持仓估值、资金权重和复利。",
    )
    comparison.to_csv(OUT / "月初门槛对照.csv", index=False, encoding="utf-8-sig")
    attribution.to_csv(OUT / "门槛差异归因.csv", index=False, encoding="utf-8-sig")
    dump(OUT / "audit.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
