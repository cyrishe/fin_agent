"""Build auditable Chinese workbook inputs and a risk-aware R38 research note."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump


ROOT = Path('docs/stock_automl_runs/20261019_b2_rolling20_gate_r38')
CORE = ['B2原策略', '上月≤−10%', '20日≤−10%',
        '20日影子≤−10%', '30日≤−10%']
SCOPES = ['全期连续', '2025五月重置', '2026一月重置']


def rate(x):
    return '—' if x is None or pd.isna(x) else f'{x:+.2%}'


def drawdown(navs, opening):
    values = np.asarray([opening, *navs], dtype=float)
    return float(np.max(1-values/np.maximum.accumulate(values)))


def main():
    summary = pd.DataFrame(json.loads((ROOT/'summary.json').read_text()))
    monthly = pd.DataFrame(json.loads((ROOT/'monthly.json').read_text()))
    signals = pd.DataFrame(json.loads((ROOT/'signals.json').read_text()))
    daily = pd.DataFrame(json.loads((ROOT/'daily.json').read_text()))
    assert len(summary) == len(SCOPES)*8
    assert len(signals.query('账户 == "全期连续" and 方案 == "20日≤−10%"')) == 404
    assert len(daily.query('账户 == "全期连续" and 方案 == "20日≤−10%"')) == 629

    exposure = (daily.assign(held=daily.positions.gt(0).astype(int))
                .groupby(['账户', '方案']).held.agg(['sum', 'count']))
    overview = []
    for scope in SCOPES:
        base = summary[(summary.账户 == scope) & (summary.方案 == 'B2原策略')].iloc[0]
        for r in summary[summary.账户 == scope].to_dict('records'):
            held = exposure.loc[(scope, r['方案'])]
            score = (r['total_return']/r['max_drawdown']
                     if r['max_drawdown'] else None)
            overview.append([scope, r['方案'], int(r['entries']), int(r['跳过信号']),
                             int(r['停手交易日']), int(held['sum']),
                             float(held['sum']/held['count']), r['total_return'],
                             r['max_drawdown'], r['total_return']-base.total_return,
                             r['max_drawdown']-base.max_drawdown,
                             r['sharpe'], score, r['D2笔均'], r['亏超5%'],
                             r['跳过D2笔均'] if pd.notna(r['跳过D2笔均']) else None])

    year_rows = []
    for scheme in CORE:
        part = daily[(daily.账户 == '全期连续') & (daily.方案 == scheme)].sort_values('date')
        prev_nav = 1_000_000.
        for year, group in part.groupby(part.date.str[:4], sort=True):
            end = float(group.nav.iloc[-1])
            year_rows.append(['全期连续', year, scheme, int(len(group)),
                              int(monthly[(monthly.账户 == '全期连续') &
                                          (monthly.方案 == scheme) &
                                          (monthly.month.str[:4] == year)].entries.sum()),
                              end/prev_nav-1, drawdown(group.nav, prev_nav), end])
            prev_nav = end

    month_rows = []
    for scope in SCOPES:
        for month in sorted(monthly[monthly.账户 == scope].month.unique()):
            for scheme in CORE:
                r = monthly[(monthly.账户 == scope) & (monthly.month == month) &
                            (monthly.方案 == scheme)].iloc[0]
                month_rows.append([scope, month, scheme, int(r.entries), r.return_,
                                   r.drawdown, r.closing_nav, r.fees])

    nav_rows = []
    full = daily[(daily.账户 == '全期连续') & daily.方案.isin(CORE)]
    by_scheme = {s: full[full.方案 == s].set_index('date') for s in CORE}
    for date in by_scheme[CORE[0]].index:
        row = [date]
        for scheme in CORE:
            r = by_scheme[scheme].loc[date]
            row.extend([r.nav, r.drawdown, int(r.positions)])
        nav_rows.append(row)

    signal_rows = []
    filtered = signals[signals.方案.isin(['20日≤−10%', '20日影子≤−10%'])]
    for scope in SCOPES:
        for scheme in ['20日≤−10%', '20日影子≤−10%']:
            subset = filtered[(filtered.账户 == scope) & (filtered.方案 == scheme)]
            for r in subset.sort_values('date').to_dict('records'):
                signal_rows.append([scope, scheme, r['date'], r['d0_rank'], r['score'],
                                    None if pd.isna(r['rolling_return']) else r['rolling_return'],
                                    r['action'], r['buy_price'],
                                    r['d2_net_return'], r['entry_time'],
                                    None if pd.isna(r['d0_amount_100m']) else r['d0_amount_100m'],
                                    None if pd.isna(r['d0_turnover_pct']) else r['d0_turnover_pct']])

    payload = {
        '总览': {'note': '同起点比较收益、回撤、暴露；Δ相对各起点原B2。历史探索，不是独立盲测。',
               'headers': ['账户起点', '方案', '买入笔', '跳过信号', '停手日', '持仓日', '持仓日比',
                           '总收益', '最大回撤', '收益Δ', '回撤Δ', '夏普', '收益/回撤',
                           'D2笔均', '亏超5%', '跳过笔均'],
               'rows': overview, 'percent': [6, 7, 8, 9, 10, 13, 14, 15],
               'decimal': [11, 12]},
        '分年': {'note': '只看2024-03起连续账户；年份在同一资金路径上逐段切分，不重置。',
               'headers': ['账户', '年份', '方案', '交易日', '买入笔', '段收益', '段回撤', '段末净值'],
               'rows': year_rows, 'percent': [5, 6]},
        '逐月': {'note': '按账户估值所在月；三种起点独立从100万元启动，月末净值不可跨起点相加。',
               'headers': ['账户起点', '月份', '方案', '买入笔', '月收益', '月回撤', '月末净值', '费用'],
               'rows': month_rows, 'percent': [4, 5]},
        '逐日净值': {'note': '2024-03连续账户。净值已经按各规则自己的历史决策重算。',
                 'headers': ['日期', '原净值', '原回撤', '原持仓', '上月净值', '上月回撤', '上月持仓',
                             '20日净值', '20日回撤', '20日持仓', '影子净值', '影子回撤', '影子持仓',
                             '30日净值', '30日回撤', '30日持仓'],
                 'rows': nav_rows, 'percent': [2, 5, 8, 11, 14]},
        '20日信号': {'note': '自身/影子均只读D1前20个完整交易日净值；若买D2净收益是事后反事实。',
                 'headers': ['账户起点', '方案', 'D1日期', 'D0排名', '模型分', '20日收益', '动作',
                             '模拟买价', '若买D2净收益', '买入分钟', 'D0额亿', 'D0换手%'],
                 'rows': signal_rows, 'percent': [5, 8], 'decimal': [4, 7, 10, 11]},
    }
    dump(ROOT/'workbook_data.json', payload)

    # Static integrity checks before formatting the workbook.
    assert all(len(v['headers']) == len(row) for v in payload.values() for row in v['rows'])
    assert len(overview) == 24 and len(year_rows) == 15 and len(nav_rows) == 629
    full20 = summary[(summary.账户 == '全期连续') & (summary.方案 == '20日≤−10%')].iloc[0]
    reset20 = summary[(summary.账户 == '2025五月重置') & (summary.方案 == '20日≤−10%')].iloc[0]
    resetbase = summary[(summary.账户 == '2025五月重置') & (summary.方案 == 'B2原策略')].iloc[0]
    skip = filtered[(filtered.账户 == '全期连续') &
                    (filtered.方案 == '20日≤−10%') & (filtered.action == '跳过')]
    skip_year = skip.assign(year=skip.date.str[:4]).groupby('year').agg(
        笔数=('d2_net_return', 'size'), 笔均=('d2_net_return', 'mean'),
        胜率=('d2_net_return', lambda x: float(x.gt(0).mean()))).reset_index()
    stage = pd.DataFrame(year_rows, columns=payload['分年']['headers'])
    doc = f'''# B2 R38：滚动账户收益风控回放

工作簿：[B2_滚动账户风控20日_R38_20261019.xlsx](B2_滚动账户风控20日_R38_20261019.xlsx)。复算脚本：[账户回放](../../../scripts/experiment_b2_rolling_nav_gate_r38.py)、[结果与工作簿数据](../../../scripts/report_b2_rolling_nav_gate_r38.py)、[逐日因果核验](../../../scripts/verify_b2_rolling_nav_gate_r38.py)。已核验 24 个账户方案、2,247 次 D1 买前指标、逐月复利及逐日净值／回撤。

本实验冻结 R35 的两月验证 B2 日线模型、四 K 规则、D1 模拟买价、D2 收盘卖价和买卖各 0.1% 费用；不重训、不改选股。比较的是**账户层的暂停新开仓规则**，而非模型分类准确率。主要方案在 D1 买入决策前用本账户截至昨日的净值，计算最近 **20 个已完成交易日**累计收益；≤−10% 当天不买，已有持仓仍按原定 D2 收盘卖出。风控账户在暂停后继续用自己的净值逐日计算，不能拿未暂停的 B2 净值来决定何时恢复。

数据为 2024-03—2026-09 共 31 个完整月、629 个交易日、404 个原 B2 信号；2026-10 尚未结束，排除。空仓时用可用现金一半、有昨日持仓时用剩余现金，初始 100 万元，允许零碎股。20 日口径首 20 个交易日不触发；账户重置时窗口也重新预热。主方案 −10% 取自 R37 的上月亏超 10% 现象，未根据本轮结果挑阈值。另将真正的“上月≤−10%暂停整月”做成自洽对照，10/30 日及其他 20 日阈值只作敏感性检查。

## 同起点比较

| 起点 | 方案 | 买入 | 收益 | 最大回撤 | 夏普 | 持仓日比 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
'''
    for scope in SCOPES:
        for scheme in CORE:
            r = summary[(summary.账户 == scope) & (summary.方案 == scheme)].iloc[0]
            e = exposure.loc[(scope, scheme)]
            doc += (f"| {scope} | {scheme} | {int(r.entries)} | {rate(r.total_return)} | "
                    f"{r.max_drawdown:.2%} | {r.sharpe:.2f} | {e['sum']/e['count']:.1%} |\n")
    doc += f'''
全期连续 20 日规则将期末亏损从 −78.35% 缩到 {rate(full20.total_return)}，最大回撤从 93.66% 降到 {full20.max_drawdown:.2%}，但仍需忍受四分之三资产的峰谷跌幅。单看此段，会高估它在后来市场的普适性。**2025 年 5 月重新以 100 万元开始**，同一规则收益从 {rate(resetbase.total_return)} 降到 {rate(reset20.total_return)}，最大回撤却从 {resetbase.max_drawdown:.2%} 增到 {reset20.max_drawdown:.2%}；2026 年 1 月重置也同方向。这是收益、风险同时变差，不属于“用部分收益交换更低回撤”。

上月亏超 10% 的日历月暂停法，在 2025 年 5 月重置段收益 {rate(summary[(summary.账户 == '2025五月重置') & (summary.方案 == '上月≤−10%')].iloc[0].total_return)}、回撤 {summary[(summary.账户 == '2025五月重置') & (summary.方案 == '上月≤−10%')].iloc[0].max_drawdown:.2%}：确实用部分收益换来较小回撤。2026 年 1 月重置段没有触发，和 B2 完全一致。

另做“影子账户”对照：即使真实账户停手，仍按原 B2 信号记录一条不交易的纸面净值，只用其**前一日**为止的 20 日涨跌幅作为复工依据。这避免真实账户停手后净值长期横盘而信号失真。影子规则在全期连续表现好于自身净值规则，但 2025 年 5 月与 2026 年 1 月重置段仍未稳定胜过原 B2。30 日≤−10% 在 2025 年 5 月重置段同时提高收益、降低回撤；2026 年 1 月重置段回撤略降而收益略降。两者都是查看已知历史后的敏感性发现，不是新的已验证策略。

## 为什么 20 日硬停会错过修复

20 日规则在全期连续账户跳过 {len(skip)} 个信号；若按原模拟价格买入，这批交易按 D2 费后单笔平均收益为 {rate(skip.d2_net_return.mean())}。分年看：

| 年份 | 跳过笔数 | 若买 D2 费后笔均 | 若买胜率 |
| --- | ---: | ---: | ---: |
'''
    for r in skip_year.to_dict('records'):
        doc += f"| {r['year']} | {int(r['笔数'])} | {rate(r['笔均'])} | {r['胜率']:.1%} |\n"
    doc += '''
2024 年初持续下行时暂停确有防护；市场反弹后，滚动账户在低净值上继续检查最近 20 日，可能仍处于暂停状态。随旧亏损日移出窗口，即使没有新交易，20 日收益也会回升到阈值上方；恢复时点因此带有路径依赖。尤其在 2025 年 5 月重置段，20 日规则跳过的 30 笔原交易事后 D2 笔均 **+1.50%**、胜率 **63.3%**，可解释其收益和回撤同时恶化。逐笔“若买 D2 净收益”只是同价位的事后机会成本，不保证实盘能成交。

## 金融判断

应在**同一起始资本、同一开始日**比较累计收益、回撤、持仓暴露和夏普。降低出手频率本身通常会压低收益和波动；只有在相应回撤改善足以覆盖机会成本时才有价值。这里 20 日≤−10% 在 2025-05 和 2026-01 两段都被原 B2 同时在收益与最大回撤上压过；因此不宜把这个硬停规则上线。月度规则提供了更合理的收益—回撤交换，但尚需独立时序检验。影子净值、30 日窗口是后验探索，不能因为历史表格好看就直接择优部署。

所有变体共用已参与先前研究的历史行情，**不是新的独立测试集**；本次没有修改正式 B2/v1.0、线上脚本或数据库策略记录。分钟买价是 R35 的历史代理，未重新证明实盘逐笔可成交。完整逐日净值、逐月收益及所有 20 日门槛买入／跳过信号见本目录工作簿；可复算数据与来源哈希见 `specification.json`、`summary.json`、`monthly.json`、`daily.json`、`signals.json`。
'''
    (ROOT/'README.md').write_text(doc)
    print(ROOT/'README.md')
    print(ROOT/'workbook_data.json')


if __name__ == '__main__':
    main()
