"""Chinese report inputs and traceable R31 research documentation."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.experiment_b2_validation_r31 import OUT, NAMES, VARIANTS, MONTHS, window, BASE, KEY
from scripts.replay_limit_rebound_cash_r23 import dump, sha

DOC = Path('docs/stock_automl_runs/20261019_b2_validation_r31')


def table(headers, rows, percent=(), decimal=(), dates=()):
    return dict(headers=headers, rows=json.loads(pd.DataFrame(rows).to_json(orient='values', double_precision=15)),
                percent=list(percent), decimal=list(decimal), dates=list(dates))


def main():
    DOC.mkdir(parents=True, exist_ok=True)
    accounts = json.loads((OUT/'accounts.json').read_text())
    months = pd.DataFrame(json.loads((OUT/'monthly.json').read_text()))
    cl = pd.DataFrame(json.loads((OUT/'shape_test_metrics.json').read_text()))
    rules = json.loads((OUT/'rules_frozen.json').read_text())
    dm = json.loads((OUT/'daily_metrics.json').read_text())
    nav = pd.read_pickle(OUT/'nav.pkl')
    ledger = pd.read_pickle(OUT/'ledger.pkl')
    summaries = []
    for a in accounts:
        name = a['variant']; v = months[months.variant.eq(name)]
        c = ledger[ledger.variant.eq(name) & ledger.observed_3]
        summaries.append([NAMES[name], a['entries'], a['closed'], a['pending'], int(c.label_3.sum()), len(c),
                          float(c.label_3.mean()), a['total_return'], a['max_drawdown'], a['sharpe'],
                          v.return_.mean(), int(v.return_.gt(0).sum()), v.return_.min(), v.drawdown.max()])
    monthly = []
    for r in months.sort_values(['month', 'variant']).itertuples():
        monthly.append([r.month+'-01', NAMES[r.variant], r.entries, r.closed, r.return_, r.drawdown,
                        r.sharpe, r.profit, r.closing_nav, r.fees])
    dailyrows = []
    for r in dm:
        for scope, text in [('all', '原涨停池'), ('open', '次日开盘打开')]:
            x = r[scope]
            dailyrows.append([r['month']+'-01', r['daily_validation'],
                {'train':'训练拟合内', 'valid':'验证', 'forecast':'测试'}[r['part']], text,
                x['known'], x['rate'], x['ap'],
                x['top1']['n'], x['top1']['rate'], x['top3']['n'], x['top3']['rate'],
                x['top5']['n'], x['top5']['rate']])
    rulerows = []
    for r in rules:
        dv, sv = VARIANTS[r['variant']]
        dw = window(r['month'], dv); sw = r['window']
        rulerows.append([r['month']+'-01', NAMES[r['variant']], '至'.join(dw['train']), '至'.join(dw['valid']),
                        '至'.join(sw['train']), '至'.join(sw['valid']),
                        r['selected']['text'].replace('五分','四分'), r['train_base']['known'],
                        r['train_selected']['known'], r['train_selected']['hit'],
                        r['valid_base']['known'], r['valid_selected']['known'], r['valid_selected']['hit']])
    trades = []
    for r in ledger.sort_values(['entry_date','variant']).itertuples():
        d = r._asdict()
        mv = np.exp(d['log_float_mv_proxy'])/1e8 if 'log_float_mv_proxy' in d else np.nan
        trades.append([r.entry_date, NAMES[r.variant], r.d0_rank, r.score, r.rule_text,
                       r.high5, r.low5, r.body5, r.pullback5, r.open_gap,
                       r.entry_time, r.buy, r.buy/r.d1_preclose-1, r.exit_date, r.sell, r.return_,
                       r.cost, r.pnl, int(r.label_3), r.board_streak_days,
                       d.get('d0_turnover_pct'), d.get('d0_amount_100m'), mv,
                       int(r.state0 not in ('N','R')), r.d0_date])
    daily = [[r.date,NAMES[r.variant],r.nav,r.return_,r.drawdown,r.cash,r.market_value,r.buy,r.sales,r.fees]
             for r in nav.sort_values(['date','variant']).itertuples()]
    data = {
        '总体': table(['方案','买入笔','已卖笔','待卖笔','再板笔','标签笔','再板率','总收益','总回撤','夏普','月均收益','盈利月','最差月','最大月回撤'], summaries,
                    percent=[6,7,8,10,12,13],decimal=[9]),
        '月度': table(['月份','方案','买入笔','已卖笔','月收益','月回撤','夏普','盈利元','期末资产','费用元'],monthly,percent=[4,5],decimal=[6],dates=[0]),
        '日线评估': table(['月份','验证月数','集合','范围','标签数','基础再板率','平均精确率','Top1数','Top1命中','Top3数','Top3命中','Top5数','Top5命中'],dailyrows,
                        percent=[5,6,8,10,12],dates=[0]),
        '每月规则': table(['月份','方案','日线训练','日线验证','形态训练','形态验证','规则','训练基数','训练保留','训练命中','验证基数','验证保留','验证命中'],rulerows,
                        percent=[9,12],dates=[0]),
        '每日': table(['日期','方案','资产','日收益','回撤','现金','持仓市值','买入金额','卖回金额','费用'],daily,percent=[3,4],dates=[0]),
        '逐笔': table(['买入日期','方案','D0排名','评分','形态规则','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','买入涨幅','卖出日期','卖价','费后收益','投入元','盈亏元','三日再板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST','选股日期'],trades,
                      percent=[5,6,7,8,9,12,15],decimal=[3,11,14,20,21,22],dates=[0,13,24]),
    }
    dump(OUT/'workbook_data.json', data)
    # Headline comparisons are weighted by actual sample counts, not mean rates.
    aggregate = []
    for dv in [2,1]:
        for scope in ['all','open']:
            for k in [1,3,5]:
                z = [r[scope][f'top{k}'] for r in dm if r['daily_validation']==dv and r['part']=='forecast']
                n=sum(x['n'] for x in z); hits=sum(x['hits'] for x in z)
                aggregate.append(dict(validation=dv,scope=scope,top=k,n=n,hits=hits,hit=hits/n))
    dump(OUT/'daily_test_aggregate.json', aggregate)
    paired = []
    baseline = months[months.variant.eq('D2S2')].set_index('month')
    rng=np.random.default_rng(20261019)
    for name in VARIANTS:
        if name=='D2S2':continue
        q=months[months.variant.eq(name)].set_index('month')
        delta=q.return_-baseline.return_
        means=delta.to_numpy()[rng.integers(0,6,size=(10000,6))].mean(axis=1)
        paired.append(dict(variant=name,return_better_months=int(delta.gt(1e-12).sum()),
                           drawdown_lower_months=int(q.drawdown.lt(baseline.drawdown-1e-12).sum()),
                           mean_monthly_delta=float(delta.mean()), descriptive_bootstrap95=np.quantile(means,[.025,.975]).tolist()))
    dump(OUT/'paired_months.json',paired)
    md=['# R31：B2 一个月与两个月验证对照','',
        '本轮固定测试2026年4—9月六个完整月；两层均保持六个月训练。四组在看结果前冻结，没有按测试月份选赢家。', '',
        '## 已有记录', '',
        'R22做过五K形态1训1验、2训1验，日线仍6训2验，交易与资金口径也不同。未找到当前B2四K两层6训1验的同口径实验。', '',
        '## 设计与边界', '',
        '- 日线沿用51因子固定随机森林，sklearn1.7.1；仅训练窗向近期平移一个月，训练长度六个月、模型参数、标签、样本成熟及连板事件剔除不变。验证不合并重训。',
        '- 第二层保持79条简单规则及全部门槛，包括验证最少6笔。验证缩短后不会放宽门槛，样本不足按原规则回退全部保留。',
        '- 两层两月=原B2；日线一月=仅日线6+1；形态一月=仅形态6+1；两层一月=两层均6+1。',
        '- 日线一月、形态两月的混合组：形态验证第一月由前一个目标月的日线模型评分，第二月由本目标月模型评分，避免第一月落入本模型拟合区。按整个验证段末端统一剔除未成熟D4标签。',
        '- D0原Top3；D1四K后按时间与排名激活，锁定后实体>-8%，失败不替补；同股下一根合格K开价代理买入；D2收盘卖出。初始100万，空仓半款、有持仓余款全买，买卖各0.1%。',
        '- 账户从4月1日空仓起，9月30日按收盘估值。D0信号4—9月；9月末信号若10月才买入，不记入六个月账户，另保存全部信号结清对照。月收益按持仓日历月，标签统计按D0月份，不能混淆。',
        '- 形态选择使用D1—D3再板及D2—D4首次再板否则D4收盘收益代理；实际资金退出固定D2。这一差异沿用基准。',
        '- 这些历史月份已有研发观察，本轮为按时序回放，不是新盲测；分钟开价是历史成交代理，不能保证实盘确认后成交。', '',
        '## 六个月账户', '',
        '|方案|买入|累计收益|最大回撤|夏普|盈利月|最差月|', '|---|---:|---:|---:|---:|---:|---:|']
    for r in summaries:
        md.append(f'|{r[0]}|{r[1]}|{r[7]:+.2%}|{r[8]:.2%}|{r[9]:.2f}|{r[11]}/6|{r[12]:+.2%}|')
    md += ['', '## 按月收益／月内回撤', '', '|月份|'+'|'.join(NAMES.values())+'|', '|---|'+'---:|'*4]
    for month in MONTHS:
        md.append('|'+month+'|'+'|'.join(f"{months[(months.variant==v)&(months.month==month)].iloc[0].return_:+.2%} / {months[(months.variant==v)&(months.month==month)].iloc[0].drawdown:.2%}" for v in VARIANTS)+'|')
    md += ['', '## 日线测试命中率', '', '|验证月数|范围|TopK|命中/样本|命中率|', '|---|---|---|---|---|']
    for r in aggregate:
        md.append(f"|{r['validation']}|{'原涨停池' if r['scope']=='all' else '次日开盘打开'}|{r['top']}|{r['hits']}/{r['n']}|{r['hit']:.2%}|")
    md += ['', '## 分析', '',
        '两层都改一月验证，在本六个月取得更高账户收益和更低总体回撤：+96.28% / 16.24%，原B2为+33.17% / 20.79%。六个月中五个月收益高于原B2，但只有三个月月内回撤更低。四月回撤由4.33%扩大到9.09%，九月收益由+10.97%变为−10.69%，九月回撤由11.61%扩大到15.29%。因此不能归纳为月月更稳。', '',
        '日线层的分类没有同步改善：原D0池Top1由66.13%变为64.80%，开盘打开子集Top1为48.65%与48.72%，Top3为46.59%与45.49%。实际买入三日再板由35/79（44.30%）变为38/88（43.18%）。本轮优势是交易收益分布与筛选/资金路径的组合结果，不能称再板预测准确率显著提高。', '',
        '仅缩短日线验证，收益+45.62%但回撤28.96%；仅缩短形态验证，收益仅+5.84%、回撤25.49%。两层变化存在交互，不能把联合组结果直接归因于日线更及时或形态规则更准确。联合组4—7月均回退全部保留，8月规则r017、9月r051；原B2仅5、7月回退。一个月验证有效Top1基数7—13笔，明显少于原两个月的17—24笔。', '',
        '联合组相对基准月均收益差+7.43个百分点，六个月成对月份重抽的描述性95%范围约−5.48至+20.36个百分点，仍涵盖变差。这只反映当前六个月样本的不确定性，历史重复研发也使其不能作为独立确认。建议保留为研究候选，正式替换须由用户确认。', '',
        '数据质量：720个唯一股票日中715通过分钟核对，4个分钟缺失、1个D1日线缺失。全部保留缺失原因，缺失不作负标签，不向Top4补位。四组共327条D0交易信号；其中1条在10月才入场，六个月账户共326笔。原B2六个月规则、交易对象、买价均复现；生产evaluate_morning对四组327信号一致。']
    md += ['', '## 保存与复现', '',
        '- 14个新月模型覆盖2025年8月—2026年9月，较早月份用于第二层因果训练名单。模型、实际训练样本、全部评分、规则候选、逐月选中规则、分钟摘录、总账和每日净值均保存在本实验输出目录。',
        '- 原B2六个月规则与最终选股/买价精确复现，四组逐日现金独立核算。完整校验见verification.json；样本质量及剔除见coverage.json和exclusions.json。',
        '- 本轮为研究候选，未替换正式B2、未写生产模型表、未宣称阿里云盘已同步。',
        '- 运行：在sklearn1.7.1环境下，依次执行`python -m scripts.experiment_b2_validation_r31 train/prepare/select/evaluate/audit`（分别执行五个stage），随后运行`python -m scripts.report_b2_validation_r31`。',
        f'- 结果目录：`{OUT}`。']
    (DOC/'README.md').write_text('\n'.join(md)+'\n')
    for name in ['accounts.json','monthly.json','daily_test_aggregate.json','paired_months.json','coverage.json','verification.json','specification.json']:
        (DOC/name).write_bytes((OUT/name).read_bytes())
    print(pd.DataFrame(aggregate).to_string(index=False))
    print(json.dumps(paired,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
