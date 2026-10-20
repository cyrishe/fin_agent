"""Prepare a compact audit workbook payload from the completed R47 replay."""
from __future__ import annotations

import json
from collections import Counter
import numpy as np
import pandas as pd

from scripts.experiment_b2_daily_shape_r47 import END, MODES, OUT, START
from scripts.experiment_b2_daily_refit_r46 import CALENDAR, ROOT as DAILY_ROOT
from scripts.replay_limit_rebound_cash_r23 import dump


PLAN_NAMES = {
    'fixed_monthly_B2': '原B2月更',
    'gap2_shape2': '日更6训2验',
    'direct6_shape1': '日更6训+形态1验',
    'direct6_shape2': '日更6训+形态2验',
}
GATE_NAMES = {'raw': '原始', 'shadow30_10': '影子停手'}


def number(x):
    if x is None or pd.isna(x):
        return None
    if isinstance(x, (np.integer, int)):
        return int(x)
    return float(x)


def text(x):
    return None if x is None or pd.isna(x) else str(x)


def first_text(*items):
    return next((text(x) for x in items if text(x) is not None), None)


def main() -> None:
    overall = json.loads((OUT / 'overall.json').read_text())
    monthly = json.loads((OUT / 'monthly.json').read_text())
    yearly = json.loads((OUT / 'yearly.json').read_text())
    classification = json.loads((OUT / 'classification.json').read_text())
    trades = pd.read_pickle(OUT / 'ledger.pkl')
    decisions = pd.read_pickle(OUT / 'daily_decisions.pkl')
    gate = pd.read_pickle(OUT / 'gate_decisions.pkl')
    signal = pd.read_pickle(OUT / 'all_signals.pkl')
    assert set(PLAN_NAMES) == set(x['plan'] for x in overall)
    assert len(trades) == sum(x['entries'] for x in overall)
    hits = {x['plan']: x for x in classification if x['stage'] == '可成交信号'}
    overview = [[PLAN_NAMES[x['plan']], GATE_NAMES[x['gate']], x['entries'],
                 x['final_nav'], x['total_return'], x['max_drawdown'],
                 x['sharpe'], hits[x['plan']]['hit'], hits[x['plan']]['d2_fee_mean']]
                for x in overall]
    years = [[x['year'], PLAN_NAMES[x['plan']], GATE_NAMES[x['gate']],
              x['entries'], x['opening_nav'], x['closing_nav'], x['return_'],
              x['drawdown'], x['sharpe']] for x in yearly]
    months = [[x['month'], PLAN_NAMES[x['plan']], GATE_NAMES[x['gate']],
               x['entries'], x['opening_nav'], x['closing_nav'], x['return_'],
               x['drawdown'], x['fees']] for x in monthly]
    metrics = [[PLAN_NAMES.get(x['plan'], x['plan']), x['stage'], x['n'],
                x['hit'], x.get('d2_fee_mean'), x.get('loss_gt5')]
               for x in classification]

    ledger_rows = []
    for r in trades.sort_values(['entry_date', 'plan', 'gate']).to_dict('records'):
        ledger_rows.append([
            r['entry_date'], PLAN_NAMES[r['plan']], GATE_NAMES[r['gate']],
            number(r.get('d0_rank')), number(r.get('score')),
            first_text(r.get('shape_model'), r.get('rule_id')),
            number(r.get('high5')), number(r.get('low5')),
            number(r.get('body5')), text(r.get('entry_time')),
            number(r.get('buy')), number(r.get('cost')),
            text(r.get('exit_date')), number(r.get('sell')),
            number(r.get('return_')), number(r.get('label_3')),
        ])

    gate_lookup = {(r.plan, r.day): bool(r.stop) for r in gate.itertuples()}
    fill_days = set(zip(signal.plan, signal.d1_date))
    decision_rows = []
    for r in decisions.sort_values(['d0_date', 'shape_variant']).to_dict('records'):
        name = r['shape_variant']
        decision_rows.append([
            r['d0_date'], text(r.get('d1_date')), PLAN_NAMES[name],
            number(r.get('d0_rank')), number(r.get('score')),
            text(r.get('shape_model')), number(r.get('high5')),
            number(r.get('low5')), number(r.get('body5')),
            int((name, r.get('d1_date')) in fill_days),
            text(r.get('delayed_time')), number(r.get('delayed_entry')),
            number(r.get('delayed_net')), number(r.get('label_3')),
            int(gate_lookup.get((name, r.get('d1_date')), False)),
        ])

    calendars = [d for d in json.loads(CALENDAR.read_text()) if START <= d <= END]
    shape_models = []
    for variant in MODES:
        first_mode = MODES[variant][0]
        by_month = {}
        for day in calendars:
            receipt = OUT / 'daily_shape' / variant / day / 'complete.json'
            if not receipt.exists():
                continue
            z = json.loads(receipt.read_text())
            by_month.setdefault(day[:7], []).append(z)
        for month, items in sorted(by_month.items()):
            choices = Counter(z['selected'] for z in items)
            first_stage = [json.loads((DAILY_ROOT / first_mode / z['day'] / 'complete.json').read_text())
                           for z in items]
            first_valid_top1 = [z['valid_open']['top1']['rate'] for z in first_stage
                                if z.get('valid_open') and z['valid_open']['top1']['rate'] is not None]
            train = np.mean([next(c['train'][0] for c in z['candidates']
                                  if c['name'] == z['selected']) for z in items])
            valid = np.mean([next(c['valid'][0] for c in z['candidates']
                                  if c['name'] == z['selected']) for z in items])
            valid_base = np.mean([z['candidates'][0]['valid'][0] for z in items])
            shape_models.append([month, PLAN_NAMES[variant], len(items),
                round(np.mean([z['train_rows'] for z in first_stage]), 1),
                float(np.mean(first_valid_top1)) if first_valid_top1 else None,
                round(np.mean([z['train_rows'] for z in items]), 1),
                round(np.mean([z['valid_rows'] for z in items]), 1),
                choices.get('rank_only', 0),
                sum(v for k, v in choices.items() if k != 'rank_only'),
                float(train), float(valid), float(valid_base)])

    payload = dict(scope='D1 2023-05-05—2026-09-30；2023 年 5 月起，2026 年截至 9 月；D2 收盘卖，买卖各 0.1%。',
                   overview=overview, yearly=years, monthly=months,
                   classification=metrics, ledger=ledger_rows,
                   decisions=decision_rows, shape_models=shape_models)
    dump(OUT / 'workbook_data.json', payload)
    print(OUT / 'workbook_data.json')


if __name__ == '__main__':
    main()
