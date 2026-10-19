"""Read-only R34/R35 audit: monthly B2 score quality versus D2 account results."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from scripts.replay_limit_rebound_cash_r23 import dump, sha


ROOT = Path('docs/stock_automl_runs/20261019_b2_accuracy_return_r36')
CASES = [
    ('一月验证', Path('outputs/stock_automl/b2_full_2024_r34_20261019'), '完整2024起', 'D1S1', 1),
    ('两月验证', Path('outputs/stock_automl/b2_two_month_2024_r35_20261019'), '完整两月2024起', 'D2S2', 2),
]


def window(month: str, valid: int) -> dict:
    m = pd.Period(month, freq='M')
    return {'train': f'{m-valid-6}—{m-valid-1}', 'valid': f'{m-valid}—{m-1}'}


def as_num(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def cohort_metrics(rows: pd.DataFrame) -> dict:
    closed = rows[rows.exit_date.notna()].copy()
    known = rows[rows.observed_3.eq(True)]
    return {
        '买入笔': int(len(rows)), '已卖笔': int(len(closed)),
        '实买三日再板': as_num(known.label_3.mean()) if len(known) else None,
        '实买D2封板': as_num(closed.d2_up.mean()) if len(closed) else None,
        'D2笔均': as_num(closed.return_.mean()) if len(closed) else None,
        'D2胜率': as_num(closed.return_.gt(0).mean()) if len(closed) else None,
        '亏超5%': as_num(closed.return_.lt(-.05).mean()) if len(closed) else None,
    }


def relation(df: pd.DataFrame, x: str, y: str) -> dict:
    pair = df[[x, y]].dropna()
    if len(pair) < 4 or pair[x].nunique() < 2 or pair[y].nunique() < 2:
        return {'样本月': len(pair), 'Pearson': None, 'Spearman': None, 'p值': None, '相关95%区间': None}
    pearson = stats.pearsonr(pair[x], pair[y])
    spearman = stats.spearmanr(pair[x], pair[y])
    try:
        ci = pearson.confidence_interval(confidence_level=.95)
        interval = [float(ci.low), float(ci.high)]
    except Exception:
        interval = None
    return {'样本月': len(pair), 'Pearson': float(pearson.statistic),
            'Spearman': float(spearman.statistic), 'p值': float(pearson.pvalue),
            '相关95%区间': interval}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    all_rows, all_corr, profiles, source = [], [], [], []
    for caption, folder, scope, variant, valid in CASES:
        for name in ['daily_metrics.json', 'rules_frozen.json', 'monthly.json', 'ledger.pkl']:
            source.append({'方案': caption, '文件': str(folder / name), 'sha256': sha(folder / name)})
        daily = {(d['month'], d['part']): d for d in json.loads((folder/'daily_metrics.json').read_text())
                 if d['daily_validation'] == valid}
        rules = {d['month']: d for d in json.loads((folder/'rules_frozen.json').read_text())}
        monthly = [m for m in json.loads((folder/'monthly.json').read_text())
                   if m['scope'] == scope and m['variant'] == variant]
        ledger = pd.read_pickle(folder/'ledger.pkl')
        ledger = ledger[ledger.scope.eq(scope) & ledger.variant.eq(variant)].copy()
        for m in monthly:
            month = m['month']
            d = {part: daily[(month, part)]['open']['top1'] for part in ('train', 'valid', 'forecast')}
            rule = rules[month]
            cohort = ledger[ledger.d0_date.str[:7].eq(month)]
            row = {'月份': month, '方案': caption, '训练月': window(month, valid)['train'],
                   '验证月': window(month, valid)['valid'],
                   '训练Top1': d['train']['rate'], '训练样本': d['train']['n'],
                   '验证Top1': d['valid']['rate'], '验证样本': d['valid']['n'],
                   '测试Top1': d['forecast']['rate'], '测试样本': d['forecast']['n'],
                   '形态规则': rule['selected']['id'],
                   '形态训练命中': rule['train_selected']['hit'],
                   '形态训练样本': rule['train_selected']['known'],
                   '形态验证命中': rule['valid_selected']['hit'],
                   '形态验证样本': rule['valid_selected']['known'],
                   '月收益': m['return_'], '月回撤': m['drawdown'],
                   '月末资产': m['closing_nav'], '账户买入笔': m['entries'],
                   **cohort_metrics(cohort)}
            row['验证减训练'] = row['验证Top1'] - row['训练Top1']
            row['日线训练月'] = row['训练月']
            row['日线验证月'] = row['验证月']
            row['实买笔'] = row['买入笔']
            row['完整月'] = month < '2026-10'
            all_rows.append(row)
        periods = [('2024年3—12月', '2024-03', '2025-01'),
                   ('2025年5月—2026年9月', '2025-05', '2026-10')]
        for name, start, end in periods:
            mask = ledger.d0_date.ge(start) & ledger.d0_date.lt(end)
            g = ledger[mask]
            closed = g[g.exit_date.notna()]
            known = g[g.observed_3.eq(True)]
            pos = closed[closed.label_3.eq(1)]
            neg = closed[closed.label_3.eq(0)]
            profiles.append({'方案': caption, '时期': name, '买入笔': len(g), '已卖笔': len(closed),
                             '三日再板': as_num(known.label_3.mean()),
                             'D1或D2封板': as_num((closed.d1_up.eq(True) | closed.d2_up.eq(True)).mean()),
                             'D2封板': as_num(closed.d2_up.mean()),
                             'D2笔均': as_num(closed.return_.mean()),
                             'D2胜率': as_num(closed.return_.gt(0).mean()),
                             '亏超5%': as_num(closed.return_.lt(-.05).mean()),
                             '再板笔收益': as_num(pos.return_.mean()),
                             '未再板笔收益': as_num(neg.return_.mean()),
                             '买价较昨收': as_num((closed.buy/closed.d1_preclose-1).mean()),
                             'D1收盘较买价': as_num((closed.d1_close/closed.buy-1).mean()),
                             'D2收盘较D1': as_num((closed.d2_close/closed.d1_close-1).mean()),
                             'D0市场上涨股比例': as_num(closed.market_up_fraction.mean()),
                             'D0成交额中位亿': as_num(closed.d0_amount_100m.median()),
                             'D1开盘涨幅均值': as_num(closed.open_gap.mean()),
                             '流通估值代理中位亿': as_num((np.exp(closed.log_float_mv_proxy)/1e8).median()),
                             '形态规则all月': sum(r['selected']['id'] == 'all' for k, r in rules.items()
                                                 if start <= k < end)})
    frame = pd.DataFrame(all_rows).sort_values(['月份', '方案']).reset_index(drop=True)
    for caption in frame['方案'].unique():
        group = frame[(frame['方案'] == caption) & frame['完整月']]
        for period, test in [('全期', group), ('2024年', group[group['月份'].str.startswith('2024')]),
                             ('2025-26年', group[group['月份'].ge('2025-01')])]:
            for predictor in ['训练Top1', '验证Top1', '验证减训练', '形态训练命中', '形态验证命中',
                              '测试Top1', '实买三日再板']:
                for outcome in ['月收益', 'D2笔均']:
                    all_corr.append({'方案': caption, '时期': period, '预测量': predictor,
                                     '结果量': outcome, **relation(test, predictor, outcome)})
    dump(ROOT/'月度对照.json', all_rows)
    dump(ROOT/'相关性.json', all_corr)
    dump(ROOT/'时期拆解.json', profiles)
    dump(ROOT/'来源哈希.json', source)
    monthly_headers = ['月份', '方案', '月收益', '月回撤', '账户买入笔', 'D2笔均',
                       '日线训练月', '训练Top1', '训练样本', '日线验证月', '验证Top1',
                       '验证样本', '测试Top1', '测试样本', '实买三日再板', '实买笔',
                       '形态规则', '形态训练命中', '形态验证命中']
    corr_headers = ['方案', '时期', '预测量', '结果量', '样本月', 'Pearson', 'Spearman',
                    'p值', '95%下界', '95%上界']
    profile_headers = ['方案', '时期', '买入笔', '三日再板', 'D1或D2封板', 'D2封板',
                       'D2笔均', 'D2胜率', '亏超5%', '再板笔收益', '未再板笔收益',
                       '买价较昨收', 'D1收盘较买价', 'D2收盘较D1', 'D0成交额中位亿',
                       'D1开盘涨幅均值', '流通估值代理中位亿', '形态规则all月']
    workbook = {
        '月度对照': {'headers': monthly_headers,
                 'rows': [[None if pd.isna(r.get(k)) else r.get(k) for k in monthly_headers]
                          for r in frame.to_dict('records')],
                 'percent': [2,3,5,7,10,12,14,17,18],
                 'note': '月收益/回撤按连续账户日历月；训练/验证为目标月之前的日线窗口。Top1指D0名次第1且D1开盘打开后的D1—D3再板率。实买按D0月份，跨月交易笔数可异；2026-10截至14日。'},
        '相关性': {'headers': corr_headers,
                'rows': [[r.get(k) if k not in ('95%下界','95%上界') else
                          (r['相关95%区间'][0 if k=='95%下界' else 1] if r['相关95%区间'] else None)
                          for k in corr_headers] for r in all_corr],
                'decimal': [5,6,7,8,9],
                'note': 'Pearson/Spearman按完整月计算；2026-10不入相关性。测试Top1和实买再板均为事后诊断，不是月初可用信号。p值仅描述单项相关，不构成新策略验证。'},
        '时段拆解': {'headers': profile_headers,
                  'rows': [[r.get(k) for k in profile_headers] for r in profiles],
                  'percent': [3,4,5,6,7,8,9,10,11,12,13,15],
                  'decimal': [14,16],
                  'note': '按D0月份归属的已买交易；2024比较为3—12月，后期比较为2025-05—2026-09。D2笔均已扣买卖各0.1%费。'},
    }
    dump(ROOT/'workbook_data.json', workbook)
    print('MONTHS', len(frame), 'profiles', json.dumps(profiles, ensure_ascii=False))
    for c in all_corr:
        if c['时期'] == '全期' and c['结果量'] == '月收益' and c['预测量'] in ('训练Top1','验证Top1','验证减训练','测试Top1','实买三日再板'):
            print('CORR', c)
    print('2024', frame[frame['月份'].str.startswith('2024')][['月份','方案','训练Top1','验证Top1','测试Top1','实买三日再板','D2笔均','月收益','月回撤']].to_string(index=False))


if __name__ == '__main__':
    main()
