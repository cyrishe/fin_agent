"""R60: original B2 plus the frozen R57 prior-month 5/10-day sum gate."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_recent_loss_resume_r55 import prior_return, should_pause
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha

SOURCE = Path('outputs/stock_automl/b2_2021_minute_r60_20261020')
R41 = Path('outputs/stock_automl/b2_2022_minute_r41_20261019')
R57 = Path('docs/stock_automl_runs/20261020_b2_sum_loss_r57')
OUT = Path('docs/stock_automl_runs/20261020_b2_2021_minutes_r60')
START, END = '2022-05-01', '2026-09-30'
INITIAL, FEE = 1_000_000., .001


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.read_pickle(SOURCE/'signals.pkl')
    calendar = json.loads((SOURCE/'calendar.json').read_text())
    signals = frame[frame.d1_date.between(START, END)].sort_values('d1_date').copy()
    old = pd.read_pickle(R41/'signals.pkl')
    pd.testing.assert_frame_equal(
        signals[signals.d0_date.ge('2023-05-01')].sort_values('d1_date').reset_index(drop=True),
        old[old.d1_date.le(END)].sort_values('d1_date').reset_index(drop=True),
        check_dtype=False)
    assert signals.d1_date.is_unique

    baseline, baseline_monthly, shadow_nav, shadow_ledger, rejected = replay(
        signals, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    assert not rejected and baseline['entries'] == len(signals) and baseline['pending'] == 0
    shadow_nav = shadow_nav.set_index('date').sort_index()
    months = sorted(set(shadow_nav.index.str[:7]))
    assert months[0] == '2022-05' and months[-1] == '2026-09' and len(months) == 53
    shadow_candidates = signals.d0_date.str[:7].value_counts().to_dict()
    gate = {}
    for i, month in enumerate(months):
        prev = months[i - 1] if i else None
        n = shadow_candidates.get(prev, 0) if prev else None
        r5 = prior_return(shadow_nav, prev, 5) if prev else None
        r10 = prior_return(shadow_nav, prev, 10) if prev else None
        paused = should_pause(n, r5, r10, 'sum_negative')
        gate[month] = dict(前月=prev, 前月候选=n, 前月末5日=r5,
                           前月末10日=r10, 月初暂停=bool(paused))

    decisions = signals[['d0_date','d1_date','symbol','d0_rank','score','rule_id',
                         'entry_time','entry','d2_close']].copy()
    decisions['选股月'] = decisions.d0_date.str[:7]
    decisions['月初暂停'] = decisions.选股月.map(lambda month: gate[month]['月初暂停'])
    decisions['前月末5日'] = decisions.选股月.map(lambda month: gate[month]['前月末5日'])
    decisions['前月末10日'] = decisions.选股月.map(lambda month: gate[month]['前月末10日'])
    decisions['影子D2费后收益'] = decisions.d2_close / decisions.entry * (1-FEE)/(1+FEE)-1
    chosen = signals[signals.d1_date.isin(decisions.loc[~decisions.月初暂停, 'd1_date'])]
    filtered, filtered_monthly, actual_nav, ledger, rejected = replay(
        chosen, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    assert not rejected and filtered['entries'] == len(chosen) and filtered['pending'] == 0
    assert len(signals) == baseline['entries'] == filtered['entries'] + int(decisions.月初暂停.sum())

    # R57's shadow starts in May 2023, but June 2023 onward observes the same
    # previous month as this longer shadow. Earlier history may change May's gate.
    old_months = pd.read_csv(R57/'逐月.csv').set_index('月份')
    for month in old_months.index[1:]:
        assert gate[month]['月初暂停'] == bool(old_months.loc[month, '月初暂停']), month
        np.testing.assert_allclose(
            [gate[month]['前月末5日'], gate[month]['前月末10日']],
            old_months.loc[month, ['前月末5日','前月末10日']].to_numpy(dtype=float),
            rtol=0, atol=1e-12)
    old_baseline, _, _, _, rejected = replay(old[old.d1_date.le(END)], calendar,
        start='2023-05-01', end=END, initial=INITIAL, fee_rate=FEE)
    assert not rejected
    saved = pd.read_csv(R57/'总体.csv').set_index('方案').loc['原B2']
    np.testing.assert_allclose(old_baseline['final_nav'], saved['期末资金'], rtol=0, atol=1e-7)

    output = []
    for name, result in [('原B2', baseline), ('收益和整月停摆', filtered)]:
        output.append(dict(方案=name, 起点=START[:7], 截止=END[:7],
            信号=len(signals), 买入=result['entries'], 跳过=len(signals)-result['entries'],
            总收益=result['total_return'], 最大回撤=result['max_drawdown'],
            年化夏普=result['sharpe'], D2费后胜率=result['closed_win_rate'],
            D2费后笔均=result['closed_mean_return'], 期末资金=result['final_nav'],
            交易费=result['total_fees']))
    total = pd.DataFrame(output)

    monthly_rows = []
    baseline_by_month = {row['month']:row for row in baseline_monthly}
    filtered_by_month = {row['month']:row for row in filtered_monthly}
    for month in months:
        base = baseline_by_month[month]
        actual = filtered_by_month[month]
        monthly_rows.append(dict(月份=month, **gate[month],
            原B2候选=shadow_candidates.get(month,0),
            放行D0=int((decisions.选股月.eq(month) & ~decisions.月初暂停).sum()),
            原B2月收益=base['return_'], 原B2月回撤=base['drawdown'],
            实买=actual['entries'], 月收益=actual['return_'],
            月回撤=actual['drawdown'], 月费用=actual['fees']))
    monthly = pd.DataFrame(monthly_rows)
    assert np.isclose(np.prod(1+monthly.原B2月收益)-1, baseline['total_return'])
    assert np.isclose(np.prod(1+monthly.月收益)-1, filtered['total_return'])

    yearly_rows = []
    for name, nav, trades in [('原B2',shadow_nav.reset_index(),shadow_ledger),
                               ('收益和整月停摆',actual_nav,ledger)]:
        for year, group in nav.groupby(nav.date.str[:4],sort=True):
            first = group.index[0]
            opening = INITIAL if first == 0 else float(nav.loc[first-1,'nav'])
            values = np.r_[opening,group.nav.to_numpy(dtype=float)]
            yearly_rows.append(dict(年份=year,方案=name,
                买入=int(trades.entry_date.str.startswith(year).sum()),
                收益=float(values[-1]/values[0]-1),
                最大回撤=float(np.max(1-values/np.maximum.accumulate(values)))))
    yearly = pd.DataFrame(yearly_rows)

    start_rows = []
    for first in (START,'2023-05-01','2024-03-01','2025-05-01'):
        for name, picks in [('原B2',signals),('收益和整月停摆',chosen)]:
            # The R57 May-2023 reset has no April-2023 D0 cohort; keep that
            # precise comparison while retaining its D1-based later resets.
            if first == '2023-05-01':
                picks = picks[picks.d0_date.ge(first)]
            result, _, _, _, rejected = replay(picks,calendar,start=first,end=END,
                initial=INITIAL,fee_rate=FEE)
            assert not rejected
            start_rows.append(dict(起点=first[:7],方案=name,买入=result['entries'],
                总收益=result['total_return'],最大回撤=result['max_drawdown'],
                年化夏普=result['sharpe']))
    starts = pd.DataFrame(start_rows)

    early_last = '2023-04-30'
    stage_rows = []
    for name, nav, trades in [('原B2',shadow_nav.reset_index(),shadow_ledger),
                               ('收益和整月停摆',actual_nav,ledger)]:
        early = nav[nav.date.le(early_last)]
        values = np.r_[INITIAL,early.nav.to_numpy(dtype=float)]
        stage_rows.append(dict(区间='2022-05—2023-04',方案=name,
            D1买入=int(trades.entry_date.le(early_last).sum()),
            收益=float(values[-1]/INITIAL-1),
            最大回撤=float(np.max(1-values/np.maximum.accumulate(values)))))
    stages = pd.DataFrame(stage_rows)

    daily = shadow_nav[['nav','return_']].rename(columns={
        'nav':'原B2净值','return_':'原B2日收益'}).join(
        actual_nav.set_index('date')[['nav','return_']].rename(columns={
            'nav':'停摆净值','return_':'停摆日收益'}),validate='one_to_one')

    total.to_csv(OUT/'总体.csv',index=False,encoding='utf-8-sig')
    monthly.to_csv(OUT/'逐月.csv',index=False,encoding='utf-8-sig')
    yearly.to_csv(OUT/'逐年.csv',index=False,encoding='utf-8-sig')
    starts.to_csv(OUT/'不同起点.csv',index=False,encoding='utf-8-sig')
    stages.to_csv(OUT/'新增阶段.csv',index=False,encoding='utf-8-sig')
    daily.to_csv(OUT/'逐日净值.csv',encoding='utf-8-sig')
    candidate_columns = ['选股月','d0_date','d1_date','symbol','d0_rank','score',
                         'rule_id','entry_time','entry','d2_close','前月末5日',
                         '前月末10日','月初暂停','影子D2费后收益']
    decisions[candidate_columns].rename(columns={
        'd0_date':'D0','d1_date':'D1','symbol':'股票','d0_rank':'排名',
        'score':'日线分','rule_id':'四K规则','entry_time':'买入分钟',
        'entry':'模拟买价','d2_close':'D2收盘',
        '前月末5日':'前月末5收益','前月末10日':'前月末10收益'
    }).to_csv(OUT/'逐笔候选.csv',index=False,encoding='utf-8-sig')
    ledger[['d0_date','entry_date','exit_date','symbol','d0_rank','score',
            'rule_id','entry_time','buy','sell','return_','cost','pnl',
            'allocation']].rename(columns={
        'd0_date':'D0','entry_date':'买入日','exit_date':'D2卖出日',
        'symbol':'股票','d0_rank':'排名','score':'日线分',
        'rule_id':'四K规则','entry_time':'买入分钟','buy':'买价',
        'sell':'D2卖价','return_':'D2费后收益','cost':'投入',
        'pnl':'盈亏额','allocation':'资金分配'
    }).to_csv(OUT/'实买账本.csv',index=False,encoding='utf-8-sig')
    dump(OUT/'backtest_audit.json',dict(
        signal_sha256=sha(SOURCE/'signals.pkl'),calendar_sha256=sha(SOURCE/'calendar.json'),
        old_R41_signal_sha256=sha(R41/'signals.pkl'),
        old_R57_month_sha256=sha(R57/'逐月.csv'),
        original_B2_overlap_replayed_exact=True,
        overlapping_gates_from_2023_06_exact=True,
        months=len(months),new_test_months=12,
        first_month_without_prior_B2_history='2022-05',
        paused_months=int(monthly.月初暂停.sum()),
        fee_rate=FEE,production_changed=False))
    print(total.to_string(index=False),flush=True)
    print('\nNEW MONTHS',flush=True)
    print(monthly[monthly.月份.le('2023-04')].to_string(index=False),flush=True)
    print('\nYEARLY',flush=True)
    print(yearly.to_string(index=False),flush=True)
    print('\nNEW PERIOD',flush=True)
    print(stages.to_string(index=False),flush=True)


if __name__ == '__main__':
    main()
