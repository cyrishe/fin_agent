"""R32 report: common-period comparison and separately labelled early history."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.experiment_b2_validation_full_r32 import OUT,BASE,EARLY,CONT,MONTHS,COMMON,r,KEY
from scripts.report_b2_validation_r31 import table
from scripts.report_b2_d2_metrics_r31 import metrics

DOC=Path('docs/stock_automl_runs/20261019_b2_validation_full_r32')
NAMES={'D2S2':'两层两月','D1S1':'两层一月'}

def main():
    DOC.mkdir(parents=True,exist_ok=True)
    accounts=json.loads((OUT/'accounts.json').read_text())
    months=pd.DataFrame(json.loads((OUT/'monthly.json').read_text()))
    ledger=pd.read_pickle(OUT/'ledger.pkl');nav=pd.read_pickle(OUT/'nav.pkl')
    rules=json.loads((OUT/'all_rules.json').read_text());dm=json.loads((OUT/'daily_metrics.json').read_text())
    main_accounts=[x for x in accounts if x['scope']!='六个月复核']
    summaries=[];d2=[]
    for a in main_accounts:
        name=a['variant'];scope=a['scope'];v=months[months.variant.eq(name)&months.scope.eq(scope)]
        g=ledger[ledger.variant.eq(name)&ledger.scope.eq(scope)];c=g[g.observed_3]
        label=NAMES[name]+'·'+scope
        summaries.append([label,a['entries'],a['closed'],a['pending'],int(c.label_3.sum()),len(c),float(c.label_3.mean()),a['total_return'],a['max_drawdown'],a['sharpe'],v.return_.mean(),int(v.return_.gt(0).sum()),v.return_.min(),v.drawdown.max()])
        for period in ['总体',*sorted(g.entry_date.str[:7].unique())]:
            q=g if period=='总体' else g[g.entry_date.str.startswith(period)]
            d2.append(dict(scope=scope,variant=name,period=period,**metrics(q)))
    r.dump(OUT/'d2_metrics.json',d2)
    monthly=[]
    for m in MONTHS:
        def row(scope,variant):
            x=months[months.month.eq(m)&months.scope.eq(scope)&months.variant.eq(variant)]
            return x.iloc[0] if len(x) else None
        new=row('共同区间','D1S1');old=row('共同区间','D2S2');full=row('一月全期','D1S1')
        def get(x,k):return x[k] if x is not None else None
        monthly.append([m+'-01',get(old,'return_'),get(new,'return_'),get(new,'return_')-get(old,'return_') if old is not None else None,get(old,'drawdown'),get(new,'drawdown'),get(new,'drawdown')-get(old,'drawdown') if old is not None else None,get(old,'entries'),get(new,'entries'),get(full,'return_'),get(full,'drawdown'),get(full,'entries')])
    d2rows=[[x['period']+'-01' if x['period']!='总体' else '总体',NAMES[x['variant']],x['scope'],x['closed'],x['pending'],x['win_rate'],x['mean'],x['median'],x['bad5_rate'],x['d2_limit_rate'],x['d12_limit_rate'],x['only_d3']] for x in d2]
    dailyrows=[]
    for x in dm:
        for scope,text in [('all','原涨停池'),('open','次日开盘打开')]:
            z=x[scope]
            dailyrows.append([x['month']+'-01',x['daily_validation'],{'train':'训练拟合内','valid':'验证','forecast':'测试'}[x['part']],text,z['known'],z['rate'],z['ap'],z['top1']['n'],z['top1']['rate'],z['top3']['n'],z['top3']['rate'],z['top5']['n'],z['top5']['rate']])
    rulerows=[]
    for x in sorted(rules,key=lambda v:(v['month'],v['variant'])):
        w=r.window(x['month'],1 if x['variant']=='D1S1' else 2)
        rulerows.append([x['month']+'-01',NAMES[x['variant']],'至'.join(w['train']),'至'.join(w['valid']),'至'.join(x['window']['train']),'至'.join(x['window']['valid']),x['selected']['text'].replace('五分','四分'),x['train_base']['known'],x['train_selected']['known'],x['train_selected']['hit'],x['valid_base']['known'],x['valid_selected']['known'],x['valid_selected']['hit']])
    # Show each actual trade once. New series uses its continuous full-history account.
    keep=(ledger.variant.eq('D2S2')&ledger.scope.eq('共同区间'))|(ledger.variant.eq('D1S1')&ledger.scope.eq('一月全期'))
    trades=[]
    for x in ledger[keep].sort_values(['entry_date','variant']).itertuples():
        d=x._asdict();mv=np.exp(d['log_float_mv_proxy'])/1e8 if pd.notna(d.get('log_float_mv_proxy')) else None
        trades.append([x.entry_date,NAMES[x.variant],x.d0_rank,x.score,x.rule_text,x.high5,x.low5,x.body5,x.pullback5,x.open_gap,x.entry_time,x.buy,x.buy/x.d1_preclose-1,x.exit_date,x.sell,x.return_,x.cost,x.pnl,int(x.label_3) if x.observed_3 else None,x.board_streak_days,d.get('d0_turnover_pct'),d.get('d0_amount_100m'),mv,int(x.state0 not in ('N','R')),x.d0_date,'新增早期' if x.d0_date<'2025-05-01' else '共同区间',int(x.d1_up) if pd.notna(x.d1_up) else None,int(x.d2_up) if pd.notna(x.d2_up) else None])
    navkeep=nav.scope.isin(['共同区间','一月全期'])
    daily=[[x.date,NAMES[x.variant]+'·'+x.scope,x.nav,x.return_,x.drawdown,x.cash,x.market_value,x.buy,x.sales,x.fees] for x in nav[navkeep].sort_values(['date','variant','scope']).itertuples()]
    # Original Top3, including blocked/missing records; no fabricated hypothetical fills.
    f=pd.read_pickle(OUT/'enriched.pkl');new=f[f.part.eq('test')].copy()
    old=pd.read_pickle(r.four.OUT/'four_all_top3.pkl');old=old[old.part.eq('test')].copy()
    old=pd.concat([pd.read_pickle(EARLY/'top3_four.pkl'),old],ignore_index=True);old['variant']='D2S2'
    candidates=pd.concat([new,old],ignore_index=True)
    rulesmap={(x['variant'],x['month']):x['selected'] for x in rules}
    signals=pd.read_pickle(OUT/'signals.pkl').set_index(['variant',*KEY])
    candidate_rows=[]
    for x in candidates.sort_values(['d0_date','variant','d0_rank']).itertuples():
        d=x._asdict();rule=rulesmap[(x.variant,x.d0_date[:7])];idx=(x.variant,x.symbol,x.d0_date)
        chosen=idx in signals.index
        rowframe=pd.DataFrame([d]);passed=bool(x.eligible and r.shape.mask(rowframe,rule).iloc[0])
        candidate_rows.append([x.d0_date,NAMES[x.variant],x.d0_rank,x.score,x.d1_date,int(bool(x.eligible)),d.get('reason'),rule['text'].replace('五分','四分'),int(passed),int(chosen),x.high5,x.low5,x.body5,x.pullback5,x.open_gap,signals.loc[idx,'entry_time'] if chosen else None,signals.loc[idx,'entry'] if chosen else None,int(x.label_3) if x.observed_3 else None,int(x.d1_up) if pd.notna(x.d1_up) else None,int(x.d2_up) if pd.notna(x.d2_up) else None,x.board_streak_days,d.get('d0_turnover_pct'),d.get('d0_amount_100m'),np.exp(d['log_float_mv_proxy'])/1e8 if pd.notna(d.get('log_float_mv_proxy')) else None,int(x.state0 not in ('N','R')) if x.state0!='unknown' else None])
    data={
        '总体':table(['方案及区间','买入笔','已卖笔','待卖笔','再板笔','标签笔','再板率','总收益','总回撤','夏普','月均收益','盈利月','最差月','最大月回撤'],summaries,percent=[6,7,8,10,12,13],decimal=[9]),
        '月度':table(['月份','两月收益','一月收益','收益差','两月回撤','一月回撤','回撤差','两月笔数','一月笔数','一月全期收益','一月全期回撤','一月全期笔数'],monthly,percent=[1,2,3,4,5,6,9,10],dates=[0]),
        'D2评估':table(['买入月份','方案','区间','已卖笔','待卖笔','D2胜率','D2均收益','D2中位收益','亏超5%','D2封板率','两日再板率','仅D3再板笔'],d2rows,percent=[5,6,7,8,9,10],dates=[0]),
        '日线评估':table(['月份','验证月数','集合','范围','标签数','基础再板率','平均精确率','Top1数','Top1命中','Top3数','Top3命中','Top5数','Top5命中'],dailyrows,percent=[5,6,8,10,12],dates=[0]),
        '每月规则':table(['月份','方案','日线训练','日线验证','形态训练','形态验证','规则','训练基数','训练保留','训练命中','验证基数','验证保留','验证命中'],rulerows,percent=[9,12],dates=[0]),
        '每日':table(['日期','方案及区间','资产','日收益','回撤','现金','持仓市值','买入金额','卖回金额','费用'],daily,percent=[3,4],dates=[0]),
        '逐笔':table(['买入日期','方案','D0排名','评分','形态规则','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','买入涨幅','卖出日期','卖价','费后收益','投入元','盈亏元','三日再板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST','选股日期','区间','D1封板','D2封板'],trades,percent=[5,6,7,8,9,12,15],decimal=[3,11,14,20,21,22],dates=[0,13,24]),
        '前三候选':table(['选股日期','方案','D0排名','评分','次日日期','数据可用','核对结果','形态规则','形态通过','最终入选','四K最高','四K最低','四K实体','高点回落','开盘涨幅','买入K结束','买价','三日再板','D1封板','D2封板','原连板','D0换手百分数','D0成交额亿','流通估值亿','是否ST'],candidate_rows,percent=[10,11,12,13,14],decimal=[3,16,21,22,23],dates=[0,4])}
    notes={
        '总体':'共同区间：2025-05起；一月全期：2025-03起；均100万元起步，截至2026-10-14。D2收盘卖，买卖各0.1%。',
        '月度':'收益差=一月−两月，回撤差负值更好。B:I为共同起点账户；J:L为一月从3月起连续账户。10月仅截至14日。',
        'D2评估':'已卖笔的费后收益；月度按买入月份。待卖不当0；两日再板含D1和D2。',
        '日线评估':'标签为D1—D3收盘再板；TopK固定D0排名，开盘筛选后不补位。训练为拟合内。',
        '每月规则':'两层均6月训练；形态训练用历史月度因果评分Top1，验证最少6笔，回退全部保留时仍有实体门槛。',
        '每日':'三条账户分别核算；空仓买一半，有持仓买余款；月末待卖按收价计值。',
        '逐笔':'每笔仅列一次；一月资金来自3月起全期账户。K时刻为结束标签，取该K开价；费后收益未卖留空；流通估值为代理值。',
        '前三候选':'保留未入选及缺失记录；最终入选遵循原策略。只有入选有买价；数据可用含历史非ST与分钟核对。'}
    for k,v in notes.items():
        data[k]['note']=v
        data[k]['title']='B2 两层一月验证全期对照 R32'
    r.dump(OUT/'workbook_data.json',data)
    aggregate=[]
    for dv in [2,1]:
        for scope in ['all','open']:
            for k in [1,3,5]:
                z=[x[scope][f'top{k}'] for x in dm if x['daily_validation']==dv and x['part']=='forecast' and x['month'] in COMMON]
                n=sum(x['n'] for x in z);hits=sum(x['hits'] for x in z)
                aggregate.append(dict(validation=dv,scope=scope,top=k,n=n,hits=hits,hit=hits/n))
    r.dump(OUT/'daily_test_aggregate.json',aggregate)
    oldm=months[(months.scope=='共同区间')&(months.variant=='D2S2')].set_index('month')
    newm=months[(months.scope=='共同区间')&(months.variant=='D1S1')].set_index('month')
    delta=newm.return_-oldm.return_;dd=newm.drawdown-oldm.drawdown
    paired=dict(months=len(delta),return_better_months=int(delta.gt(1e-12).sum()),drawdown_lower_months=int(dd.lt(-1e-12).sum()),both_better_months=int((delta.gt(1e-12)&dd.lt(-1e-12)).sum()),mean_monthly_return_delta=float(delta.mean()),mean_monthly_drawdown_delta=float(dd.mean()),full_month_return_better=int(delta.iloc[:-1].gt(1e-12).sum()),full_months=len(delta)-1)
    r.dump(OUT/'paired_months.json',paired)
    years=[]
    for name,g in [('D2S2',oldm),('D1S1',newm)]:
        for year,z in g.groupby(g.index.str[:4]):
            years.append(dict(variant=name,year=year,months=len(z),return_=float((1+z.return_).prod()-1),positive=int(z.return_.gt(0).sum())))
    r.dump(OUT/'year_segments.json',years)
    rule_counts=[]
    for name in ['D2S2','D1S1']:
        selected=[x for x in rules if x['variant']==name and x['month'] in COMMON]
        rule_counts.append(dict(variant=name,months=len(selected),fallback=sum(x['selected']['id']=='all' for x in selected),valid_min=min(x['valid_base']['known'] for x in selected),valid_max=max(x['valid_base']['known'] for x in selected)))
    r.dump(OUT/'rule_diagnostics.json',rule_counts)
    md=['# R32：B2 两层一月验证，全区间回测','',
        '## 范围与固定条件','',
        '- 新方案两层均六个月训练、一个月验证，固定日线随机森林及四K选规参数。正式B2保持原版本。',
        '- 日线模型自2024年8月起；第二层自2025年3月起可按六个月训练、一个月验证形成规则。原B2第二层最早2025年5月。',
        '- 公平比较均从2025年5月1日空仓100万元起，至2026年10月14日；一月全期另从2025年3月1日起。新增早期只供观察，不算原方案输赢。',
        '- 每月第一层仅拟合训练段，验证不回并；第二层训练使用历史因果月模型Top1评分，验证使用当前未拟合验证月评分；D4成熟和连板事件清洗沿用。',
        '- D0原Top3，四根完整K观察，按首次开价低于涨停且有量的分钟激活，时间优先再排名。锁定后实体>-8%，失败不顺延；同股下一根合格分钟开价代理买入，截止09:40结束标签。',
        '- D1买入、D2下一交易日收盘卖；空仓半款，有昨日持仓余款全买；买卖各0.1%，允许碎股。月收益按日历净值，D2逐笔指标按买入月。',
        '- 夏普按包含空仓日的每日账户收益计算，252日年化，无风险利率0。月均收益是月收益的算术平均，不是逐笔收益之和。',
        '- 日线/形态标签仍为三日再板；形态选规收益护栏仍为D2—D4首次板否则D4退出代理。本轮仅扩展一月验证全期，不改目标或执行方式。',
        '- 2026年10月不完整，最后待卖按收盘估值。分钟开价为历史成交代理，未模拟排队和容量。研发已经观察过这些历史，非新外部盲测。','',
        '## 账户结果','',
        '|区间/方案|买入|已卖|待卖|总收益|总回撤|夏普|盈利月|最差月|','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for a,s in zip(main_accounts,summaries):md.append(f"|{s[0]}|{a['entries']}|{a['closed']}|{a['pending']}|{a['total_return']:+.2%}|{a['max_drawdown']:.2%}|{a['sharpe']:.2f}|{a['positive_months']}/{a['months']}|{s[12]:+.2%}|")
    md+=['','## 共同区间月度','', '|月份|两月收益|一月收益|两月回撤|一月回撤|','|---|---:|---:|---:|---:|']
    for m in COMMON:md.append(f'|{m}|{oldm.loc[m,"return_"]:+.2%}|{newm.loc[m,"return_"]:+.2%}|{oldm.loc[m,"drawdown"]:.2%}|{newm.loc[m,"drawdown"]:.2%}|')
    md+=['','## D2费后逐笔','', '|方案/区间|已卖|胜率|笔均|中位|亏超5%|','|---|---:|---:|---:|---:|---:|']
    for a in d2:
        if a['period']=='总体':md.append(f"|{NAMES[a['variant']]}·{a['scope']}|{a['closed']}|{a['win_rate']:.2%}|{a['mean']:+.2%}|{a['median']:+.2%}|{a['bad5_rate']:.2%}|")
    md+=['','## 分析结论','',
        '完整共同区间不支持把一月验证直接替换为正式方案。账户收益由两月的+160.85%降为一月的+48.50%，最大回撤由40.20%增至47.41%，盈利月份由13/18降至8/18。两组各有一笔待卖，均按10月14日收盘估值。',
        f"一月方案在{paired['return_better_months']}/18个月收益较高，但只有{paired['drawdown_lower_months']}/18个月回撤更低，同时改善的仅{paired['both_better_months']}/18个月。剔除不完整10月后，收益胜出为{paired['full_month_return_better']}/17个月。月均收益低约3.06个百分点，平均月内回撤高约3.04个百分点，不能称全期更稳。最差月为2025年7月−17.53%，最大月内回撤为2025年11月31.98%。",
        'D2实际交易也走弱：费后胜率从51.56%降到49.26%，笔均从+1.13%降到+0.50%，中位从+0.32%降到−0.09%，亏超5%从27.56%增至29.04%。因此差异不只是买入笔数或资金复利路径；每笔收益分布本身也较弱。',
        '差异有明显时期性：2025年5—12月一月方案−38.41%，两月+20.43%；2026年1月至10月14日一月+141.12%，两月+116.59%。后者仍有一月方案优势，但不能据此回头按年份切换赢家。',
        'R31的2026年4—9月六个月结果已按相同起点空仓复现：一月+96.28%、回撤16.24%。本全期账户会携带3月底仓位进入4月，所以月度表4月与R31单独起算不同；模型分数、月规则、选股和买价已核对一致。此前六个月的结论确实只适用于该段，不能推广至全期。',
        '共同区间的日线原Top1再板率，两月71.18%、一月68.68%；开盘打开子集Top1为61.54%、58.08%。最终买入三日再板为54.67%、48.53%。更近的训练窗口未带来本区间分类准确率提升。',
        '形态层样本明显减少：一月验证基数仅4—13笔，回退全部保留14/18个月；两月基数15—27笔，回退3/18个月。回退仍保留四K实体>-8%、原Top3、激活和延迟买入规则。小验证样本及筛选强度改变是可观察的关联，未单独隔离因果，不能把所有下降都归咎于验证样本少。',
        '一月方案新增2025年3—4月32笔，收益+11.37%，但最大回撤19.05%，其中3月为负；全20个月305笔、+65.38%、回撤48.59%。这两个月没有同起点两月方案，单列供人工分析。',
        '结论：一月验证保留为研究分支，当前不建议以其替换原B2。原两月方案自身40.20%的全期回撤仍高，也不能因相对胜出就视为稳健。两组都是已研究历史的时序回放，不能当作新的独立验证。',
        '', '## 保存与复现','',
        '27个一月验证月模型均保存，其中14个来自R31原文件逐字节复用，新增13个。保存实际训练样本、全部评分、79条规则候选、月度规则、首10分钟证据、Top3和交易总账。',
        '独立核对模型重载分数/排名、原B2全期账户、R31六个月规则/选股/独立起点账户、生产evaluate_morning、逐日现金与D2价格费用。详见verification.json。',
        '复现：sklearn1.7.1环境，依次运行 scripts.experiment_b2_validation_full_r32 的 train、prepare、select、evaluate、audit，再运行 scripts.report_b2_validation_full_r32。',
        '本研究不更新生产模型表；待用户结合Excel确认。归档保存本地，云盘状态另记录，不宣称已上传。']
    (DOC/'README.md').write_text('\n'.join(md)+'\n')
    for filename in ['specification.json','accounts.json','monthly.json','d2_metrics.json','daily_test_aggregate.json','paired_months.json','year_segments.json','rule_diagnostics.json','verification.json','coverage.json']:
        (DOC/filename).write_bytes((OUT/filename).read_bytes())
    print(json.dumps(paired,ensure_ascii=False,indent=2));print(pd.DataFrame([x for x in d2 if x['period']=='总体']).to_string(index=False))

if __name__=='__main__':main()
