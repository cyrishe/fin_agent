"""Combine one- and two-month B2 validation schemes in one Chinese workbook."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import experiment_b2_full_2024_r34 as one
from scripts import experiment_b2_two_month_2024_r35 as two
from scripts.report_b2_d2_metrics_r31 import metrics
from scripts.report_b2_validation_r31 import table
from scripts.replay_limit_rebound_cash_r23 import dump

OUT = two.OUT
DOC = Path('docs/stock_automl_runs/20261019_b2_both_2024_r35')
XLSX = DOC / 'B2_一月两月验证_2024起全期_R35_20261019.xlsx'
CASES = [
    ('一月·2024一月起', one.OUT, '完整2024起', 'D1S1'),
    ('一月·2024三月起', one.OUT, '2024三月同起', 'D1S1'),
    ('两月·2024三月起', two.OUT, '完整两月2024起', 'D2S2'),
    ('一月·2024全年', one.OUT, '新增2024段', 'D1S1'),
    ('一月·2024三月至年底', one.OUT, '2024三月单段', 'D1S1'),
    ('两月·2024三月至年底', two.OUT, '两月新增2024段', 'D2S2'),
    ('一月·2025五月起', one.OUT, '共同区间', 'D1S1'),
    ('两月·2025五月起', two.OUT, '共同区间', 'D2S2'),
]


def value(frame, scope, month, field, variant=None):
    match = frame[frame.scope.eq(scope) & frame.month.eq(month)]
    if variant is not None:
        match = match[match.variant.eq(variant)]
    return match.iloc[0][field] if len(match) else None


def main():
    two.configure()
    DOC.mkdir(parents=True, exist_ok=True)
    one_accounts = json.loads((one.OUT / 'accounts.json').read_text())
    two_accounts = json.loads((two.OUT / 'accounts.json').read_text())
    one_monthly = pd.DataFrame(json.loads((one.OUT / 'monthly.json').read_text()))
    two_monthly = pd.DataFrame(json.loads((two.OUT / 'monthly.json').read_text()))
    one_ledger = pd.read_pickle(one.OUT / 'ledger.pkl')
    two_ledger = pd.read_pickle(two.OUT / 'ledger.pkl')
    one_nav = pd.read_pickle(one.OUT / 'nav.pkl')
    two_nav = pd.read_pickle(two.OUT / 'nav.pkl')
    base = json.loads((one.OUT / 'workbook_data.json').read_text())

    summary, d2, chosen_accounts = [], [], []
    for caption, root, scope, variant in CASES:
        accounts = one_accounts if root == one.OUT else two_accounts
        ledger = one_ledger if root == one.OUT else two_ledger
        month_data = one_monthly if root == one.OUT else two_monthly
        account = next(x for x in accounts if x['scope'] == scope and x['variant'] == variant)
        chosen_accounts.append(dict(caption=caption, **account))
        group = ledger[ledger.scope.eq(scope) & ledger.variant.eq(variant)]
        known = group[group.observed_3]
        lines = month_data[month_data.scope.eq(scope) & month_data.variant.eq(variant)]
        summary.append([caption, account['entries'], account['closed'], account['pending'],
            int(known.label_3.sum()), len(known),
            float(known.label_3.mean()) if len(known) else None,
            account['total_return'], account['max_drawdown'], account['sharpe'],
            float(lines.return_.mean()), int(lines.return_.gt(0).sum()),
            float(lines.return_.min()), float(lines.drawdown.max())])
        for period in ['总体', *sorted(group.entry_date.str[:7].unique())]:
            subset = group if period == '总体' else group[group.entry_date.str.startswith(period)]
            d2.append(dict(caption=caption, scope=scope, variant=variant,
                           period=period, **metrics(subset)))
    dump(OUT / 'd2_metrics.json', d2)

    month_rows = []
    for month in [str(p) for p in pd.period_range('2024-01', '2026-10', freq='M')]:
        a = lambda scope, field: value(one_monthly, scope, month, field)
        b = lambda scope, field: value(two_monthly, scope, month, field)
        month_rows.append([month + '-01', a('完整2024起', 'return_'),
            a('2024三月同起', 'return_'), b('完整两月2024起', 'return_'),
            a('2024三月同起', 'drawdown'), b('完整两月2024起', 'drawdown'),
            a('2024三月同起', 'entries'), b('完整两月2024起', 'entries'),
            a('2024三月同起', 'closing_nav'), b('完整两月2024起', 'closing_nav')])
    common_rows = []
    for month in [str(p) for p in pd.period_range('2025-05', '2026-10', freq='M')]:
        a = lambda field: value(one_monthly, '共同区间', month, field, 'D1S1')
        b = lambda field: value(two_monthly, '共同区间', month, field, 'D2S2')
        common_rows.append([month + '-01', a('return_'), b('return_'),
            a('drawdown'), b('drawdown'), a('entries'), b('entries'),
            a('closing_nav'), b('closing_nav')])
    d2_rows = [[x['period'] + '-01' if x['period'] != '总体' else '总体',
        x['caption'], x['closed'], x['pending'], x['win_rate'], x['mean'],
        x['median'], x['bad5_rate'], x['d2_limit_rate'], x['d12_limit_rate'],
        x['only_d3']] for x in d2]

    daily_rows = [x for x in base['日线评估']['rows'] if x[1] == 1]
    for item in json.loads((two.OUT / 'daily_metrics.json').read_text()):
        for key, caption in [('all', '原涨停池'), ('open', '次日开盘打开')]:
            v = item[key]
            daily_rows.append([item['month'] + '-01', 2,
                {'train': '训练拟合内', 'valid': '验证', 'forecast': '测试'}[item['part']],
                caption, v['known'], v['rate'], v['ap'], v['top1']['n'],
                v['top1']['rate'], v['top3']['n'], v['top3']['rate'],
                v['top5']['n'], v['top5']['rate']])
    daily_rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

    rule_rows = [x for x in base['每月规则']['rows'] if x[1] == '两层一月']
    rules = json.loads((two.OUT / 'rules_frozen.json').read_text())
    for rule in rules:
        daily_window = two.r.window(rule['month'], 2)
        rule_rows.append([rule['month'] + '-01', '两层两月',
            '至'.join(daily_window['train']), '至'.join(daily_window['valid']),
            '至'.join(rule['window']['train']), '至'.join(rule['window']['valid']),
            rule['selected']['text'].replace('五分', '四分'),
            rule['train_base']['known'], rule['train_selected']['known'],
            rule['train_selected']['hit'], rule['valid_base']['known'],
            rule['valid_selected']['known'], rule['valid_selected']['hit']])
    rule_rows.sort(key=lambda x: (x[0], x[1]))

    nav_rows = []
    for frame, scopes, variant in [(one_nav, ['完整2024起', '2024三月同起', '共同区间'], 'D1S1'),
                                   (two_nav, ['完整两月2024起', '共同区间'], 'D2S2')]:
        for x in frame[frame.scope.isin(scopes) & frame.variant.eq(variant)].itertuples():
            caption = ('一月·' if x.variant == 'D1S1' else '两月·') + x.scope
            nav_rows.append([x.date, caption, x.nav, x.return_, x.drawdown,
                             x.cash, x.market_value, x.buy, x.sales, x.fees])
    nav_rows.sort(key=lambda x: (x[0], x[1]))

    trades = [x for x in base['逐笔']['rows'] if x[1] == '两层一月']
    xledger = two_ledger[two_ledger.scope.eq('完整两月2024起')]
    for x in xledger.sort_values('entry_date').itertuples():
        mv = np.exp(x.log_float_mv_proxy) / 1e8 if pd.notna(x.log_float_mv_proxy) else None
        trades.append([x.entry_date, '两层两月', x.d0_rank, x.score,
            x.rule_text, x.high5, x.low5, x.body5, x.pullback5, x.open_gap,
            x.entry_time, x.buy, x.buy / x.d1_preclose - 1, x.exit_date, x.sell,
            x.return_, x.cost, x.pnl, int(x.label_3) if x.observed_3 else None,
            x.board_streak_days, x.d0_turnover_pct, x.d0_amount_100m, mv,
            int(x.state0 not in ('N', 'R')), x.d0_date,
            '新增2024' if x.d0_date < '2025-01-01' else
            ('新增2025年初' if x.d0_date < '2025-05-01' else '共同区间'),
            int(x.d1_up) if pd.notna(x.d1_up) else None,
            int(x.d2_up) if pd.notna(x.d2_up) else None])
    trades.sort(key=lambda x: (x[0], x[1]))

    candidates = [x for x in base['前三候选']['rows'] if x[1] == '两层一月']
    enriched = pd.read_pickle(two.OUT / 'enriched.pkl')
    test = enriched[enriched.part.eq('test')]
    rulemap = {x['month']: x['selected'] for x in rules}
    signals = pd.read_pickle(two.OUT / 'signals.pkl').set_index(two.KEY)
    for x in test.sort_values(['d0_date', 'd0_rank']).itertuples():
        data = x._asdict()
        rule = rulemap[x.d0_date[:7]]
        key = (x.symbol, x.d0_date)
        selected = key in signals.index
        passed = bool(x.eligible and two.r.shape.mask(pd.DataFrame([data]), rule).iloc[0])
        mv = np.exp(data['log_float_mv_proxy']) / 1e8 if pd.notna(data.get('log_float_mv_proxy')) else None
        candidates.append([x.d0_date, '两层两月', x.d0_rank, x.score,
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
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))

    sheets = {
        '总体': table(base['总体']['headers'], summary,
                    percent=[6,7,8,10,12,13], decimal=[9]),
        '月度': table(['月份','一月全期收益','一月同起收益','两月同起收益',
                     '一月同起回撤','两月同起回撤','一月买入笔','两月买入笔',
                     '一月月末资产','两月月末资产'], month_rows,
                    percent=[1,2,3,4,5], dates=[0]),
        '五月同起': table(['月份','一月收益','两月收益','一月回撤','两月回撤',
                         '一月买入笔','两月买入笔','一月月末资产','两月月末资产'],
                        common_rows, percent=[1,2,3,4], dates=[0]),
        'D2评估': table(['买入月份','方案及区间','已卖笔','待卖笔','D2胜率','D2均收益',
                       'D2中位收益','亏超5%','D2封板率','两日再板率','仅D3再板笔'],
                      d2_rows, percent=[4,5,6,7,8,9], dates=[0]),
        '日线评估': table(base['日线评估']['headers'], daily_rows,
                       percent=[5,6,8,10,12], dates=[0]),
        '每月规则': table(base['每月规则']['headers'], rule_rows,
                       percent=[9,12], dates=[0]),
        '每日': table(base['每日']['headers'], nav_rows,
                    percent=[3,4], dates=[0]),
        '逐笔': table(base['逐笔']['headers'], trades,
                    percent=[5,6,7,8,9,12,15], decimal=[3,11,14,20,21,22], dates=[0,13,24]),
        '前三候选': table(base['前三候选']['headers'], candidates,
                          percent=[10,11,12,13,14], decimal=[3,16,21,22,23], dates=[0,4]),
    }
    notes = {
        '总体': '一月=两层各6月训练、1月验证；两月=两层各6月训练、2月验证。各账户独立从100万元起，不能拼接不同起点收益。',
        '月度': '一月全期从2024-01连续；同起点两方案均从2024-03空仓起，故1-2月两月及同起数据留空。按持仓日历月。',
        '五月同起': '两方案均从2025-05空仓100万元起；此区间与原B2已登记的18个月结果逐项复现。',
        'D2评估': '按买入月；已卖笔费后收益。三日再板含D3，但实际D2收盘就卖，不能用三日再板率代替交易胜率。',
        '日线评估': '集合按时间前后隔离；训练拟合内只诊断，验证用于规则/模型确认，测试只报告。D1—D3任一收盘封板为标签。',
        '每月规则': '每个月按各自历史验证窗口从79条四K规则选一条；训练6月，验证一月或两月；目标月测试结果不参与选规。',
        '每日': '按持仓交易日逐日估值；空仓买一半现金，有持仓再买剩余现金；D2收盘卖，买卖各0.1%。',
        '逐笔': '每个交易信号只列一次：一月方案自2024-01，两月方案自2024-03。买价为锁定后下一根合格分钟K开价代理。',
        '前三候选': '每个D0原排名前三均保留；仅最终入选者有买价。分钟缺失或日线冲突者注明原因。',
    }
    for name, content in sheets.items():
        content['title'] = 'B2 一月与两月验证 2024—2026 R35'
        content['note'] = notes[name]
    dump(OUT / 'workbook_data.json', sheets)

    one_audit = json.loads((one.OUT / 'verification.json').read_text())
    two_audit = json.loads((two.OUT / 'verification.json').read_text())
    doc = ['# B2 一月与两月验证：2024 年起完整回测', '',
        '## 新补范围', '',
        '- 2023 年和 2024 年 Tushare 分钟档案各 242 个交易日；KingdomAI 2022 年 9 月至 2023 年 12 月日线补齐因果训练窗口。2022 年 9—10 月用于日线特征预热，11—12 月开始提供训练标签。',
        '- 一月方案：日线第一批折外预测在 2023 年 6 月，四K训练 2023 年 6—11 月、验证 12 月，首次完整测试 2024 年 1 月。',
        '- 两月方案：日线第一批折外预测在 2023 年 7 月，四K训练 2023 年 7—12 月、验证 2024 年 1—2 月，首次完整测试 2024 年 3 月。没有 2022 年分钟K，所以两月方案的 2024 年 1—2 月不能按同口径补出。',
        '- 两方案均固定 R18 随机森林 51 因子、D0 前三、79 条四K候选规则、D1 开板后下一根合格分钟K开价代理买入、D2 收盘卖、买卖各 0.1% 费用；只有两层验证窗口长度不同。',
        '- 新增月份按目标月前的训练与验证选规；既有 2025 年 5 月后两月 B2 模型、规则、交易和账户结果严格复现。',
        '- 历史区间已参与多轮研究，不视为新外部盲测；分钟K开价代理没有盘口队列和滑点保证。', '',
        '## 结果', '', '|方案及账户起点|买入笔|已卖笔|总收益|最大回撤|夏普|',
        '|---|---:|---:|---:|---:|---:|']
    for item in chosen_accounts:
        doc.append(f"|{item['caption']}|{item['entries']}|{item['closed']}|{item['total_return']:+.2%}|{item['max_drawdown']:.2%}|{item['sharpe']:.2f}|")
    early_one = metrics(one_ledger[one_ledger.scope.eq('2024三月单段') & one_ledger.variant.eq('D1S1')])
    early_two = metrics(two_ledger[two_ledger.scope.eq('两月新增2024段') & two_ledger.variant.eq('D2S2')])
    doc += ['', '### 对交易结果的解释', '',
        f"- 2024 年 3—12 月同起点的已卖交易，一月方案 {early_one['closed']} 笔、D2 费后笔均 {early_one['mean']:+.2%}、亏损超过 5% 的比例 {early_one['bad5_rate']:.2%}；两月方案 {early_two['closed']} 笔、笔均 {early_two['mean']:+.2%}、亏损超过 5% 的比例 {early_two['bad5_rate']:.2%}。",
        f"- 同一组交易在 D1—D2 任一收盘再板的比例分别为 {early_one['d12_limit_rate']:.2%} 与 {early_two['d12_limit_rate']:.2%}；训练标签是 D1—D3 任一再板，且实际 D2 就卖出。应把账户收益、D2 笔均与回撤作为主要判断。",
        '- 两方案的 2024 年损失跨越多个月份，不能用后段共同区间的盈利来抵消或声称策略全历史稳定。']
    doc += ['', '### 2024 年同起点的逐月账户表现', '',
            '|月份|一月月收益|两月月收益|一月月内回撤|两月月内回撤|一月买入笔|两月买入笔|',
            '|---|---:|---:|---:|---:|---:|---:|']
    for row in month_rows[2:12]:
        doc.append(f'|{row[0][:7]}|{row[2]:+.2%}|{row[3]:+.2%}|{row[4]:.2%}|{row[5]:.2%}|{row[6]}|{row[7]}|')
    doc += ['', '## 数据与代码核验', '',
        f"- 一月方案 {one_audit['model_reload_count']} 个月模型重载评分最大差 {one_audit['model_score_max_diff']}；{one_audit['production_evaluator_exact']} 笔交易与生产评估器一致。",
        f"- 两月方案 {two_audit['model_reload_count']} 个月模型重载评分最大差 {two_audit['model_score_max_diff']}；{two_audit['production_evaluator_exact']} 笔交易与生产评估器一致。",
        '- 两方案每笔 D2 收盘卖价与双边费用、每日现金与持仓净值均核对。早期分钟质量冲突保留于候选明细，不纳入可交易样本。',
        '- Excel 的月度账户按持仓日历月，D2评估与逐笔按买入月份；两种月份口径不应混用。',
        '- 本轮只扩展研究回测，不改生产 B2 定义、数据库逐日记录或线上选股。', '',
        '## 复现', '',
        '- 依次运行 `scripts.experiment_b2_full_2024_r34` 的 sources、panel、train、prepare、select、evaluate、audit。',
        '- 依次运行 `scripts.experiment_b2_two_month_2024_r35` 的 train、prepare、select、evaluate、audit。',
        '- 运行 `scripts.report_b2_both_2024_r35`，再运行 XLSX 构建与 `scripts.verify_b2_both_2024_r35_workbook`。模型、训练行、全部分数、分钟质量、规则候选、逐笔与逐日账户存于 R34/R35 各自输出目录。']
    (DOC / 'README.md').write_text('\n'.join(doc) + '\n')
    for name, source in [('R34_accounts.json', one.OUT / 'accounts.json'),
                         ('R35_accounts.json', two.OUT / 'accounts.json'),
                         ('R34_verification.json', one.OUT / 'verification.json'),
                         ('R35_verification.json', two.OUT / 'verification.json'),
                         ('R35_coverage.json', two.OUT / 'coverage.json')]:
        (DOC / name).write_bytes(source.read_bytes())
    print(pd.DataFrame(chosen_accounts)[['caption', 'entries', 'closed',
          'total_return', 'max_drawdown', 'sharpe']].to_string(index=False))
    print('sheets', {name: len(content['rows']) for name, content in sheets.items()})


if __name__ == '__main__':
    main()
