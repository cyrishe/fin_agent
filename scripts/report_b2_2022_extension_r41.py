"""Continuous B2 30/10 historical-performance gate after 2022 minute extension."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.extend_b2_2022_minutes_r41 import END, OUT as SOURCE, REPORT, R35, START
from scripts.report_b2_double_shadow_full_r40 import step
from scripts.replay_limit_rebound_cash_r23 import drawdown, dump, replay, sha, sharpe


INITIAL = 1_000_000.
FEE = .001


def run():
    REPORT.mkdir(parents=True, exist_ok=True)
    calendar = json.loads((SOURCE/'calendar.json').read_text())
    dates = [d for d in calendar if START <= d <= END]
    frame = pd.read_pickle(SOURCE/'signals.pkl')
    signals = frame[frame.d1_date.between(START, END)].sort_values('d1_date')
    assert not signals.d1_date.duplicated().any()
    by_day = {r['d1_date']: r for r in signals.to_dict('records')}
    paper = {'cash': INITIAL, 'history': [INITIAL], 'positions': []}
    real = {'cash': INITIAL, 'history': [INITIAL], 'positions': []}
    daily, decisions, chosen = [], [], set()
    for i, day in enumerate(dates):
        # At the morning decision, history[i] is yesterday's completed close.
        prior = paper['history'][i]
        hist30 = prior/paper['history'][i-30]-1 if i >= 30 else None
        hist10 = prior/paper['history'][i-10]-1 if i >= 10 else None
        stop = bool(hist30 is not None and hist30 <= -.10 and
                    hist10 is not None and hist10 < .01)
        row = by_day.get(day)
        if row is not None:
            decisions.append(dict(date=day, d0_date=row['d0_date'],
                symbol=row['symbol'], d0_rank=int(row['d0_rank']),
                score=float(row['score']), rule=str(row['rule_id']),
                buy_price=float(row['entry']), entry_time=str(row['entry_time']),
                d2_close=float(row['d2_close']),
                d2_net_return=float(row['d2_close']/row['entry']*(1-FEE)/(1+FEE)-1),
                history30=hist30, history10=hist10,
                action='跳过' if stop else '买入'))
            if not stop:
                chosen.add(day)
        paper_day = step(paper, day, i, row, {})
        real_day = step(real, day, i, row if not stop else None, {})
        daily.append(dict(date=day, history30=hist30, history10=hist10,
            stop=int(stop), signal=int(row is not None),
            action=('跳过' if stop else '买入') if row is not None else '',
            prior_paper_nav=prior, paper_nav=paper_day['nav'],
            **{k:real_day[k] for k in ('opening_cash','buy','sales','fees',
                                        'cash','market_value','nav','positions')}))
    chosen_frame = signals[signals.d1_date.isin(chosen)]
    base_s, base_m, base_d, _, base_ex = replay(
        signals, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    real_s, real_m, real_d, ledger, real_ex = replay(
        chosen_frame, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    assert not base_ex and not real_ex
    np.testing.assert_allclose([x['paper_nav'] for x in daily], base_d.nav,
                               atol=1e-7, rtol=0)
    np.testing.assert_allclose([x['nav'] for x in daily], real_d.nav,
                               atol=1e-7, rtol=0)
    assert len(decisions) == len(signals) and len(ledger) == len(chosen)
    assert all(x['date'] >= dates[30] for x in decisions
               if x['date'].startswith('2024-03'))
    old = pd.read_pickle(R35/'signals.pkl')
    a = signals[signals.d0_date.ge('2024-03-01')]
    b = old[old.d0_date.ge('2024-03-01') & old.d1_date.le(END)]
    cols = ['symbol','d0_date','d1_date','entry_time','entry','d2_close']
    pd.testing.assert_frame_equal(a[cols].sort_values(['d0_date','symbol']).reset_index(drop=True),
        b[cols].sort_values(['d0_date','symbol']).reset_index(drop=True),check_dtype=False)
    old_march = json.loads((Path('docs/stock_automl_runs/20261019_b2_double_shadow_full_r40')/
                            'signals.json').read_text())
    march = [x for x in decisions if x['date'].startswith('2024-03')]
    assert len(march) == len([x for x in old_march if x['date'].startswith('2024-03')]) == 14
    assert {(x['date'],x['symbol']) for x in march} == {
        (x['date'],x['symbol']) for x in old_march if x['date'].startswith('2024-03')}
    assert all(x['history30'] is not None and x['history10'] is not None for x in march)
    lot_by_day = {x['entry_date']: x for x in ledger.to_dict('records')}
    for x in decisions:
        lot = lot_by_day.get(x['date'])
        x['invested'] = float(lot['cost']) if lot else None
        x['realized_pnl'] = float(lot['pnl']) if lot else None
    for x, r in zip(daily, real_d.to_dict('records'), strict=True):
        x['day_return'] = float(r['return_'])
        x['drawdown'] = float(r['drawdown'])
    by_decision = pd.DataFrame(decisions)
    monthly = []
    for m, b in zip(real_m, base_m, strict=True):
        month = m['month']
        s = by_decision[by_decision.date.str.startswith(month)]
        monthly.append(dict(month=month, market_days=m['days'],
            signals=len(s), buys=int(s.action.eq('买入').sum()),
            skips=int(s.action.eq('跳过').sum()),
            stopped_days=sum(x['stop'] for x in daily if x['date'].startswith(month)),
            opening_nav=m['opening_nav'], closing_nav=m['closing_nav'],
            return_=m['return_'], drawdown=m['drawdown'],
            baseline_return=b['return_'], baseline_drawdown=b['drawdown'],
            fees=m['fees']))
    yearly = []
    prev_real = prev_paper = INITIAL
    for year, group in pd.DataFrame(daily).groupby(pd.DataFrame(daily).date.str[:4],sort=True):
        end_real, end_paper = float(group.nav.iloc[-1]), float(group.paper_nav.iloc[-1])
        y = by_decision[by_decision.date.str.startswith(year)]
        yearly.append(dict(year=year, start=group.date.iloc[0], end=group.date.iloc[-1],
            buys=int(y.action.eq('买入').sum()), skips=int(y.action.eq('跳过').sum()),
            opening_nav=prev_real,closing_nav=end_real,
            return_=end_real/prev_real-1,
            drawdown=drawdown(np.r_[prev_real,group.nav]),
            sharpe=sharpe(group.day_return),
            baseline_return=end_paper/prev_paper-1,
            baseline_drawdown=drawdown(np.r_[prev_paper,group.paper_nav])))
        prev_real, prev_paper = end_real, end_paper
    assert np.isclose(np.prod([1+x['return_'] for x in monthly])-1,
                      real_s['total_return'])
    assert np.isclose(np.prod([1+x['return_'] for x in yearly])-1,
                      real_s['total_return'])
    summary = dict(start=START,end=END,initial=INITIAL,
        source_signal_sha256=sha(SOURCE/'signals.pkl'),
        minute_manifest_sha256=sha(SOURCE/'minute_sources.json'),
        original_signal_sha256=sha(R35/'signals.pkl'),
        baseline=base_s, historical_gate=real_s,
        total_signals=len(decisions), total_buys=len(chosen),
        total_skips=len(decisions)-len(chosen),
        stopped_days=sum(x['stop'] for x in daily),
        first_30_available=next(x['date'] for x in daily if x['history30'] is not None),
        march_2024=dict(signals=len(march),buys=sum(x['action']=='买入' for x in march),
            skips=sum(x['action']=='跳过' for x in march),
            all_history_available=True,
            first_signal=march[0]['date'],last_signal=march[-1]['date']))
    dump(REPORT/'summary.json',summary)
    dump(REPORT/'yearly.json',yearly)
    dump(REPORT/'monthly.json',monthly)
    dump(REPORT/'daily.json',daily)
    dump(REPORT/'signals.json',decisions)
    dump(REPORT/'march_2024.json',march)
    dump(REPORT/'specification.json',dict(
        experiment='R41 extended B2 historical 30/10 gate',
        source_spec_sha256=sha(SOURCE/'specification.json'),
        source_signals_sha256=summary['source_signal_sha256'],
        old_signal_sha256=summary['original_signal_sha256'],
        gate='At D1 before buy: prior close unfiltered B2 account return over 30 trading days <=-10% and over 10 days <+1%; no other filter added',
        account='One continuous 1m account from 2023-05; B2 flat half cash or carried all remaining; D2 close sell; 0.1% each side',
        original_B2_2024_03_onward_signals_exact=True,
        reproducibility='Parallel day-by-day reference and gated accounts each agree with canonical cash replay',
        status='historical research, no production strategy change or DB write'))
    dump(REPORT/'workbook_data.json',{
        '总览': dict(
            note='同一账户从2023-05连续到2026-09；每天用此前30/10交易日原B2模拟净值决定是否买入；双边各0.1%费。',
            headers=['方案','起点','终点','信号','买入','跳过','期末净值','总收益','最大回撤','夏普','交易费'],
            rows=[
                ['原B2',START,END,len(decisions),len(decisions),0,base_s['final_nav'],base_s['total_return'],base_s['max_drawdown'],base_s['sharpe'],base_s['total_fees']],
                ['历史30/10过滤',START,END,len(decisions),len(chosen),len(decisions)-len(chosen),real_s['final_nav'],real_s['total_return'],real_s['max_drawdown'],real_s['sharpe'],real_s['total_fees']],
            ],percent=[7,8],decimal=[9]),
        '逐年': dict(
            note='2023只含5—12月，2026只含1—9月；年度之间不重置账户。',
            headers=['年份','起始日','结束日','买入','跳过','年初净值','年末净值','年收益','年回撤','夏普','原B2收益','原B2回撤'],
            rows=[[x[k] for k in ('year','start','end','buys','skips','opening_nav','closing_nav','return_','drawdown','sharpe','baseline_return','baseline_drawdown')] for x in yearly],
            percent=[7,8,10,11],decimal=[9]),
        '逐月': dict(
            note='月收益按持仓日历月的月初至月末净值；月回撤从月初净值重置峰值。',
            headers=['月份','信号','买入','跳过','停手日','月初净值','月末净值','月收益','月回撤','原B2收益','原B2回撤','费用'],
            rows=[[x[k] for k in ('month','signals','buys','skips','stopped_days','opening_nav','closing_nav','return_','drawdown','baseline_return','baseline_drawdown','fees')] for x in monthly],
            percent=[7,8,9,10]),
        '2024三月核对': dict(
            note='14笔原B2信号与旧版完全相同；历史30/10均为当天买入前可见值，逐笔直接显示买或跳过。',
            headers=['D1日期','D0排名','D0评分','历史30日','历史10日','动作','买价','D2收盘','D2费后涨跌','实际投入','实际盈亏'],
            rows=[[x[k] for k in ('date','d0_rank','score','history30','history10','action','buy_price','d2_close','d2_net_return','invested','realized_pnl')] for x in march],
            percent=[3,4,8],decimal=[2]),
        '逐笔': dict(
            note='完整信号；“跳过”的D2结果只作事后对照，不进入真实账户。',
            headers=['D1日期','D0排名','评分','形态规则','历史30日','历史10日','动作','买入时刻','代理买价','D2收盘','D2费后涨跌','实际投入','实际盈亏'],
            rows=[[x[k] for k in ('date','d0_rank','score','rule','history30','history10','action','entry_time','buy_price','d2_close','d2_net_return','invested','realized_pnl')] for x in decisions],
            percent=[4,5,10],decimal=[2]),
    })
    print(json.dumps(dict(summary={k:summary[k] for k in
        ('start','end','total_signals','total_buys','total_skips','stopped_days',
         'first_30_available','march_2024')},
        baseline={k:base_s[k] for k in ('total_return','max_drawdown','sharpe')},
        gate={k:real_s[k] for k in ('total_return','max_drawdown','sharpe','final_nav')},
        yearly=yearly),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    run()
