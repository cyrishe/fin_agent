"""Read-only audit of whether B2 monthly daily validation predicts next month.

Uses frozen R41/R35/R18 model metrics and the uninterrupted B2 R41 account.
The 50% split is descriptive; this script never creates a trading gate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump, sha


ROOT = Path('outputs/stock_automl')
SOURCE = Path('docs/stock_automl_runs/20261019_b2_2022_minutes_r41')
OUT = Path('docs/stock_automl_runs/20261019_b2_daily_validation_r42')


def metrics_path(month: str) -> Path:
    if month < '2023-07':
        return ROOT/'b2_2022_minute_r41_20261019'/'daily2'/month/'metrics.json'
    if month < '2025-01':
        return ROOT/'b2_two_month_2024_r35_20261019'/'daily2'/month/'metrics.json'
    return ROOT/'limit_rebound_segments_r18_20261015'/month/'segments_metrics.json'


def describe(frame: pd.DataFrame) -> dict:
    def corr(x: str, y: str) -> float:
        return float(frame[x].corr(frame[y]))
    groups = {}
    for key in ('valid_top1', 'valid_top3'):
        for label, part in (('below50', frame[frame[key] < .5]),
                            ('atleast50', frame[frame[key] >= .5])):
            groups[f'{key}_{label}'] = dict(months=len(part),
                mean_b2_month_return=float(part.b2_month_return.mean()),
                mean_d2_net=float(part.d2_net_mean.mean()))
    return dict(months=len(frame),
        corr_valid_top1_d2_net=corr('valid_top1', 'd2_net_mean'),
        corr_valid_top1_b2_month=corr('valid_top1', 'b2_month_return'),
        corr_valid_top3_d2_net=corr('valid_top3', 'd2_net_mean'),
        corr_valid_top3_b2_month=corr('valid_top3', 'b2_month_return'),
        descriptive_50pct_split=groups)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    monthly = pd.DataFrame(json.loads((SOURCE/'monthly.json').read_text()))
    signals = pd.DataFrame(json.loads((SOURCE/'signals.json').read_text()))
    signals['month'] = signals.date.str[:7]
    d2 = signals.groupby('month').agg(
        b2_signals=('date', 'size'), d2_net_mean=('d2_net_return', 'mean'))
    d2.index.name = 'month'
    rows, sources = [], []
    for month in monthly.month:
        path = metrics_path(month)
        contents = json.loads(path.read_text())
        parts = contents.get('metrics', contents)
        valid = parts['valid']['open']
        train = parts['train']['open']
        assert contents['window']['test'] == month
        rows.append(dict(month=month,
            train_top1=train['top1']['rate'],
            valid_top1=valid['top1']['rate'],
            valid_top1_n=valid['top1']['n'],
            valid_top3=valid['top3']['rate'],
            valid_top3_n=valid['top3']['n']))
        sources.append(dict(month=month, path=str(path), sha256=sha(path)))
    table = monthly[['month', 'baseline_return']].rename(
        columns={'baseline_return': 'b2_month_return'})
    table = table.merge(pd.DataFrame(rows), on='month', validate='one_to_one')
    table = table.merge(d2.reset_index(), on='month', validate='one_to_one')
    assert len(table) == 41 and table.b2_signals.sum() == len(signals) == 543
    table.to_csv(OUT/'逐月验证与次月表现.csv', index=False)
    groups = {
        'full': describe(table),
        '2023_May_Dec': describe(table[table.month.str.startswith('2023')]),
        '2024': describe(table[table.month.str.startswith('2024')]),
        '2025_2026_Sep': describe(table[table.month >= '2025-01']),
    }
    dump(OUT/'summary.json', dict(
        description='Frozen B2 validation metrics vs next month unfiltered B2; 50% split descriptive only',
        monthly_sources=dict(monthly_sha256=sha(SOURCE/'monthly.json'),
                             signals_sha256=sha(SOURCE/'signals.json')),
        metric_sources=sources, groups=groups,
        checked_months=len(table), checked_signals=int(table.b2_signals.sum())))
    print(json.dumps(groups, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
