"""R33 Chinese workbook data and research summary for the 2025 Jan/Feb extension."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_b2_early_2025_r33 import OUT, R32, configure
from scripts.report_b2_validation_r31 import table
from scripts.report_b2_d2_metrics_r31 import metrics
from scripts.replay_limit_rebound_cash_r23 import dump

DOC = Path('docs/stock_automl_runs/20261019_b2_early_2025_r33')
NAMES = {'D1S1': '两层一月', 'D2S2': '两层两月'}
MAIN = [('共同区间', 'D2S2'), ('共同区间', 'D1S1'),
        ('一月全期含1-2月', 'D1S1'), ('新增1-2月单独', 'D1S1'),
        ('一月全期', 'D1S1')]


def row_value(frame, scope, variant, month, key):
    match = frame[frame.scope.eq(scope) & frame.variant.eq(variant) & frame.month.eq(month)]
    return match.iloc[0][key] if len(match) else None


def main() -> None:
    previous, r = configure()
    DOC.mkdir(parents=True, exist_ok=True)
    accounts = json.loads((OUT / 'accounts.json').read_text())
    monthly = pd.DataFrame(json.loads((OUT / 'monthly.json').read_text()))
    ledger = pd.read_pickle(OUT / 'ledger.pkl')
    nav = pd.read_pickle(OUT / 'nav.pkl')
    rules = json.loads((OUT / 'all_rules.json').read_text())
    daily = json.loads((OUT / 'daily_metrics.json').read_text())
    ordered = [next(a for a in accounts if a['scope'] == s and a['variant'] == v) for s, v in MAIN]
    summary, d2 = [], []
    for account in ordered:
        scope, variant = account['scope'], account['variant']
        lines = monthly[monthly.scope.eq(scope) & monthly.variant.eq(variant)]
        group = ledger[ledger.scope.eq(scope) & ledger.variant.eq(variant)]
        known = group[group.observed_3]
        summary.append([f'{NAMES[variant]}·{scope}', account['entries'], account['closed'],
            account['pending'], int(known.label_3.sum()), len(known),
            float(known.label_3.mean()) if len(known) else None,
            account['total_return'], account['max_drawdown'], account['sharpe'],
            float(lines.return_.mean()), int(lines.return_.gt(0).sum()),
            float(lines.return_.min()), float(lines.drawdown.max())])
        for period in ['总体', *sorted(group.entry_date.str[:7].unique())]:
            subset = group if period == '总体' else group[group.entry_date.str.startswith(period)]
            d2.append(dict(scope=scope, variant=variant, period=period, **metrics(subset)))
    dump(OUT / 'd2_metrics.json', d2)
    month_rows = []
    for month in [str(m) for m in pd.period_range('2025-01', '2026-10', freq='M')]:
        a = lambda s, v, k: row_value(monthly, s, v, month, k)
        old, new = a('共同区间', 'D2S2', 'return_'), a('共同区间', 'D1S1', 'return_')
        odd, ndd = a('共同区间', 'D2S2', 'drawdown'), a('共同区间', 'D1S1', 'drawdown')
        month_rows.append([month + '-01', old, new,
            new - old if old is not None else None, odd, ndd,
            ndd - odd if odd is not None else None,
            a('共同区间', 'D2S2', 'entries'), a('共同区间', 'D1S1', 'entries'),
            a('一月全期含1-2月', 'D1S1', 'return_'),
            a('一月全期含1-2月', 'D1S1', 'drawdown'),
            a('一月全期含1-2月', 'D1S1', 'entries')])
    d2rows = [[z['period'] + '-01' if z['period'] != '总体' else '总体',
               NAMES[z['variant']], z['scope'], z['closed'], z['pending'],
               z['win_rate'], z['mean'], z['median'], z['bad5_rate'],
               z['d2_limit_rate'], z['d12_limit_rate'], z['only_d3']] for z in d2]
    dailyrows = []
    for item in daily:
        for scope, caption in [('all', '原涨停池'), ('open', '次日开盘打开')]:
            v = item[scope]
            dailyrows.append([item['month'] + '-01', item['daily_validation'],
                {'train': '训练拟合内', 'valid': '验证', 'forecast': '测试'}[item['part']],
                caption, v['known'], v['rate'], v['ap'],
                v['top1']['n'], v['top1']['rate'], v['top3']['n'], v['top3']['rate'],
                v['top5']['n'], v['top5']['rate']])
    rule_rows = []
    for item in sorted(rules, key=lambda x: (x['month'], x['variant'])):
        w = r.window(item['month'], 1 if item['variant'] == 'D1S1' else 2)
        rule_rows.append([item['month'] + '-01', NAMES[item['variant']],
            '至'.join(w['train']), '至'.join(w['valid']),
            '至'.join(item['window']['train']), '至'.join(item['window']['valid']),
            item['selected']['text'].replace('五分', '四分'), item['train_base']['known'],
            item['train_selected']['known'], item['train_selected']['hit'],
            item['valid_base']['known'], item['valid_selected']['known'],
            item['valid_selected']['hit']])
    trade_rows = []
    wanted = ((ledger.variant.eq('D2S2') & ledger.scope.eq('共同区间')) |
              (ledger.variant.eq('D1S1') & ledger.scope.eq('一月全期含1-2月')))
    for x in ledger[wanted].sort_values(['entry_date', 'variant']).itertuples():
        mv = np.exp(x.log_float_mv_proxy) / 1e8 if pd.notna(x.log_float_mv_proxy) else None
        trade_rows.append([x.entry_date, NAMES[x.variant], x.d0_rank, x.score,
            x.rule_text, x.high5, x.low5, x.body5, x.pullback5, x.open_gap,
            x.entry_time, x.buy, x.buy / x.d1_preclose - 1, x.exit_date, x.sell,
            x.return_, x.cost, x.pnl, int(x.label_3) if x.observed_3 else None,
            x.board_streak_days, x.d0_turnover_pct, x.d0_amount_100m, mv,
            int(x.state0 not in ('N', 'R')), x.d0_date,
            '新增1-2月' if x.d0_date < '2025-03-01' else
            ('新增3-4月' if x.d0_date < '2025-05-01' else '共同区间'),
            int(x.d1_up) if pd.notna(x.d1_up) else None,
            int(x.d2_up) if pd.notna(x.d2_up) else None])
    daily_nav = [[x.date, NAMES[x.variant] + '·' + x.scope, x.nav, x.return_,
                  x.drawdown, x.cash, x.market_value, x.buy, x.sales, x.fees]
                 for x in nav[nav.scope.isin(['共同区间', '一月全期含1-2月'])]
                 .sort_values(['date', 'variant', 'scope']).itertuples()]
    f = pd.read_pickle(OUT / 'enriched.pkl')
    latest = f[f.part.eq('test')].copy()
    old = pd.read_pickle(r.four.OUT / 'four_all_top3.pkl')
    old = pd.concat([pd.read_pickle(previous.EARLY / 'top3_four.pkl'),
                     old[old.part.eq('test')]], ignore_index=True)
    old['variant'] = 'D2S2'
    candidates = pd.concat([latest, old], ignore_index=True)
    rulemap = {(x['variant'], x['month']): x['selected'] for x in rules}
    signals = pd.read_pickle(OUT / 'signals.pkl').set_index(['variant', 'symbol', 'd0_date'])
    candidate_rows = []
    for x in candidates.sort_values(['d0_date', 'variant', 'd0_rank']).itertuples():
        data = x._asdict()
        rule = rulemap[(x.variant, x.d0_date[:7])]
        index = (x.variant, x.symbol, x.d0_date)
        selected = index in signals.index
        passed = bool(x.eligible and r.shape.mask(pd.DataFrame([data]), rule).iloc[0])
        mv = np.exp(data['log_float_mv_proxy']) / 1e8 if pd.notna(data.get('log_float_mv_proxy')) else None
        candidate_rows.append([x.d0_date, NAMES[x.variant], x.d0_rank, x.score,
            x.d1_date, int(bool(x.eligible)), data.get('reason'),
            rule['text'].replace('五分', '四分'), int(passed), int(selected),
            x.high5, x.low5, x.body5, x.pullback5, x.open_gap,
            signals.loc[index, 'entry_time'] if selected else None,
            signals.loc[index, 'entry'] if selected else None,
            int(x.label_3) if x.observed_3 else None,
            int(x.d1_up) if pd.notna(x.d1_up) else None,
            int(x.d2_up) if pd.notna(x.d2_up) else None,
            x.board_streak_days, data.get('d0_turnover_pct'),
            data.get('d0_amount_100m'), mv,
            int(x.state0 not in ('N', 'R')) if x.state0 != 'unknown' else None])
    sheets = {
        '总体': table(['方案及区间','买入笔','已卖笔','待卖笔','再板笔','标签笔','再板率','总收益','总回撤','夏普','月均收益','盈利月','最差月','最大月回撤'], summary,
                    percent=[6,7,8,10,12,13],decimal=[9]),
        '月度': table(['月份','两月收益','一月收益','收益差','两月回撤','一月回撤','回撤差','两月笔数','一月笔数','一月全期收益','一月全期回撤','一月全期笔数'], month_rows,
                    percent=[1,2,3,4,5,6,9,10], dates=[0]),
        'D2评估': table(['买入月份','方案','区间','已卖笔','待卖笔','D2胜率','D2均收益','D2中位收益','亏超5%','D2封板率','两日再板率','仅D3再板笔'], d2rows,
                       percent=[5,6,7,8,9,10], dates=[0]),
        '日线评估': table(['月份','验证月数','集合','范围','标签数','基础再板率','平均精确率','Top1数','Top1命中','Top3数','Top3命中','Top5数','Top5命中'], dailyrows,
                        percent=[5,6,8,10,12], dates=[0]),
        '每月规则': table(['月份','方案','日线训练','日线验证','形态训练','形态验证','规则','训练基数','训练保留','训练命中','验证基数','验证保留','验证命中'], rule_rows,
                        percent=[9,12], dates=[0]),
        '每日': table(['日期','方案及区间','资产','日收益','回撤','现金','持仓市值','买入金额','卖回金额','费用'], daily_nav,
                    percent=[3,4], dates=[0]),
        '逐笔': table(['买入日期','方案','D0排名','评分','形态规则','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','买入涨幅','卖出日期','卖价','费后收益','投入元','盈亏元','三日再板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST','选股日期','区间','D1封板','D2封板'], trade_rows,
                    percent=[5,6,7,8,9,12,15], decimal=[3,11,14,20,21,22], dates=[0,13,24]),
        '前三候选': table(['选股日期','方案','D0排名','评分','次日日期','数据可用','核对结果','形态规则','形态通过','最终入选','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','三日再板','D1封板','D2封板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST'], candidate_rows,
                          percent=[10,11,12,13,14], decimal=[3,16,21,22,23], dates=[0,4]),
    }
    notes = {
        '总体': '新增一月全期从2025-01空仓100万元起；旧一月全期从3月起。共同区间从5月起，起点相同才可公平比较。D2收盘卖，买卖各0.1%。',
        '月度': 'B:I为2025-05以后同起点账户；J:L为一月方案自2025-01连续账户。1-4月两月方案无完整模型，留空。10月仅截至14日。',
        'D2评估': '只统计已卖笔的费后收益；月份按买入月。D2封板不等于盈利。',
        '日线评估': '标签=D1—D3至少一天收盘再封板；TopK按D0固定排名，次日过滤不补位。训练拟合内仅作诊断。',
        '每月规则': '两层均6月训练，1月验证；2025-01、02新补历史模型和规则，其余月份逐项复核与R32一致。',
        '每日': '账户净值按持仓日历日结算；空仓买一半现金，有持仓再买剩余现金。',
        '逐笔': '每笔仅列一次。一月方案资金从2025-01起；买价为下一根合格分钟K的开价代理；费后收益未卖留空。',
        '前三候选': '保留未买入与分钟核验失败记录；只给真正入选行写买价。历史非ST按当日查。',
    }
    for name, content in sheets.items():
        content['title'] = 'B2 2025年1—2月补全回测 R33'
        content['note'] = notes[name]
    dump(OUT / 'workbook_data.json', sheets)
    early = [x for x in ordered if x['scope'] == '新增1-2月单独'][0]
    full = [x for x in ordered if x['scope'] == '一月全期含1-2月'][0]
    oldfull = [x for x in ordered if x['scope'] == '一月全期'][0]
    early_d2 = next(x for x in d2 if x['scope'] == '新增1-2月单独' and x['period'] == '总体')
    early_month = monthly[monthly.scope.eq('一月全期含1-2月') &
                          monthly.month.isin(['2025-01','2025-02'])].sort_values('month')
    assertions = json.loads((OUT / 'verification.json').read_text())
    md = ['# R33：B2 两层一月验证补全 2025 年 1—2 月', '',
        '## 方法与数据', '',
        '- 2023 年日线与 Tushare 分钟档案均覆盖 242 个交易日；2023 年新增日线按月独立归档。2023 日线用于补齐早期模型训练，2025 年一、二月的开盘形态使用对应 D1 的分钟K；2023 分钟档案仅作源覆盖核对。',
        '- 第一层只新增 2024 年 6、7 月月度随机森林；2024 年 8 月至 2026 年 10 月模型及评分文件与 R32 逐字节复用。原 51 因子、六个月训练、一个月验证和三日再板标签不变。',
        '- 第二层 2025 年 1、2 月分别以历史六个月 Top1 因果预测训练，前一个月验证，从固定 79 条四K规则中选规；其余月份规则与 R32 逐项一致。',
        '- D0 原 Top3、D1 四根完整分钟K观察、首次开板后同股下一根合格开价买入、实体大于−8%不顺延、D2 收盘卖出、买卖各 0.1% 费用均不变。',
        '- 新连续账户从 2025 年 1 月空仓 100 万元起；原 R32 的 2025 年 3 月起账户保留供不同起点对照，2025 年 5 月起同起点两月基准保留。2026 年 10 月仅至 14 日，未卖仓位按当日收盘估值。',
        '- 这些日期已被反复研究，属于按时间排序的历史回放，不是新的外部盲测。', '',
        '## 结果', '',
        '|账户|交易笔|已卖|总收益|最大回撤|夏普|',
        '|---|---:|---:|---:|---:|---:|']
    for a in ordered:
        md.append(f"|{NAMES[a['variant']]}·{a['scope']}|{a['entries']}|{a['closed']}|{a['total_return']:+.2%}|{a['max_drawdown']:.2%}|{a['sharpe']:.2f}|")
    md += ['', '### 新增月份', '', '|月份|月收益|月内最大回撤|交易笔|',
           '|---|---:|---:|---:|']
    for x in early_month.itertuples():
        md.append(f'|{x.month}|{x.return_:+.2%}|{x.drawdown:.2%}|{x.entries}|')
    md += ['', '## 复核与结论', '',
        f"- 共 {assertions.get('model_reload_count')} 个一月月模型（新增 2 个、逐字节复用 27 个）的重载评分与存档评分吻合；原 R32 交易记录及既有月规则均复现。",
        f"- 新增一、二月单独账户 {early['entries']} 笔，收益 {early['total_return']:+.2%}、最大回撤 {early['max_drawdown']:.2%}；从一月连续持有至 2026 年 10 月 14 日收益 {full['total_return']:+.2%}、最大回撤 {full['max_drawdown']:.2%}。",
        f"- 新增月份 D1 或 D2 再板率 {early_d2['d12_limit_rate']:.2%}，但 D2 实际费后笔均 {early_d2['mean']:+.2%}、中位 {early_d2['median']:+.2%}、亏超 5% 占 {early_d2['bad5_rate']:.2%}。再板标签不能代替实际持仓收益；详见 Excel 的 D2 评估和逐笔。",
        '- 全期账户的复利路径受一、二月资金变化影响；比较模型优劣仍应看 2025 年 5 月起同起点账户，不能用不同起点总收益直接判胜负。',
        '- 补全一、二月只扩展研究结果，不改变正式 B2 自动选股、数据库模型或生产脚本。', '',
        '## 复现', '',
        '- 在 sklearn 1.7.1 环境依次运行 `scripts.experiment_b2_early_2025_r33` 的 sources、panel、train、prepare、select、evaluate、audit，再运行 `scripts.report_b2_early_2025_r33`。',
        '- 归档目录保存日线原始月文件、早期面板、完整模型与评分、规则候选、分钟证据、逐笔/每日账户及核验回执。']
    (DOC / 'README.md').write_text('\n'.join(md) + '\n')
    for filename in ['specification.json', 'daily_sources.json', 'panel_manifest.json',
                     'accounts.json', 'monthly.json', 'd2_metrics.json', 'verification.json',
                     'coverage.json', 'signal_controls.json', 'early_rule_controls.json']:
        (DOC / filename).write_bytes((OUT / filename).read_bytes())
    print(pd.DataFrame(ordered)[['scope','variant','entries','closed','total_return','max_drawdown','sharpe']].to_string(index=False))
    print(early_month[['month','entries','return_','drawdown']].to_string(index=False))


if __name__ == '__main__':
    main()
