"""Readable, reconciled B2 January 2024–October 2026 research workbook inputs."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_full_2024_r34 import OUT, FIRST, LAST, configure
from scripts.report_b2_d2_metrics_r31 import metrics
from scripts.report_b2_validation_r31 import table
from scripts.replay_limit_rebound_cash_r23 import dump

DOC = Path('docs/stock_automl_runs/20261019_b2_full_2024_r34')
CASES = [('完整2024起', 'D1S1'), ('新增2024段', 'D1S1'),
         ('2025重置复核', 'D1S1'), ('共同区间', 'D2S2'), ('共同区间', 'D1S1')]
LABELS = {'D1S1': '两层一月', 'D2S2': '两层两月'}


def at(monthly, scope, variant, month, field):
    x = monthly[monthly.scope.eq(scope) & monthly.variant.eq(variant) & monthly.month.eq(month)]
    return x.iloc[0][field] if len(x) else None


def main() -> None:
    previous, r = configure()
    DOC.mkdir(parents=True, exist_ok=True)
    accounts = json.loads((OUT / 'accounts.json').read_text())
    monthly = pd.DataFrame(json.loads((OUT / 'monthly.json').read_text()))
    ledger = pd.read_pickle(OUT / 'ledger.pkl')
    nav = pd.read_pickle(OUT / 'nav.pkl')
    rules = json.loads((OUT / 'all_rules.json').read_text())
    daily = json.loads((OUT / 'daily_metrics.json').read_text())
    ordered = [next(a for a in accounts if a['scope'] == s and a['variant'] == v)
               for s, v in CASES]
    summary, d2 = [], []
    for account in ordered:
        scope, variant = account['scope'], account['variant']
        lines = monthly[monthly.scope.eq(scope) & monthly.variant.eq(variant)]
        group = ledger[ledger.scope.eq(scope) & ledger.variant.eq(variant)]
        known = group[group.observed_3]
        summary.append([f'{LABELS[variant]}·{scope}', account['entries'], account['closed'],
            account['pending'], int(known.label_3.sum()), len(known),
            float(known.label_3.mean()) if len(known) else None,
            account['total_return'], account['max_drawdown'], account['sharpe'],
            float(lines.return_.mean()), int(lines.return_.gt(0).sum()),
            float(lines.return_.min()), float(lines.drawdown.max())])
        for period in ['总体', *sorted(group.entry_date.str[:7].unique())]:
            subset = group if period == '总体' else group[group.entry_date.str.startswith(period)]
            d2.append(dict(scope=scope, variant=variant, period=period, **metrics(subset)))
    dump(OUT / 'd2_metrics.json', d2)

    monthly_rows = []
    for month in [str(m) for m in pd.period_range(FIRST, LAST, freq='M')]:
        g = lambda s, v, k: at(monthly, s, v, month, k)
        monthly_rows.append([month + '-01',
            g('完整2024起', 'D1S1', 'return_'), g('完整2024起', 'D1S1', 'drawdown'),
            g('完整2024起', 'D1S1', 'entries'), g('完整2024起', 'D1S1', 'closing_nav'),
            g('共同区间', 'D2S2', 'return_'), g('共同区间', 'D1S1', 'return_'),
            g('共同区间', 'D2S2', 'drawdown'), g('共同区间', 'D1S1', 'drawdown'),
            g('共同区间', 'D2S2', 'entries'), g('共同区间', 'D1S1', 'entries')])
    d2_rows = [[x['period'] + '-01' if x['period'] != '总体' else '总体',
        LABELS[x['variant']], x['scope'], x['closed'], x['pending'],
        x['win_rate'], x['mean'], x['median'], x['bad5_rate'],
        x['d2_limit_rate'], x['d12_limit_rate'], x['only_d3']] for x in d2]
    daily_rows = []
    for item in daily:
        for key, caption in [('all', '原涨停池'), ('open', '次日开盘打开')]:
            value = item[key]
            daily_rows.append([item['month'] + '-01', item['daily_validation'],
                {'train': '训练拟合内', 'valid': '验证', 'forecast': '测试'}[item['part']],
                caption, value['known'], value['rate'], value['ap'],
                value['top1']['n'], value['top1']['rate'], value['top3']['n'],
                value['top3']['rate'], value['top5']['n'], value['top5']['rate']])
    rule_rows = []
    for item in sorted(rules, key=lambda x: (x['month'], x['variant'])):
        if item['variant'] == 'D2S2' and item['month'] < '2025-05':
            continue
        w = r.window(item['month'], 1 if item['variant'] == 'D1S1' else 2)
        rule_rows.append([item['month'] + '-01', LABELS[item['variant']],
            '至'.join(w['train']), '至'.join(w['valid']),
            '至'.join(item['window']['train']), '至'.join(item['window']['valid']),
            item['selected']['text'].replace('五分', '四分'),
            item['train_base']['known'], item['train_selected']['known'],
            item['train_selected']['hit'], item['valid_base']['known'],
            item['valid_selected']['known'], item['valid_selected']['hit']])

    wanted = ((ledger.variant.eq('D1S1') & ledger.scope.eq('完整2024起')) |
              (ledger.variant.eq('D2S2') & ledger.scope.eq('共同区间')))
    trades = []
    for x in ledger[wanted].sort_values(['entry_date', 'variant']).itertuples():
        mv = np.exp(x.log_float_mv_proxy) / 1e8 if pd.notna(x.log_float_mv_proxy) else None
        trades.append([x.entry_date, LABELS[x.variant], x.d0_rank, x.score,
            x.rule_text, x.high5, x.low5, x.body5, x.pullback5, x.open_gap,
            x.entry_time, x.buy, x.buy / x.d1_preclose - 1, x.exit_date, x.sell,
            x.return_, x.cost, x.pnl, int(x.label_3) if x.observed_3 else None,
            x.board_streak_days, x.d0_turnover_pct, x.d0_amount_100m, mv,
            int(x.state0 not in ('N', 'R')), x.d0_date,
            '新增2024' if x.d0_date < '2025-01-01' else
            ('新增2025年初' if x.d0_date < '2025-05-01' else '共同区间'),
            int(x.d1_up) if pd.notna(x.d1_up) else None,
            int(x.d2_up) if pd.notna(x.d2_up) else None])
    day_rows = [[x.date, LABELS[x.variant] + '·' + x.scope, x.nav, x.return_,
                 x.drawdown, x.cash, x.market_value, x.buy, x.sales, x.fees]
                for x in nav[nav.scope.isin(['完整2024起', '共同区间'])]
                .sort_values(['date', 'variant', 'scope']).itertuples()]

    f = pd.read_pickle(OUT / 'enriched.pkl')
    latest = f[f.part.eq('test')].copy()
    old = pd.read_pickle(r.four.OUT / 'four_all_top3.pkl')
    old = pd.concat([pd.read_pickle(previous.EARLY / 'top3_four.pkl'),
                     old[old.part.eq('test')]], ignore_index=True)
    old = old[old.d0_date.ge('2025-05-01')].copy()
    old['variant'] = 'D2S2'
    candidates = pd.concat([latest, old], ignore_index=True)
    rulemap = {(x['variant'], x['month']): x['selected'] for x in rules}
    signals = pd.read_pickle(OUT / 'signals.pkl').set_index(['variant', 'symbol', 'd0_date'])
    candidate_rows = []
    for x in candidates.sort_values(['d0_date', 'variant', 'd0_rank']).itertuples():
        data = x._asdict()
        rule = rulemap[(x.variant, x.d0_date[:7])]
        key = (x.variant, x.symbol, x.d0_date)
        selected = key in signals.index
        passed = bool(x.eligible and r.shape.mask(pd.DataFrame([data]), rule).iloc[0])
        mv = np.exp(data['log_float_mv_proxy']) / 1e8 if pd.notna(data.get('log_float_mv_proxy')) else None
        candidate_rows.append([x.d0_date, LABELS[x.variant], x.d0_rank, x.score,
            x.d1_date, int(bool(x.eligible)), data.get('reason'),
            rule['text'].replace('五分', '四分'), int(passed), int(selected),
            x.high5, x.low5, x.body5, x.pullback5, x.open_gap,
            signals.loc[key, 'entry_time'] if selected else None,
            signals.loc[key, 'entry'] if selected else None,
            int(x.label_3) if x.observed_3 else None,
            int(x.d1_up) if pd.notna(x.d1_up) else None,
            int(x.d2_up) if pd.notna(x.d2_up) else None,
            x.board_streak_days, data.get('d0_turnover_pct'),
            data.get('d0_amount_100m'), mv,
            int(x.state0 not in ('N', 'R')) if x.state0 != 'unknown' else None])

    sheets = {
        '总体': table(['方案及区间','买入笔','已卖笔','待卖笔','再板笔','标签笔','再板率','总收益','最大回撤','夏普','月均收益','盈利月','最差月','最大月回撤'], summary,
                    percent=[6,7,8,10,12,13], decimal=[9]),
        '月度': table(['月份','主账户月收益','主账户月回撤','买入笔','月末资产','两月同起点收益','一月同起点收益','两月同起点回撤','一月同起点回撤','两月笔数','一月笔数'], monthly_rows,
                    percent=[1,2,5,6,7,8], dates=[0]),
        'D2评估': table(['买入月份','方案','区间','已卖笔','待卖笔','D2胜率','D2均收益','D2中位收益','亏超5%','D2封板率','两日再板率','仅D3再板笔'], d2_rows,
                     percent=[5,6,7,8,9,10], dates=[0]),
        '日线评估': table(['月份','验证月数','集合','范围','标签数','基础再板率','平均精确率','Top1数','Top1命中','Top3数','Top3命中','Top5数','Top5命中'], daily_rows,
                       percent=[5,6,8,10,12], dates=[0]),
        '每月规则': table(['月份','方案','日线训练','日线验证','形态训练','形态验证','规则','训练基数','训练保留','训练命中','验证基数','验证保留','验证命中'], rule_rows,
                       percent=[9,12], dates=[0]),
        '每日': table(['日期','方案及区间','资产','日收益','回撤','现金','持仓市值','买入金额','卖回金额','费用'], day_rows,
                    percent=[3,4], dates=[0]),
        '逐笔': table(['买入日期','方案','D0排名','评分','形态规则','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','买入涨幅','卖出日期','卖价','费后收益','投入元','盈亏元','三日再板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST','选股日期','区间','D1封板','D2封板'], trades,
                    percent=[5,6,7,8,9,12,15], decimal=[3,11,14,20,21,22], dates=[0,13,24]),
        '前三候选': table(['选股日期','方案','D0排名','评分','次日日期','数据可用','核对结果','形态规则','形态通过','最终入选','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','三日再板','D1封板','D2封板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST'], candidate_rows,
                          percent=[10,11,12,13,14], decimal=[3,16,21,22,23], dates=[0,4]),
    }
    notes = {
        '总体': '主账户从2024年1月起100万元连续回放；2024单段和2025重置仅核对；同起点比较自2025年5月。D2收盘卖，买卖各0.1%。',
        '月度': '完整收益按2024年1月起连续账户的持仓日历月；右侧两方案仅2025年5月以后同起点。2026年10月只截至14日。',
        'D2评估': '按买入月归属；已卖笔的费后收益。D2封板和三日再板不等于盈利。',
        '日线评估': '三日标签=D1—D3至少一天收盘再封板。训练拟合内只作诊断；验证和测试按时间前后隔离。',
        '每月规则': '两层一月方案各用6月训练、1月验证。日线验证和形态验证均在目标月之前。',
        '每日': '账户按交易日逐日估值。空仓买一半现金；有持仓再买剩余现金；D2收盘卖。',
        '逐笔': '一月方案逐笔覆盖2024年1月至2026年10月，旧两月方案仅同起点比较。买价为下一根合格分钟K开价代理。',
        '前三候选': '保留未买入及分钟数据核验失败的D0前三名；只给最终入选者写买价。',
    }
    for name, content in sheets.items():
        content['title'] = 'B2 2024—2026 两层一月完整回测 R34'
        content['note'] = notes[name]
    dump(OUT / 'workbook_data.json', sheets)

    main_account = ordered[0]
    controls = json.loads((OUT / 'verification.json').read_text())
    added_months = monthly[monthly.scope.eq('完整2024起') & monthly.month.lt('2025-01')].sort_values('month')
    doc = ['# B2 2024 年起完整历史回放 R34', '',
        '## 范围与方法', '',
        '- 本轮新增 2024 年 1—12 月；两层各用前 6 个月训练、前 1 个月验证。2022 年 9—10 月只作特征预热，11—12 月进入最早日线模型训练。2023 年 6 月产生第一批因果日线预测，2024 年 1 月是首个完整两层测试月。',
        '- 2023 和 2024 年 Tushare 原始分钟档案各覆盖 242 个交易日。逐股过滤后将 09:30 集合竞价记录并入 09:31 的首根分钟K，与历史压缩格式一致。2025—2026 年沿用已核对分钟数据。',
        '- 月模型使用固定随机森林 51 因子和原样本权重；每月保存实际训练行、模型和训练/验证/测试评分。第二层从 79 条四K规则按训练及上一验证月选一条。',
        '- D0 固定排名前三；D1 前四根完整分钟K观察；满足形态、实体大于−8%、开板后同股下一根合格K开价代理买入，不因该门槛失败而顺延；D2 收盘卖出；买卖各收 0.1% 手续费。',
        '- 账户从 2024 年 1 月空仓 100 万元起；空仓买一半现金，已有持仓再用剩余现金。所有账户单独重置。2026 年 10 月只统计至 14 日，未卖持仓按当日收盘估值。',
        '- 这是一段已研究过的历史回放，不是新的外部盲测；分钟开价代理不保证真实可成交量和盘口队列。', '',
        '## 账户结果', '', '|区间与方案|买入笔|已卖笔|收益|最大回撤|夏普|',
        '|---|---:|---:|---:|---:|---:|']
    for item in ordered:
        doc.append(f"|{LABELS[item['variant']]}·{item['scope']}|{item['entries']}|{item['closed']}|{item['total_return']:+.2%}|{item['max_drawdown']:.2%}|{item['sharpe']:.2f}|")
    doc += ['', '## 新增的 2024 年月份', '', '|月份|月收益|月内最大回撤|交易笔|',
            '|---|---:|---:|---:|']
    for item in added_months.itertuples():
        doc.append(f'|{item.month}|{item.return_:+.2%}|{item.drawdown:.2%}|{item.entries}|')
    doc += ['', '## 核验', '',
        f"- {controls.get('model_reload_count')} 个月模型从文件重载后复评分吻合；交易时的生产评估器与逐笔回放一致 {controls.get('production_evaluator_exact')} 笔。",
        '- 既有 2025 年 1 月以后信号和规则逐项复现；2025 年 1 月重新起算账户与 R33 逐项相同。',
        '- 同起点两月基准从 2025 年 5 月起可直接对比；2024 年起主账户与同起点账户不能用终值直接比策略。',
        '- Excel 的月度表按持仓日历月，D2 评估和逐笔按买入月；两种月度口径不同。',
        '- 本轮只扩展研究回放，不改变服务器 B2 模型与自动选股。', '',
        '## 复现', '',
        '- 依次运行 `scripts.experiment_b2_full_2024_r34` 的 sources、panel、train、prepare、select、evaluate、audit，再运行 `scripts.report_b2_full_2024_r34`。',
        '- R34 输出目录保留日线原始月源、早期面板、模型及训练样本、月规则候选、分钟证据、逐笔和逐日账户以及验证回执。']
    (DOC / 'README.md').write_text('\n'.join(doc) + '\n')
    for filename in ['specification.json', 'daily_sources.json', 'panel_manifest.json',
                     'accounts.json', 'monthly.json', 'd2_metrics.json', 'verification.json',
                     'coverage.json', 'signal_controls.json', 'rule_controls.json']:
        (DOC / filename).write_bytes((OUT / filename).read_bytes())
    print(pd.DataFrame(ordered)[['scope', 'variant', 'entries', 'closed',
        'total_return', 'max_drawdown', 'sharpe']].to_string(index=False))
    print(added_months[['month', 'entries', 'return_', 'drawdown']].to_string(index=False))
    print('workbook rows', {name: len(content['rows']) for name, content in sheets.items()})


if __name__ == '__main__':
    main()
