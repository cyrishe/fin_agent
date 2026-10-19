"""Publish the R39 B2 30-day loss / 10-day recovery comparison."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump


ROOT = Path('docs/stock_automl_runs/20261019_b2_recovery_gate_r39')
SCOPES = ['全期连续', '2025五月重置', '2026一月重置']
SCHEMES = ['B2原策略', '30日单门槛', '30日+真实10日',
           '30日+影子10日', '影子30日+影子10日']


def pct(value):
    return '—' if value is None or pd.isna(value) else f'{value:+.2%}'


def num(value):
    return None if value is None or pd.isna(value) else float(value)


def main():
    summary = pd.DataFrame(json.loads((ROOT/'summary.json').read_text()))
    monthly = pd.DataFrame(json.loads((ROOT/'monthly.json').read_text()))
    daily = pd.DataFrame(json.loads((ROOT/'daily.json').read_text()))
    signals = pd.DataFrame(json.loads((ROOT/'signals.json').read_text()))
    assert len(summary) == 15
    overview, month_rows, daily_rows, differences, signal_rows = [], [], [], [], []
    for scope in SCOPES:
        reference = summary[(summary.账户 == scope) & (summary.方案 == '30日单门槛')].iloc[0]
        original = summary[(summary.账户 == scope) & (summary.方案 == 'B2原策略')].iloc[0]
        assert len(signals[(signals.账户 == scope) & (signals.方案 == 'B2原策略')]) == original.signals
        for scheme in SCHEMES:
            r = summary[(summary.账户 == scope) & (summary.方案 == scheme)].iloc[0]
            overview.append([scope, scheme, int(r.entries), int(r['跳过信号']),
                             int(r['恢复信号']), r.total_return, r.max_drawdown,
                             r.sharpe, r.total_return-reference.total_return,
                             r.max_drawdown-reference.max_drawdown,
                             num(r['恢复D2笔均']), num(r['跳过D2笔均'])])
            current = signals[(signals.账户 == scope) & (signals.方案 == scheme)]
            if scheme != '30日单门槛' and scheme != 'B2原策略':
                base = signals[(signals.账户 == scope) & (signals.方案 == '30日单门槛')]
                base_days = set(base.loc[base.action == '买入', 'date'])
                now_days = set(current.loc[current.action == '买入', 'date'])
                added = current[current.date.isin(now_days-base_days)]
                removed = base[base.date.isin(base_days-now_days)]
                differences.append([scope, scheme, len(added),
                                    num(added.d2_net_return.mean()), len(removed),
                                    num(removed.d2_net_return.mean()),
                                    int(current.recovery_override.sum()),
                                    num(current.loc[current.recovery_override == 1,
                                                    'd2_net_return'].mean())])
        for month in sorted(monthly[monthly.账户 == scope].month.unique()):
            for scheme in SCHEMES:
                r = monthly[(monthly.账户 == scope) & (monthly.方案 == scheme) &
                            (monthly.month == month)].iloc[0]
                month_rows.append([scope, month, scheme, int(r.entries),
                                   r.return_, r.drawdown, r.closing_nav])
        for scheme in ['30日单门槛', '30日+真实10日', '30日+影子10日']:
            current = signals[(signals.账户 == scope) & (signals.方案 == scheme)]
            for r in current.sort_values('date').to_dict('records'):
                signal_rows.append([scope, scheme, r['date'], r['d0_rank'],
                                    r['score'], num(r['own30']), num(r['own10']),
                                    num(r['shadow10']), int(r['loss_trigger']),
                                    int(r['recovery_override']), r['action'],
                                    r['buy_price'], r['d2_net_return']])
    full = daily[daily.账户 == '全期连续']
    by_scheme = {x: full[full.方案 == x].set_index('date') for x in SCHEMES}
    for date in by_scheme[SCHEMES[0]].index:
        row = [date]
        for scheme in SCHEMES:
            r = by_scheme[scheme].loc[date]
            row.extend([r.nav, r.drawdown])
        daily_rows.append(row)
    payload = {
        '总览': {'note': 'Δ相对同起点30日单门槛；2024-03连续、2025-05重置、2026-01重置不可混算。',
               'headers': ['账户起点', '方案', '买入笔', '跳过笔', '恢复笔', '总收益',
                           '最大回撤', '夏普', '收益Δ30日', '回撤Δ30日', '恢复D2笔均', '跳过D2笔均'],
               'rows': overview, 'percent': [5, 6, 8, 9, 10, 11], 'decimal': [7]},
        '逐月': {'note': '各方案独立按原B2信号逐日重新回放；月收益按账户净值归属。',
               'headers': ['账户起点', '月份', '方案', '买入笔', '月收益', '月回撤', '月末净值'],
               'rows': month_rows, 'percent': [4, 5]},
        '逐日净值': {'note': '只展示2024-03起的连续账户；每个方案都保留自己的资金路径。',
                 'headers': ['日期', '原净值', '原回撤', '30日净值', '30日回撤',
                             '真实10净值', '真实10回撤', '影子10净值', '影子10回撤',
                             '全影子净值', '全影子回撤'],
                 'rows': daily_rows, 'percent': [2, 4, 6, 8, 10]},
        '交易差异': {'note': '相对30日单门槛：新增/少买的D2笔均是同价位事后反事实，非账户收益差。',
                 'headers': ['账户起点', '方案', '新增买入', '新增D2笔均',
                             '少买', '少买D2笔均', '10日恢复笔', '恢复D2笔均'],
                 'rows': differences, 'percent': [3, 5, 7]},
        '信号明细': {'note': 'D1买前仅用昨日及更早净值；暂停仍计算原选股与影子账户。',
                 'headers': ['账户起点', '方案', 'D1日期', 'D0排名', '模型分',
                             '30日实益', '10日实益', '10日影益', '30日亏',
                             '10日恢复', '动作', '模拟买价', '若买D2净益'],
                 'rows': signal_rows, 'percent': [5, 6, 7, 12], 'decimal': [4, 11]},
    }
    assert all(len(v['headers']) == len(r) for v in payload.values() for r in v['rows'])
    assert len(daily_rows) == 629 and len(signal_rows) == 2247
    dump(ROOT/'workbook_data.json', payload)

    def result(scope, scheme):
        return summary[(summary.账户 == scope) & (summary.方案 == scheme)].iloc[0]

    doc = '''# B2 R39：30 日亏损门槛加入近 10 日恢复条件

工作簿：[B2_30日亏损10日恢复_R39_20261019.xlsx](B2_30日亏损10日恢复_R39_20261019.xlsx)。复算代码：[逐日回放](../../../scripts/experiment_b2_recovery_gate_r39.py)、[报表](../../../scripts/report_b2_recovery_gate_r39.py)、[因果核验](../../../scripts/verify_b2_recovery_gate_r39.py)。已核验 15 组账户、3,745 个买入前信号计算、月收益复利及逐日净值／回撤。沿用 R35 冻结的两月验证 B2 信号及 D1 买、D2 收盘卖、双边各 0.1% 费用；没有重训日线模型或四 K 规则。2024-03—2026-09 共 31 个完整月，排除尚未结束的 2026-10。

**暂停的含义：只停真实买入。** 每天仍计算 D0 Top3、D1 四 K 规则和原本会买的唯一候选，逐日保存被跳过的信号；影子账户假设照原 B2 每天交易，用已完成交易日的收盘净值监测策略是否恢复。所有判断在 D1 买前，只能读取截至前一交易日的账户数据。

本轮重点条件是：近 30 个已完成交易日收益 **≤−10%**，且近 10 个已完成交易日收益 **<+1%**，才暂停当天的新买。比较“10 日真实账户净值”和“10 日影子账户净值”两种恢复判断；另列全影子 30／10 日作敏感性检查。每个方案按自身交易结果重新回放资金，不能把不同方案的买卖信号套在同一条净值线上。

## 同起点结果

| 起点 | 方案 | 买入 | 跳过 | 收益 | 最大回撤 | 夏普 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
'''
    for scope in SCOPES:
        for scheme in SCHEMES:
            r = result(scope, scheme)
            doc += (f"| {scope} | {scheme} | {int(r.entries)} | {int(r['跳过信号'])} | "
                    f"{pct(r.total_return)} | {r.max_drawdown:.2%} | {r.sharpe:.2f} |\n")
    doc += '''
## 恢复信号到底带来什么

真实账户停手后，近 10 日净值往往横盘，难以显示策略恢复。“30 日＋真实 10 日”在全期 404 个原信号里只比单门槛多买 **1 笔**，那笔 D2 费后 **−7.28%**；2026 年 1 月重置段一笔也没有新增。它在执行层几乎等同原 30 日硬停。

影子 10 日能提供停手期间的行情反馈，但 **≥+1% 并非盈利交易的可靠恢复信号**。2025 年 5 月重置段，相比 30 日单门槛，加入影子 10 日后新增买入 **6 笔**，这些股票 D2 费后笔均 **−5.02%**；资金路径改变又少买了 **3 笔**，其事后 D2 笔均 **+8.23%**。因此账户收益从 +176.52% 降至 +111.02%，回撤从 30.25% 增至 35.16%。2026 年 1 月重置段新增 4 笔的事后 D2 笔均 **−2.81%**，收益与回撤也同时恶化。

从 2024 年 3 月连续起步，影子 10 日版本改善了早期亏损；全影子 30／10 日甚至转正，但在两个近期重置段不如 30 日单门槛。起点与市场阶段改变会改变触发、复工、后续资金规模，因此不能用早期灾难段的优势证明近期适用。

## 金融判断

同起点比较，现有 **30 日单门槛**仍是这轮已试方案在 2025-05 重置段收益和回撤同时较好的方案；在 2026-01 重置段，它以约 1.66 个百分点的收益换取约 1.57 个百分点的回撤改善，夏普从 2.32 到 2.41。新增的 10 日 +1% 条件没有改善这两个近期区间的风险收益交换。30 日单门槛本身来自已看过的历史，仍只能列为研究候选；R39 的所有结果也不是独立盲测，正式 B2/v1.0、生产选股及数据库记录不变。

逐日和逐笔的可复算输出及输入哈希见 `specification.json`、`summary.json`、`monthly.json`、`daily.json`、`signals.json`。分钟买价是冻结 R35 的历史代理价，本轮没有新增实盘可成交性验证。
'''
    (ROOT/'README.md').write_text(doc)
    print(ROOT/'README.md')
    print(ROOT/'workbook_data.json')


if __name__ == '__main__':
    main()
