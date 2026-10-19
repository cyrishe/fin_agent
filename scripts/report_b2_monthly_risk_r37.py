"""Month-by-month B2 loss diagnostics using frozen R34/R35 outputs only."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

from scripts.replay_limit_rebound_cash_r23 import dump, sha


OUT = Path('docs/stock_automl_runs/20261019_b2_monthly_risk_r37')
R36 = Path('docs/stock_automl_runs/20261019_b2_accuracy_return_r36/月度对照.json')
ROOTS = {
    '两月验证': (Path('outputs/stock_automl/b2_two_month_2024_r35_20261019'), '完整两月2024起'),
    '一月验证': (Path('outputs/stock_automl/b2_full_2024_r34_20261019'), '完整2024起'),
}


def numeric(x):
    return None if x is None or pd.isna(x) else float(x)


def wilson_lower(p: pd.Series, n: pd.Series) -> pd.Series:
    z = 1.96
    return (p + z*z/(2*n) - z*np.sqrt(p*(1-p)/n + z*z/(4*n*n)))/(1+z*z/n)


def enrich(source: pd.DataFrame, scheme: str) -> pd.DataFrame:
    folder, scope = ROOTS[scheme]
    x = source[(source.方案 == scheme) & (source.月份 < '2026-10')].sort_values('月份').copy()
    daily = {r['month']: r for r in json.loads((folder/'daily_metrics.json').read_text())
             if r['part'] == 'valid' and r['daily_validation'] == (2 if scheme == '两月验证' else 1)}
    x['训练验证差'] = x.训练Top1 - x.验证Top1
    x['验证环比波动'] = x.验证Top1.diff().abs()
    x['验证Wilson下界'] = wilson_lower(x.验证Top1, x.验证样本)
    x['验证基准率'] = x.月份.map(lambda m: daily[m]['open']['top1']['same_day_base'])
    x['验证超额'] = x.验证Top1 - x.验证基准率
    x['上月收益'] = x.月收益.shift(1)
    x['上月回撤'] = x.月回撤.shift(1)
    x['验证测试差'] = x.验证Top1 - x.测试Top1
    x['大亏'] = x.月收益.le(-.15)
    x['高回撤'] = x.月回撤.ge(.20)
    ledger = pd.read_pickle(folder/'ledger.pkl')
    ledger = ledger[ledger.scope.eq(scope) & ledger.variant.eq('D2S2' if scheme == '两月验证' else 'D1S1')]
    closed = ledger[ledger.exit_date.notna()].copy()
    closed['month'] = closed.d0_date.str[:7]
    bad = closed.groupby('month').return_.apply(lambda v: float(v.lt(-.05).mean()))
    x['D2亏超5%'] = x.月份.map(bad)
    assert x.月份.is_unique and x.月收益.notna().all()
    return x


def correlation(g: pd.DataFrame, metric: str, outcome: str) -> dict:
    pair = g[[metric, outcome]].dropna()
    if len(pair) < 4 or pair[metric].nunique() < 2 or pair[outcome].nunique() < 2:
        return {'月数': len(pair), 'Pearson': None, 'Spearman': None}
    return {'月数': len(pair), 'Pearson': float(pearsonr(pair[metric], pair[outcome]).statistic),
            'Spearman': float(spearmanr(pair[metric], pair[outcome]).statistic)}


def split(g: pd.DataFrame, metric: str, mask: pd.Series) -> dict:
    use = g[metric].notna()
    pos, neg = g[use & mask], g[use & ~mask]
    return {'触发月': len(pos), '触发大亏': int(pos.大亏.sum()),
            '触发大亏率': numeric(pos.大亏.mean()), '触发均收益': numeric(pos.月收益.mean()),
            '未触发月': len(neg), '未触发大亏': int(neg.大亏.sum()),
            '未触发大亏率': numeric(neg.大亏.mean()), '未触发均收益': numeric(neg.月收益.mean()),
            '触发月份': '、'.join(pos.月份.tolist())}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = pd.DataFrame(json.loads(R36.read_text()))
    primary = enrich(source, '两月验证')
    alternative = enrich(source, '一月验证')
    metrics = [
        ('训练Top1', '月前已知'), ('验证Top1', '月前已知'),
        ('训练验证差', '月前已知'), ('验证环比波动', '月前已知'),
        ('验证样本', '月前已知'), ('验证Wilson下界', '月前已知'),
        ('验证超额', '月前已知'), ('形态训练命中', '月前已知'),
        ('形态验证命中', '月前已知'), ('形态验证样本', '月前已知'),
        ('上月收益', '月前已知'), ('上月回撤', '月前已知'),
        ('测试Top1', '月后诊断'), ('实买三日再板', '月后诊断'),
        ('验证测试差', '月后诊断'),
    ]
    corr = []
    for period, group in [('全期', primary), ('2024年', primary[primary.月份.lt('2025-01')]),
                          ('2025—26年', primary[primary.月份.ge('2025-01')])]:
        for metric, timing in metrics:
            bad = group[group.大亏][metric].dropna()
            other = group[~group.大亏][metric].dropna()
            corr.append({'时期': period, '统计量': metric, '可知时间': timing,
                         **correlation(group, metric, '月收益'),
                         '与D2笔均相关': correlation(group, metric, 'D2笔均')['Pearson'],
                         '大亏月均值': numeric(bad.mean()) if len(bad) else None,
                         '其余月均值': numeric(other.mean()) if len(other) else None})
    threshold_defs = [
        ('训练Top1≥85%', '训练Top1', lambda g: g.训练Top1.ge(.85)),
        ('训练－验证≥30个百分点', '训练验证差', lambda g: g.训练验证差.ge(.30)),
        ('验证Top1≤50%', '验证Top1', lambda g: g.验证Top1.le(.50)),
        ('验证率环比变动≥10个百分点', '验证环比波动', lambda g: g.验证环比波动.ge(.10)),
        ('验证Top1样本<15', '验证样本', lambda g: g.验证样本.lt(15)),
        ('形态验证命中≤50%', '形态验证命中', lambda g: g.形态验证命中.le(.50)),
        ('上月收益≤−10%', '上月收益', lambda g: g.上月收益.le(-.10)),
    ]
    threshold = []
    for caption, metric, flag in threshold_defs:
        threshold.append({'范围': '两月B2全期', '条件': caption, **split(primary, metric, flag(primary))})
        if metric == '上月收益':
            late = primary[primary.月份.ge('2025-01')]
            threshold.append({'范围': '两月B2·2025—26', '条件': caption,
                              **split(late, metric, flag(late))})
    for caption, metric, flag in threshold_defs:
        if metric in ('训练Top1', '训练验证差', '验证环比波动', '上月收益'):
            threshold.append({'范围': '一月方案复核', '条件': caption,
                              **split(alternative, metric, flag(alternative))})
    columns = ['月份', '日线训练月', '日线验证月', '训练Top1', '验证Top1', '训练验证差',
               '验证环比波动', '验证样本', '验证Wilson下界', '形态验证命中', '形态验证样本',
               '上月收益', '测试Top1', '验证测试差', '实买三日再板', 'D2笔均',
               'D2亏超5%', '月收益', '月回撤', '大亏', '高回撤']
    primary_rows = [{key: (bool(row[key]) if key in ('大亏','高回撤') else
                           (None if pd.isna(row[key]) else row[key])) for key in columns}
                    for _, row in primary.iterrows()]
    dump(OUT/'逐月诊断.json', primary_rows)
    dump(OUT/'相关检验.json', corr)
    dump(OUT/'阈值分组.json', threshold)
    dump(OUT/'一月方案复核.json', [
        {key: (bool(row[key]) if key in ('大亏','高回撤') else
               (None if pd.isna(row[key]) else row[key])) for key in columns}
        for _, row in alternative.iterrows()])
    inputs = [R36] + [p for root, _ in ROOTS.values() for p in
                      (root/'daily_metrics.json', root/'ledger.pkl')]
    dump(OUT/'来源哈希.json', [{'file': str(p), 'sha256': sha(p)} for p in inputs])
    corr_cols = ['时期', '统计量', '可知时间', '月数', 'Pearson', 'Spearman',
                 '与D2笔均相关', '大亏月均值', '其余月均值']
    threshold_cols = ['范围', '条件', '触发月', '触发大亏', '触发大亏率', '触发均收益',
                      '未触发月', '未触发大亏', '未触发大亏率', '未触发均收益', '触发月份']
    workbook = {
        '逐月诊断': {'headers': columns,
                 'rows': [[int(r[k]) if k in ('大亏','高回撤') else r[k] for k in columns]
                          for r in primary_rows],
                 'percent': [3,4,5,6,8,9,11,12,13,14,15,16,17,18],
                 'note': '每行一个测试月；月前可知列在左，测试月实际与收益在右。大亏=月收益≤−15%，高回撤=月内最大回撤≥20%。2026-10未完月不纳入。'},
        '相关检验': {'headers': corr_cols,
                 'rows': [[r.get(k) for k in corr_cols] for r in corr],
                 'decimal': [4,5,6],
                 'note': '原两月 B2 的月份之间相关性。后两列均值按原量纲：命中率/收益为比例，样本数为笔。分段检查方向是否稳定；月后诊断量不能事先风控。'},
        '阈值分组': {'headers': threshold_cols,
                 'rows': [[r.get(k) for k in threshold_cols] for r in threshold],
                 'percent': [4,5,8,9],
                 'note': '直观阈值的历史分组，仅描述，不是经独立验证的交易规则。“上月收益”在首月未知，比较时排除首月。末列可定位触发月份。'},
    }
    dump(OUT/'workbook_data.json', workbook)
    print('primary months', len(primary), 'large losses', int(primary.大亏.sum()))
    for r in corr:
        if r['时期']=='全期' and r['统计量'] in ('训练Top1','训练验证差','验证环比波动','上月收益','测试Top1'):
            print('correlation', r)
    for r in threshold:
        if r['范围']=='两月B2全期': print('split',r['条件'],r['触发月'],r['触发大亏'],r['未触发月'],r['未触发大亏'])


if __name__ == '__main__':
    main()
