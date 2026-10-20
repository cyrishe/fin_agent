"""Frozen monthly B2; pause on prior-month last 5/10-day losses, then optionally resume."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

SOURCE = Path("outputs/stock_automl/b2_2022_minute_r41_20261019")
REFERENCE = Path("docs/stock_automl_runs/20261019_b2_2022_minutes_r41/summary.json")
OUT = Path("docs/stock_automl_runs/20261020_b2_recent_loss_resume_r55")
START, END = "2023-05-01", "2026-09-30"
FEE = .001
INITIAL = 1_000_000.


def prior_return(nav: pd.DataFrame, previous_month: str, n: int) -> float | None:
    dates = nav.index[nav.index.str.startswith(previous_month)].tolist()
    if len(dates) < n:
        return None
    start = nav.index.get_loc(dates[-n])
    if start == 0:
        return None
    return float(nav.loc[dates[-1], "nav"] / nav.iloc[start - 1].nav - 1)


def should_pause(previous_candidates: int | None, r5: float | None,
                 r10: float | None) -> bool:
    return bool(previous_candidates is not None and previous_candidates > 0 and
                r5 is not None and r10 is not None and r5 < 0 and r10 < 0)


def first_shadow_win_cross(ledger: pd.DataFrame, month: str) -> dict | None:
    done = ledger[ledger.d0_date.str.startswith(month) &
                  ledger.exit_date.fillna("").str.startswith(month)].sort_values("exit_date")
    wins = 0
    for index, row in enumerate(done.itertuples(), start=1):
        wins += int(row.return_ > 0)
        if wins / index > .5:
            return dict(触发卖出日=row.exit_date, 触发D0=row.d0_date,
                        触发股票=row.symbol, 触发费后收益=float(row.return_),
                        触发时已卖=index, 触发时胜率=wins / index)
    return None


def main() -> None:
    assert not should_pause(0, -.01, -.02)
    assert not should_pause(None, -.01, -.02)
    assert not should_pause(1, 0, -.02)
    assert should_pause(1, -.01, -.02)
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [SOURCE / "signals.pkl", SOURCE / "calendar.json", REFERENCE]
    frame = pd.read_pickle(paths[0]); calendar = json.loads(paths[1].read_text())
    signals = frame[frame.d1_date.between(START, END)].sort_values("d1_date").copy()
    assert len(signals) == 543 and signals.d1_date.is_unique
    base, base_monthly, paper_nav, paper_ledger, excluded = replay(
        signals, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    assert not excluded and base["entries"] == len(signals) and base["pending"] == 0
    saved = json.loads(REFERENCE.read_text())["baseline"]
    np.testing.assert_allclose(base["final_nav"], saved["final_nav"], rtol=0, atol=1e-7)
    np.testing.assert_allclose(base["max_drawdown"], saved["max_drawdown"], rtol=0, atol=1e-12)
    assert base["signals"] == saved["signals"] == 543
    paper_nav = paper_nav.set_index("date").sort_index()
    paper_ledger = paper_ledger.copy()
    months = sorted(set(paper_nav.index.str[:7]))
    shadow_candidate_count = signals.d0_date.str[:7].value_counts().to_dict()
    month_gate = {}
    for i, month in enumerate(months):
        prev = months[i - 1] if i else None
        previous_candidates = shadow_candidate_count.get(prev, 0) if prev else None
        r5 = prior_return(paper_nav, prev, 5) if prev else None
        r10 = prior_return(paper_nav, prev, 10) if prev else None
        pause = should_pause(previous_candidates, r5, r10)
        cross = first_shadow_win_cross(paper_ledger, month)
        month_gate[month] = dict(前月=prev, 前月候选数=previous_candidates,
                                 前月末5日=r5, 前月末10日=r10,
                                 月初暂停=bool(pause), **(cross or {}))
    rows = []
    for row in signals.itertuples():
        # The user's "test month" is the D0 selection/model month, even if
        # the last D0 of a month is actionable on D1 in the next month.
        month = row.d0_date[:7]
        gate = month_gate[month]
        cross_day = gate.get("触发卖出日")
        prior_closed = paper_ledger[paper_ledger.d0_date.str.startswith(month) &
                                    paper_ledger.exit_date.fillna("").lt(row.d1_date) &
                                    paper_ledger.exit_date.notna()]
        prior_n = len(prior_closed)
        prior_win = float(prior_closed.return_.gt(0).mean()) if prior_n else None
        resume = not gate["月初暂停"] or (cross_day is not None and cross_day < row.d1_date)
        full_pause = not gate["月初暂停"]
        rows.append(dict(选股月=month, D0=row.d0_date, D1=row.d1_date, 股票=row.symbol,
                         排名=int(row.d0_rank), 日线分=float(row.score), 四K规则=row.rule_id,
                         买入时刻=row.entry_time, 买价=float(row.entry), D2收盘=float(row.d2_close),
                         影子D2费后收益=float(row.d2_close / row.entry * (1-FEE) / (1+FEE) - 1),
                         前月末5日=gate["前月末5日"], 前月末10日=gate["前月末10日"],
                         月初暂停=int(gate["月初暂停"]), 当月影子已卖=prior_n,
                         当月影子胜率=prior_win, 胜率触发日=cross_day,
                         全月暂停买入=int(full_pause), 胜率恢复买入=int(resume)))
    decisions = pd.DataFrame(rows)
    assert len(decisions) == len(signals)
    assert decisions.loc[decisions.全月暂停买入.eq(1), "胜率恢复买入"].eq(1).all()
    assert decisions.loc[decisions.月初暂停.eq(0), ["全月暂停买入", "胜率恢复买入"]].eq(1).all().all()
    resumed = decisions[decisions.月初暂停.eq(1) & decisions.胜率恢复买入.eq(1)]
    assert resumed.apply(lambda r: r.胜率触发日 < r.D1, axis=1).all()
    variants = {"原B2": (base, base_monthly, paper_nav.reset_index(), paper_ledger),}
    for name, column in (("亏损月全停", "全月暂停买入"), ("亏损月胜率恢复", "胜率恢复买入")):
        chosen = signals[signals.d1_date.isin(decisions.loc[decisions[column].eq(1), "D1"])]
        account, monthly, nav, ledger, rejected = replay(
            chosen, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
        assert not rejected and account["entries"] == len(chosen) and account["pending"] == 0
        variants[name] = (account, monthly, nav, ledger)
    result_rows = []
    for name, (summary, _, _, ledger) in variants.items():
        result_rows.append(dict(方案=name, 信号=len(signals), 买入=summary["entries"],
                                跳过=len(signals)-summary["entries"], 总收益=summary["total_return"],
                                最大回撤=summary["max_drawdown"], 年化夏普=summary["sharpe"],
                                D2费后胜率=summary["closed_win_rate"],
                                D2费后笔均=summary["closed_mean_return"],
                                期末资金=summary["final_nav"], 交易费=summary["total_fees"]))
        by_day = ledger.set_index("entry_date") if len(ledger) else ledger
        decisions[name+"投入"] = decisions.D1.map(by_day.cost) if len(ledger) else np.nan
        decisions[name+"盈亏额"] = decisions.D1.map(by_day.pnl) if len(ledger) else np.nan
    results = pd.DataFrame(result_rows)
    monthly_rows = []
    for month in months:
        row = dict(月份=month, **month_gate[month], 原B2候选=int(decisions.选股月.eq(month).sum()))
        for name, (_, monthly, _, _) in variants.items():
            item = next(x for x in monthly if x["month"] == month)
            row.update({name+"买入":item["entries"], name+"月收益":item["return_"],
                        name+"月回撤":item["drawdown"], name+"月费用":item["fees"]})
        monthly_rows.append(row)
    monthly_df = pd.DataFrame(monthly_rows)
    yearly_rows = []
    for name, (_, _, nav, ledger) in variants.items():
        nav = nav.copy()
        for year, q in nav.groupby(nav.date.str[:4], sort=True):
            first_idx = q.index[0]
            opening = INITIAL if first_idx == 0 else float(nav.loc[first_idx-1, "nav"])
            values = np.r_[opening, q.nav.to_numpy(dtype=float)]
            buys = int(ledger.entry_date.str.startswith(year).sum()) if len(ledger) else 0
            yearly_rows.append(dict(方案=name, 年份=year, 买入=buys,
                                    收益=float(values[-1] / values[0] - 1),
                                    最大回撤=float(np.max(1-values/np.maximum.accumulate(values)))))
    yearly = pd.DataFrame(yearly_rows)
    start_rows = []
    for start in ("2023-05-01", "2024-03-01", "2025-05-01", "2026-01-01"):
        for name, column in (("原B2", None), ("亏损月全停", "全月暂停买入"),
                             ("亏损月胜率恢复", "胜率恢复买入")):
            chosen = signals if column is None else signals[
                signals.d1_date.isin(decisions.loc[decisions[column].eq(1), "D1"])]
            account, _, _, _, rejected = replay(
                chosen, calendar, start=start, end=END, initial=INITIAL, fee_rate=FEE)
            assert not rejected
            start_rows.append(dict(起点=start[:7], 方案=name, 买入=account["entries"],
                                   总收益=account["total_return"],
                                   最大回撤=account["max_drawdown"], 年化夏普=account["sharpe"]))
    starts = pd.DataFrame(start_rows)
    assert len(monthly_df) == len(months) == 41
    assert monthly_df.前月候选数.dropna().gt(0).all()
    assert not monthly_df.loc[monthly_df.前月候选数.fillna(0).eq(0), "月初暂停"].any()
    assert np.isclose(np.prod(1 + monthly_df["原B2月收益"]) - 1, base["total_return"])
    for name in variants:
        assert np.isclose(np.prod(1 + monthly_df[name+"月收益"]) - 1,
                          variants[name][0]["total_return"])
    assert decisions.全月暂停买入.sum() <= decisions.胜率恢复买入.sum() <= len(signals)
    results.to_csv(OUT / "总体.csv", index=False, encoding="utf-8-sig")
    monthly_df.to_csv(OUT / "逐月.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(OUT / "逐年.csv", index=False, encoding="utf-8-sig")
    starts.to_csv(OUT / "不同起点.csv", index=False, encoding="utf-8-sig")
    decisions.to_csv(OUT / "逐笔候选.csv", index=False, encoding="utf-8-sig")
    dump(OUT / "sources.json", [{"file": str(path), "sha256": sha(path)} for path in paths])
    dump(OUT / "audit.json", dict(source_signals=sha(paths[0]), baseline_replayed_exact=True,
                                   months=len(months), shadow_signals=len(signals),
                                   gate_months=int(monthly_df.月初暂停.sum()),
                                   previous_month_zero_candidate_never_pauses=True,
                                   previous_month_zero_candidate_unit_checked=True,
                                   previous_month_zero_candidate_observed=int(
                                       monthly_df.前月候选数.fillna(-1).eq(0).sum()),
                                   previous_month_candidate_min=int(monthly_df.前月候选数.dropna().min()),
                                   no_buy_on_trigger_trade=True,
                                   all_resumed_buys_after_trigger_close=True,
                                   baseline_superset_resume_superset_full_pause=True,
                                   fee_rate=FEE))
    print(results.to_string(index=False))
    print("\nSTARTS")
    print(starts.to_string(index=False))
    print("\nPAUSED MONTHS")
    print(monthly_df[monthly_df.月初暂停][["月份", "前月末5日", "前月末10日", "原B2月收益",
                                            "亏损月全停月收益", "亏损月胜率恢复月收益", "原B2买入",
                                            "亏损月全停买入", "亏损月胜率恢复买入", "触发卖出日"]].to_string(index=False))


if __name__ == "__main__":
    main()
