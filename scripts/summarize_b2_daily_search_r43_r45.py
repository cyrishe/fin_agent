"""Produce compact Chinese workbook data for frozen R43--R45 B2 research."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.experiment_b2_daily_search_r43 import OUT as HIT, RISK_OUT as RISK, TEST
from scripts.experiment_b2_validation_pause_r45 import OUT as GATE
from scripts.replay_limit_rebound_cash_r23 import dump, sha


def run() -> None:
    h_overall = json.loads((HIT / 'overall.json').read_text())
    r_overall = json.loads((RISK / 'overall.json').read_text())
    g_overall = json.loads((GATE / 'overall.json').read_text())
    h_monthly = json.loads((HIT / 'monthly.json').read_text())
    r_monthly = json.loads((RISK / 'monthly.json').read_text())
    g_monthly = json.loads((GATE / 'monthly.json').read_text())
    h_yearly = json.loads((HIT / 'yearly.json').read_text())
    r_yearly = json.loads((RISK / 'yearly.json').read_text())
    g_yearly = json.loads((GATE / 'yearly.json').read_text())
    plans = [
        ('固定B2', h_overall, h_monthly, h_yearly, 'fixed_RF51', 'raw'),
        ('原30/10停手', h_overall, h_monthly, h_yearly, 'fixed_RF51', '30_10'),
        ('命中选模', h_overall, h_monthly, h_yearly, 'searched_R43_hit', 'raw'),
        ('D2选模', r_overall, r_monthly, r_yearly, 'searched_R44_risk', 'raw'),
    ]
    summary, annual, monthly = [], [], []
    for title, whole, months, years, code, gate in plans:
        obj = next(x for x in whole if x['plan'] == code and x['gate'] == gate)
        summary.append([title, obj['entries'], obj['final_nav'], obj['total_return'],
                        obj['max_drawdown'], obj['sharpe'], obj['total_fees']])
        annual += [[x['year'], title, x['entries'], x['opening_nav'], x['closing_nav'],
                    x['return_'], x['drawdown'], x['sharpe']]
                   for x in years if x['plan'] == code and x['gate'] == gate]
        monthly += [[x['month'], title, x['entries'], x['opening_nav'], x['closing_nav'],
                     x['return_'], x['drawdown'], x['fees']]
                    for x in months if x['plan'] == code and x['gate'] == gate]
    obj = g_overall['validation_gate']
    summary.append(['验证停手', obj['entries'], obj['final_nav'], obj['total_return'],
                    obj['max_drawdown'], obj['sharpe'], obj['total_fees']])
    annual += [[x['year'], '验证停手', x['buys'], x['opening_nav'], x['closing_nav'],
                x['return_'], x['drawdown'], x['sharpe']] for x in g_yearly]
    monthly += [[x['month'], '验证停手', x['entries'], x['opening_nav'], x['closing_nav'],
                 x['return_'], x['drawdown'], x['fees']]
                for x in g_monthly['validation_gate']]
    order = {'固定B2': 0, '原30/10停手': 1, '命中选模': 2,
             'D2选模': 3, '验证停手': 4}
    annual.sort(key=lambda x: (x[0], order[x[1]]))
    monthly.sort(key=lambda x: (x[0], order[x[1]]))

    metrics = []
    pauses = json.loads((GATE / 'monthly_choices.json').read_text())
    for root, title in [(HIT, '命中选模'), (RISK, 'D2选模')]:
        rows = json.loads((root / 'daily_model_metrics.json').read_text())
        by_month = {x['month']: x for x in rows if x['selected']}
        for month in TEST:
            x = by_month[month]
            chosen = json.loads((root / 'selected' / month / 'choice.json').read_text())
            group = next(v for v in chosen['candidates'] if v['id'] == chosen['selected'])
            hits = lambda stage, k: x[stage]['open'][f'top{k}']['rate']
            metrics.append([month, title, x['model'],
                hits('train_infit', 1), hits('oof', 1), hits('oof', 3),
                hits('valid', 1), hits('valid', 3),
                hits('test', 1), hits('test', 3),
                x['valid']['open']['top1']['n'],
                group.get('risk_valid', {}).get('mean'),
                int(pauses[month]['enabled']) if title == 'D2选模' else None])
    metrics.sort(key=lambda x: (x[0], x[1]))

    trades = []
    for root, title in [(HIT, '命中选模'), (RISK, 'D2选模')]:
        frame = pd.read_pickle(root / 'signals.pkl')
        frame = frame[frame.d1_date.between('2023-05-01', '2026-09-30')]
        for x in frame.itertuples():
            model_month = x.d0_date[:7]
            trades.append([x.d1_date, title, int(x.d0_rank), float(x.score),
                str(x.rule_id), float(x.body5), str(x.entry_time),
                float(x.entry), float(x.d2_close),
                float(x.d2_close / x.entry * .999 / 1.001 - 1),
                int(pauses[model_month]['enabled']) if title == 'D2选模' else None])
    trades.sort(key=lambda x: (x[0], x[1]))
    data = dict(
        scope='2023-05 至 2026-09，连续100万元账户；D1按原四K规则模拟买入，D2收盘卖出，买卖各0.1%费用。2023仅5—12月，2026仅1—9月。历史研究期，非新盲测。',
        summary=summary, annual=annual, monthly=monthly,
        model_metrics=metrics, trades=trades,
        source_hashes=dict(
            hit=sha(HIT / 'signals.pkl'), risk=sha(RISK / 'signals.pkl'),
            gate=sha(GATE / 'decisions.pkl'),
            hit_choice=sha(HIT / 'specification.json'),
            risk_choice=sha(RISK / 'specification.json'),
            validation_gate=sha(GATE / 'specification.json')))
    dump(GATE / 'workbook_data.json', data)
    print('rows', {k: len(data[k]) for k in ('summary', 'annual', 'monthly',
                                            'model_metrics', 'trades')})


if __name__ == '__main__':
    run()
