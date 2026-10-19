"""Isolate the weak 2024 B2 regime and diagnose what R39 gates actually fix."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_rolling_nav_gate_r38 import CALENDAR, FEE, INITIAL, SOURCE
from scripts.replay_limit_rebound_cash_r23 import dump, replay, sha


ROOT = Path('docs/stock_automl_runs/20261019_b2_recovery_gate_r39')
SCHEMES = ['B2原策略', '30日单门槛', '30日+真实10日',
           '30日+影子10日', '影子30日+影子10日']
START, END = '2024-03-01', '2024-12-31'
RANDOM_DRAWS = 300
SEED = 20261019


def pct(value):
    return '—' if value is None or pd.isna(value) else f'{value:+.2%}'


def maybe(value):
    return None if value is None or pd.isna(value) else float(value)


def main():
    daily = pd.DataFrame(json.loads((ROOT/'daily.json').read_text()))
    monthly = pd.DataFrame(json.loads((ROOT/'monthly.json').read_text()))
    signals = pd.DataFrame(json.loads((ROOT/'signals.json').read_text()))
    daily = daily[(daily.账户 == '全期连续') & daily.date.between(START, END)]
    monthly = monthly[(monthly.账户 == '全期连续') & monthly.month.between('2024-03', '2024-12')]
    signals = signals[(signals.账户 == '全期连续') & signals.date.between(START, END)]
    assert len(daily[daily.方案 == 'B2原策略']) == 205
    assert len(signals[signals.方案 == 'B2原策略']) == 117
    overview, month_rows, quality = [], [], []
    for scheme in SCHEMES:
        d = daily[daily.方案 == scheme].sort_values('date')
        v = np.r_[INITIAL, d.nav.to_numpy()]
        s = signals[signals.方案 == scheme]
        bought = s[s.action == '买入'].d2_net_return
        skipped = s[s.action == '跳过'].d2_net_return
        m = monthly[monthly.方案 == scheme]
        ret = float(v[-1]/v[0]-1)
        dd = float(np.max(1-v/np.maximum.accumulate(v)))
        overview.append([scheme, int(len(bought)), int(len(skipped)),
                         int(s.recovery_override.sum()), ret, dd, float(v[-1]),
                         int(m.return_.gt(0).sum()), float(m.return_.min())])
        quality.append([scheme, int(len(bought)), maybe(bought.mean()),
                        maybe(bought.gt(0).mean()), maybe(bought.lt(-.05).mean()),
                        int(len(skipped)), maybe(skipped.mean()),
                        maybe(skipped.gt(0).mean()), maybe(skipped.lt(-.05).mean())])
        np.testing.assert_allclose(np.prod(1+m.return_.to_numpy())-1,
                                   ret, rtol=0, atol=1e-10)
    for month in sorted(monthly.month.unique()):
        for scheme in SCHEMES:
            r = monthly[(monthly.month == month) & (monthly.方案 == scheme)].iloc[0]
            month_rows.append([month, scheme, int(r.entries), r.return_,
                               r.drawdown, r.closing_nav])

    raw = pd.read_pickle(SOURCE/'signals.pkl')
    raw = raw[raw.d1_date.between(START, END)].copy()
    raw['month'] = raw.d1_date.str[:7]
    assert len(raw) == 117 and not raw.d1_date.duplicated().any()
    calendar = json.loads(CALENDAR.read_text())
    reference_signals = signals[signals.方案 == '30日单门槛']
    counts = reference_signals[reference_signals.action == '买入'].date.str[:7].value_counts().to_dict()
    assert sum(counts.values()) == 62
    rng = np.random.default_rng(SEED)
    benchmark = []
    for kind in ['全期等笔数随机', '逐月等笔数随机']:
        ret, dd = [], []
        for _ in range(RANDOM_DRAWS):
            if kind == '全期等笔数随机':
                idx = rng.choice(raw.index.to_numpy(), 62, replace=False)
            else:
                idx = np.concatenate([
                    rng.choice(part.index.to_numpy(), counts.get(month, 0), replace=False)
                    for month, part in raw.groupby('month') if counts.get(month, 0)
                ])
            result, *_ = replay(raw.loc[idx].drop(columns='month'), calendar,
                                start=START, end=END, initial=INITIAL, fee_rate=FEE)
            ret.append(result['total_return'])
            dd.append(result['max_drawdown'])
        benchmark.append(dict(
            kind=kind, draws=RANDOM_DRAWS, seed=SEED,
            return_q05=float(np.quantile(ret, .05)),
            return_median=float(np.median(ret)),
            return_q95=float(np.quantile(ret, .95)),
            drawdown_q05=float(np.quantile(dd, .05)),
            drawdown_median=float(np.median(dd)),
            drawdown_q95=float(np.quantile(dd, .95)),
            random_higher_return=float(np.mean(np.asarray(ret) > overview[1][4])),
            random_lower_drawdown=float(np.mean(np.asarray(dd) < overview[1][5]))))
    dump(ROOT/'2024_analysis.json', dict(
        source_sha256=sha(SOURCE/'signals.pkl'), calendar_sha256=sha(CALENDAR),
        year=2024, eligible_months=['2024-03', '2024-12'],
        overview=overview, trade_quality=quality, random_controls=benchmark))

    random_rows = [[x['kind'], x['draws'], x['return_q05'], x['return_median'],
                    x['return_q95'], x['drawdown_q05'], x['drawdown_median'],
                    x['drawdown_q95'], x['random_higher_return'],
                    x['random_lower_drawdown']] for x in benchmark]
    payload = {
        '2024总览': {'note': '原两月验证B2首次测试2024-03；2024-01/02是形态验证月，不能当测试。',
                    'headers': ['方案', '买入笔', '跳过笔', '恢复笔', '期间收益',
                                '最大回撤', '期末净值', '盈利月', '最差月收益'],
                    'rows': overview, 'percent': [4, 5, 8]},
        '2024逐月': {'note': '2024-03至12月，同一账户从100万元连续回放。',
                    'headers': ['月份', '方案', '买入笔', '月收益', '月回撤', '月末净值'],
                    'rows': month_rows, 'percent': [3, 4]},
        '已买与跳过': {'note': 'D2费后逐笔；跳过组是原信号的事后模拟交易结果。',
                    'headers': ['方案', '已买笔', '已买笔均', '已买胜率', '已买亏超5%',
                                '跳过笔', '跳过笔均', '跳过胜率', '跳过亏超5%'],
                    'rows': quality, 'percent': [2, 3, 4, 6, 7, 8]},
        '等笔数诊断': {'note': '固定随机种子300次；非可交易策略，只诊断改善是否超过单纯减少买入。',
                    'headers': ['对照', '抽样次', '收益5分位', '收益中位', '收益95分位',
                                '回撤5分位', '回撤中位', '回撤95分位',
                                '随机收益更高', '随机回撤更低'],
                    'rows': random_rows, 'percent': [2, 3, 4, 5, 6, 7, 8, 9]},
    }
    assert all(len(v['headers']) == len(r) for v in payload.values() for r in v['rows'])
    dump(ROOT/'workbook_data_2024.json', payload)
    doc = '''# R39A：先看模型失效的 2024 年

用户指出，2024 年原 B2 大幅亏损，不能只用 2025—26 年的盈利评价账户风控。本页将 R39 已完成的两月验证 B2 回放 **单独截到 2024 年 12 月 31 日**，账户从 2024 年 3 月的 100 万元开始；不拿 2025—26 年盈利抵消其损失。专表：[B2_2024失效区间风控_R39A_20261019.xlsx](B2_2024失效区间风控_R39A_20261019.xlsx)。逐日账与所有交易仍见 [R39 主报告](README.md)。

同口径最早只能从 **2024 年 3 月**测试：日线折外选股与四 K 第二层需要此前六个月训练、两个月验证。2024 年 1—2 月虽有行情与分钟数据，但属于原两月 B2 第二层验证月；把它们填进测试会让规则用到尚未形成的验证结果。另一个一月验证模型可从 1 月测试，但那是不同方案，不能拼入本表。[R35 首测月来源](../20261019_b2_both_2024_r35/README.md)。

## 2024 年 3—12 月同起点账户

| 方案 | 买入 | 跳过 | 收益 | 最大回撤 | 期末净值 |
| --- | ---: | ---: | ---: | ---: | ---: |
'''
    for r in overview:
        doc += (f'| {r[0]} | {r[1]} | {r[2]} | {pct(r[4])} | '
                f'{r[5]:.2%} | {r[6]:,.0f} 元 |\n')
    doc += '''
## 必须看已买股票本身

| 方案 | 已买 D2 费后笔均 | 已买胜率 | 已买亏超 5% | 跳过 D2 费后笔均 |
| --- | ---: | ---: | ---: | ---: |
'''
    for r in quality:
        doc += f'| {r[0]} | {pct(r[2])} | {pct(r[3])} | {pct(r[4])} | {pct(r[6])} |\n'
    doc += f'''
原 B2 的 117 笔实际信号，D2 费后平均 **−2.65%**、仅 **34.2%** 盈利、**49.6%** 亏超 5%；这首先是交易模型在 2024 年失效，不能把问题缩成“账户回撤阈值不够好”。[R36 月度核查](../20261019_b2_accuracy_return_r36/README.md)另从分类角度记录训练／验证／测试 Top1 三日再板率月均约 79.45%／49.05%／44.92%，分类分数并未带来 D2 可实现的盈利。30 日单门槛少买 55 笔，却仍有 **62 笔**被买入，笔均 **−2.11%**、亏超 5% 占 **51.6%**，期末仍亏 **56.76%**、最大回撤 **61.20%**。30 日＋真实 10 日完全没有改变 2024 年交易。

30 日＋影子 10 日让 2024 收益由 −56.76% 改为 −53.22%，但最大回撤由 61.20% 升到 64.22%，属于较少亏损和更大峰谷风险的交换。全影子 30／10 日的 2024 损失降到 −23.67%、回撤降到 41.62%，已买笔均 −0.37%、跳过笔均 −4.54%，确有一定区分度；但它仍亏损，而且在 2025—26 年重置回放明显落后 30 日单门槛，不能靠 2024 单段选为正式规则。

30 日规则启动前必须先积累 30 个完整交易日，所以 **2024 年 3 月 −24.83%** 的亏损无法被它保护。该规则在 2024 年 6 月和 10 月一笔也没买；10 月原 B2 回升 +33.12%，被完全错过。风控既挡了坏月，也会错过修复月。

## 改善是否只是少交易

用固定随机种子在原 117 笔信号中做 300 次非交易性诊断，每次与 30 日单门槛同样只保留 62 笔：

| 随机对照 | 收益中位 | 回撤中位 | 随机收益超过30日规则 | 随机回撤低于30日规则 |
| --- | ---: | ---: | ---: | ---: |
'''
    for x in benchmark:
        doc += (f"| {x['kind']} | {pct(x['return_median'])} | "
                f"{x['drawdown_median']:.2%} | {x['random_higher_return']:.1%} | "
                f"{x['random_lower_drawdown']:.1%} |\n")
    doc += '''
“全期等笔数”还检验是否选中了较好的月份；“逐月等笔数”固定每月买入数量，只随机改月内买入日。30 日规则对全期随机的回撤明显更好；固定月度笔数后，约三成随机样本在收益或回撤上优于它，说明改善主要来自**避开某些亏损月份和降低暴露**，并非精确识别每笔好坏。这不是可交易基准，也不能给后验研发做显著性证明。

**结论：2024 年没有一个已试 30／10 日门槛使原 B2 变成盈利策略。** 若首先要防止 2024 级别的模型失效，全影子 30／10 日比真实账户 30 日单门槛更值得继续核查；但它仍亏23.67%、回撤41.62%，且近期表现较弱。账户风控不能代替对 2024 年日线排序、四 K 选规和交易信号质量的再检查。全部区间均已参与研发，不是独立盲测；正式 B2 保持不变。
'''
    (ROOT/'2024_ANALYSIS.md').write_text(doc)
    print(ROOT/'2024_ANALYSIS.md')
    print(ROOT/'workbook_data_2024.json')


if __name__ == '__main__':
    main()
