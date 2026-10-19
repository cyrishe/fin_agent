"""Sequential B2 double-shadow 30/10 gate, 2024-03 through 2026-09.

This research report does not alter the registered B2 strategy or production DB.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_rolling_nav_gate_r38 import CALENDAR, END, FEE, INITIAL, SOURCE
from scripts.replay_limit_rebound_cash_r23 import drawdown, dump, replay, sha, sharpe


START = '2024-03-01'
OUT = Path('docs/stock_automl_runs/20261019_b2_double_shadow_full_r40')
PRIOR = Path('docs/stock_automl_runs/20261019_b2_recovery_gate_r39')


def step(account: dict, day: str, day_index: int, row: dict | None,
         index: dict[str, int]) -> dict:
    """Buy first, then mark/sell existing D2 lots; today's cash cannot fund today's buy."""
    cash = account['cash']
    opening_cash = cash
    buy = sales = fees = realized = 0.
    if row is not None:
        assert len(account['positions']) <= 1
        budget = cash * (1. if account['positions'] else .5)
        assert budget > 1e-8
        amount = budget / (1+FEE)
        account['positions'].append((day_index, amount/float(row['entry']), row))
        cash -= budget
        buy = budget
        fees += budget-amount
    keep = []
    market_value = 0.
    for bought_i, shares, bought_row in account['positions']:
        k = day_index-bought_i+1
        assert k in (1, 2)
        gross = shares*float(bought_row[f'd{k}_close'])
        if k == 2:
            proceeds = gross*(1-FEE)
            cash += proceeds
            sales += proceeds
            fees += gross*FEE
        else:
            keep.append((bought_i, shares, bought_row))
            market_value += gross
    account['positions'] = keep
    account['cash'] = cash
    nav = cash+market_value
    account['history'].append(nav)
    assert cash >= -1e-7 and nav > 0
    return dict(date=day, opening_cash=opening_cash, buy=buy, sales=sales,
                fees=fees, cash=cash, market_value=market_value, nav=nav,
                positions=len(keep))


def run() -> tuple[dict, list[dict], list[dict], list[dict], list[dict]]:
    calendar = json.loads(CALENDAR.read_text())
    dates = [day for day in calendar if START <= day <= END]
    index = {day: i for i, day in enumerate(dates)}
    frame = pd.read_pickle(SOURCE/'signals.pkl')
    signal = frame[frame.d1_date.between(START, END)].sort_values('d1_date')
    assert len(signal) == len(signal.drop_duplicates('d1_date')) == 404
    by_day = {r['d1_date']: r for r in signal.to_dict('records')}
    paper = {'cash': INITIAL, 'history': [INITIAL], 'positions': []}
    real = {'cash': INITIAL, 'history': [INITIAL], 'positions': []}
    daily, decisions, chosen = [], [], set()
    for i, day in enumerate(dates):
        # history[i] is the NAV at yesterday's close, not today's close.
        prior_paper_nav = paper['history'][i]
        paper30 = prior_paper_nav/paper['history'][i-30]-1 if i >= 30 else None
        paper10 = prior_paper_nav/paper['history'][i-10]-1 if i >= 10 else None
        blocked = bool(paper30 is not None and paper30 <= -.10
                       and paper10 is not None and paper10 < .01)
        row = by_day.get(day)
        if row is not None:
            d2_net = float(row['d2_close']/row['entry']*(1-FEE)/(1+FEE)-1)
            decisions.append(dict(date=day, d0_date=row['d0_date'],
                                  symbol=row['symbol'], d0_rank=int(row['d0_rank']),
                                  score=float(row['score']), rule=str(row['rule_id']),
                                  entry_time=str(row['entry_time']),
                                  buy_price=float(row['entry']),
                                  d2_close=float(row['d2_close']),
                                  d2_net_return=d2_net, shadow30=paper30,
                                  shadow10=paper10, blocked=int(blocked),
                                  action='跳过' if blocked else '买入'))
            if not blocked:
                chosen.add(day)
        paper_day = step(paper, day, i, row, index)
        real_day = step(real, day, i, row if not blocked else None, index)
        daily.append(dict(date=day, shadow_prior_nav=prior_paper_nav,
                          shadow30=paper30, shadow10=paper10,
                          gate_blocked=int(blocked),
                          signal=int(row is not None),
                          action=('跳过' if blocked else '买入') if row is not None else '',
                          shadow_nav=paper_day['nav'],
                          **{k: real_day[k] for k in
                             ('opening_cash', 'buy', 'sales', 'fees', 'cash',
                              'market_value', 'nav', 'positions')}))
    selected = signal[signal.d1_date.isin(chosen)]
    base_s, base_m, base_d, base_l, base_ex = replay(
        signal, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    result, months, actual_d, ledger, excluded = replay(
        selected, calendar, start=START, end=END, initial=INITIAL, fee_rate=FEE)
    assert not base_ex and not excluded
    np.testing.assert_allclose([x['shadow_nav'] for x in daily], base_d.nav,
                               rtol=0, atol=1e-7)
    np.testing.assert_allclose([x['nav'] for x in daily], actual_d.nav,
                               rtol=0, atol=1e-7)
    assert result['entries'] == len(ledger) == 255
    assert Counter(x['action'] for x in decisions) == {'买入': 255, '跳过': 149}
    assert len(dates) == 629 and len(months) == 31
    # Independent readback of previously audited R39 summary, daily NAV and decisions.
    old_s = next(x for x in json.loads((PRIOR/'summary.json').read_text())
                 if x['账户'] == '全期连续' and x['方案'] == '影子30日+影子10日')
    old_d = [x for x in json.loads((PRIOR/'daily.json').read_text())
             if x['账户'] == '全期连续' and x['方案'] == '影子30日+影子10日']
    old_signals = [x for x in json.loads((PRIOR/'signals.json').read_text())
                   if x['账户'] == '全期连续' and x['方案'] == '影子30日+影子10日']
    assert [x['date'] for x in old_d] == dates
    np.testing.assert_allclose([x['nav'] for x in old_d], actual_d.nav,
                               rtol=0, atol=1e-7)
    np.testing.assert_allclose([old_s['final_nav'], old_s['max_drawdown']],
                               [result['final_nav'], result['max_drawdown']],
                               rtol=0, atol=1e-10)
    assert [(x['date'], x['action']) for x in old_signals] == [
        (x['date'], x['action']) for x in decisions]
    for a, b in zip(old_signals, decisions, strict=True):
        for key in ('shadow30', 'shadow10'):
            if a[key] is None:
                assert b[key] is None
            else:
                np.testing.assert_allclose(a[key], b[key], rtol=0, atol=1e-12)

    ledger_by_day = {r['entry_date']: r for r in ledger.to_dict('records')}
    for d in decisions:
        lot = ledger_by_day.get(d['date'])
        d.update(invested=float(lot['cost']) if lot else None,
                 realized_pnl=float(lot['pnl']) if lot else None,
                 actual_d2_return=float(lot['return_']) if lot else None,
                 allocation=lot['allocation'] if lot else '')
    for d in daily:
        month_day = actual_d[actual_d.date.eq(d['date'])].iloc[0]
        d.update(day_return=float(month_day.return_),
                 total_drawdown=float(month_day.drawdown))

    counts = pd.DataFrame(decisions)
    monthly = []
    for month, original in zip(months, base_m, strict=True):
        m = month['month']
        sig = counts[counts.date.str.startswith(m)]
        bought = sig[sig.action.eq('买入')]
        skipped = sig[sig.action.eq('跳过')]
        monthly.append(dict(month=m, days=month['days'],
                            signal_count=len(sig), buys=len(bought), skips=len(skipped),
                            opening_nav=month['opening_nav'],
                            closing_nav=month['closing_nav'],
                            return_=month['return_'], drawdown=month['drawdown'],
                            sharpe=month['sharpe'], fees=month['fees'],
                            cash=month['cash'], market_value=month['market_value'],
                            baseline_return=original['return_'],
                            baseline_drawdown=original['drawdown'],
                            bought_mean_d2=float(bought.d2_net_return.mean()) if len(bought) else None,
                            skipped_mean_d2=float(skipped.d2_net_return.mean()) if len(skipped) else None,
                            gate_days=sum(x['gate_blocked'] for x in daily if x['date'].startswith(m))))
    yearly = []
    prior_real = prior_paper = INITIAL
    for year, group in pd.DataFrame(daily).groupby(pd.DataFrame(daily).date.str[:4], sort=True):
        year_s = counts[counts.date.str.startswith(year)]
        close_real = float(group.nav.iloc[-1])
        close_paper = float(group.shadow_nav.iloc[-1])
        yearly.append(dict(year=year, start=group.date.iloc[0], end=group.date.iloc[-1],
                           days=len(group), signals=len(year_s),
                           buys=int(year_s.action.eq('买入').sum()),
                           skips=int(year_s.action.eq('跳过').sum()),
                           gate_days=int(group.gate_blocked.sum()),
                           opening_nav=prior_real, closing_nav=close_real,
                           return_=close_real/prior_real-1,
                           drawdown=drawdown(np.r_[prior_real, group.nav]),
                           sharpe=sharpe(group.day_return),
                           baseline_return=close_paper/prior_paper-1,
                           baseline_drawdown=drawdown(np.r_[prior_paper, group.shadow_nav]),
                           positive_months=sum(x['return_'] > 0 for x in monthly
                                               if x['month'].startswith(year))))
        prior_real, prior_paper = close_real, close_paper
    assert len(yearly) == 3
    assert np.isclose(np.prod([1+y['return_'] for y in yearly])-1, result['total_return'])
    assert np.isclose(np.prod([1+x['return_'] for x in monthly])-1, result['total_return'])
    summary = dict(start=START, end=END, first_valid_month='2024-03',
                   baseline=base_s, double_shadow=result,
                   signal_count=len(decisions), skipped=149,
                   gate_days=sum(x['gate_blocked'] for x in daily),
                   shadow_recovery_days=sum(
                       x['shadow30'] is not None and x['shadow30'] <= -.10
                       and x['shadow10'] is not None and x['shadow10'] >= .01
                       for x in daily),
                   positive_months=sum(x['return_'] > 0 for x in monthly),
                   skipped_mean_d2=float(counts[counts.action.eq('跳过')].d2_net_return.mean()),
                   bought_mean_d2=float(counts[counts.action.eq('买入')].d2_net_return.mean()),
                   source_signal_sha256=sha(SOURCE/'signals.pkl'),
                   calendar_sha256=sha(CALENDAR))
    return summary, yearly, monthly, daily, decisions


def workbook(summary: dict, yearly: list[dict], monthly: list[dict],
             daily: list[dict], signals: list[dict]) -> dict:
    base, gate = summary['baseline'], summary['double_shadow']
    return {
        '总览': {
            'note': '2024-03—2026-09 单一连续账户；双影子30/10均只读前一交易日净值；收益含买卖各0.1%费。',
            'headers': ['方案', '起点', '终点', '月数', '信号', '买入', '跳过', '期末净值', '总收益', '最大回撤', '夏普', '盈利月', '交易费'],
            'rows': [
                ['B2原策略', START, END, 31, 404, 404, 0, base['final_nav'], base['total_return'], base['max_drawdown'], base['sharpe'], base['positive_months'], base['total_fees']],
                ['影子30日+10日', START, END, 31, 404, 255, 149, gate['final_nav'], gate['total_return'], gate['max_drawdown'], gate['sharpe'], gate['positive_months'], gate['total_fees']],
            ], 'percent': [8, 9], 'decimal': [10]},
        '逐年': {
            'note': '2024仅3—12月；2026仅1—9月。均从同一个2024-03账户连续运行，年初未重置资本或影子历史。',
            'headers': ['年份', '起始日', '结束日', '交易日', '信号', '买入', '跳过', '停手日', '年初净值', '年末净值', '年收益', '年回撤', '夏普', 'B2收益', 'B2回撤', '盈利月'],
            'rows': [[x[k] for k in ('year','start','end','days','signals','buys','skips','gate_days','opening_nav','closing_nav','return_','drawdown','sharpe','baseline_return','baseline_drawdown','positive_months')] for x in yearly],
            'percent': [10, 11, 13, 14], 'decimal': [12]},
        '逐月': {
            'note': '月收益按月初至月末账户收盘净值；月回撤以月初净值重置峰值；跨月持仓按每日收盘盯市。',
            'headers': ['月份', '交易日', '信号', '买入', '跳过', '停手日', '月初净值', '月末净值', '月收益', '月回撤', '月夏普', 'B2月收益', 'B2月回撤', '已买D2均值', '跳过D2均值', '费用', '月末现金', '月末持仓'],
            'rows': [[x[k] for k in ('month','days','signal_count','buys','skips','gate_days','opening_nav','closing_nav','return_','drawdown','sharpe','baseline_return','baseline_drawdown','bought_mean_d2','skipped_mean_d2','fees','cash','market_value')] for x in monthly],
            'percent': [8,9,11,12,13,14], 'decimal': [10]},
        '每日账户': {
            'note': '影子30/10为当日买入前截至昨日收盘的滚动收益；停手日只停止真实新买，影子继续原B2交易。',
            'headers': ['日期', '影子30日', '影子10日', '停手', '有信号', '动作', '影子昨日净值', '影子当日净值', '实际当日净值', '实际日收益', '实际总回撤', '开盘现金', '买入金额', '卖出回款', '费用', '收盘现金', '持仓市值', '持仓数'],
            'rows': [[x[k] for k in ('date','shadow30','shadow10','gate_blocked','signal','action','shadow_prior_nav','shadow_nav','nav','day_return','total_drawdown','opening_cash','buy','sales','fees','cash','market_value','positions')] for x in daily],
            'percent': [1,2,9,10]},
        '逐笔信号': {
            'note': '每条原B2信号均保留；跳过笔的D2收益为事后反事实，不计入实际账户；买入价沿用R35历史分钟代理价。',
            'headers': ['D1日期', 'D0日期', '股票代码', 'D0排名', 'D0评分', '四K规则', '买入时刻', '代理买价', 'D2收盘价', '影子30日', '影子10日', '停手', '动作', 'D2费后涨跌', '实际投入', '实际盈亏', '资金分配'],
            'rows': [[x[k] for k in ('date','d0_date','symbol','d0_rank','score','rule','entry_time','buy_price','d2_close','shadow30','shadow10','blocked','action','d2_net_return','invested','realized_pnl','allocation')] for x in signals],
            'percent': [9,10,13], 'decimal': [4]},
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary, yearly, monthly, daily, signals = run()
    dump(OUT/'summary.json', summary)
    dump(OUT/'yearly.json', yearly)
    dump(OUT/'monthly.json', monthly)
    dump(OUT/'daily.json', daily)
    dump(OUT/'signals.json', signals)
    dump(OUT/'workbook_data.json', workbook(summary, yearly, monthly, daily, signals))
    dump(OUT/'specification.json', dict(
        experiment='R40_B2_double_shadow_30_10_full_sequential',
        strategy_registry='kingdomai.automl_strategy_definition / imac / B2 / v1.0',
        strategy_definition_sha256='157b9d77bb9951214b0978365cec407320b1d5663636b80fe60939ae94172c0c',
        code_sha256=sha(Path(__file__)), source_signal_sha256=summary['source_signal_sha256'],
        calendar_sha256=summary['calendar_sha256'],
        period=[START, END], training='R35 original rolling 6 month train, 2 month validation; no retraining',
        gate='D1 buy-before: previous close paper NAV over 30 completed trading days <= -10% AND over 10 completed trading days < +1%; paper B2 trades every day, real B2 skips new buy while gate true',
        capital='2024-03-01 CNY 1m; flat buy half available cash, carrying one position buy all remaining cash; no borrowing or integer-lot constraint',
        timing='frozen R35 original eligible 09:35-09:40 next-minute-open proxy; D2 close sell; buy before sell on same day; 0.1% per side; no orderbook fill/slippage model',
        account='one continuous real and one continuous paper account; 2025 and 2026 are not restarted',
        evidence='joint sequential replay reconciled to canonical B2 cash replay and saved R39 daily NAV, 404 decisions and pre-decision 30/10 returns',
        study_status='already examined historical period, no untouched holdout; production B2 unchanged; not written to production DB or cloud'))
    print(json.dumps({
        'summary': {k: summary[k] for k in ('signal_count','skipped','gate_days','positive_months')},
        'real': {k: summary['double_shadow'][k] for k in ('final_nav','total_return','max_drawdown','sharpe')},
        'yearly': yearly,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
