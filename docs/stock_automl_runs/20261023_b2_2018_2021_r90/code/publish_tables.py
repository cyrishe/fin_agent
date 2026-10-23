from pathlib import Path
import json,pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
O=R/'deliverables';O.mkdir(exist_ok=True)
monthly=pd.read_csv(R/'primary_monthly_model.csv')
annual=pd.read_csv(R/'primary_model_year.csv')
strict_annual=pd.read_csv(R/'rejected_strict_extrema'/'primary_model_year.csv')
signals=pd.read_pickle(R/'signals_exit.pkl')
signals=signals[signals.d0_date.ge('2018-01-01')].copy()
signals['month']=signals.d0_date.str[:7]
gate=json.loads((R/'overall.json').read_text())['gate']
signals['paused']=signals.month.map(lambda m:gate[m]['paused'])
allowed=signals[~signals.paused].copy()
counts=allowed.groupby('month').agg(上午早卖笔数=('early_sell','sum'),D2分钟缺档笔数=('d2_quality',lambda x:int(x.eq('minute_missing').sum())),D2跌停收盘假卖笔数=('d2_close_floor',lambda x:int((x&~allowed.loc[x.index,'early_sell']).sum())))
m=monthly.set_index('month').join(counts,how='left').fillna({'上午早卖笔数':0,'D2分钟缺档笔数':0,'D2跌停收盘假卖笔数':0})
out=pd.DataFrame({
 '月份':m.index,
 '停摆':m.paused.astype(bool),
 '影子候选笔数':m.shadow_candidates.astype(int),
 '实际买入笔数':m.main_pause_7pct_trades.astype(int),
 '纯收盘模型月收益_百分数':(m.V2_pause_close_month_model_return*100).round(4),
 '7%卖法模型月收益_百分数':(m.main_pause_7pct_month_model_return*100).round(4),
 '7%卖法交易胜率_百分数':(m.main_pause_7pct_win_rate*100).round(4),
 '上午早卖笔数':m['上午早卖笔数'].astype(int),
 'D2分钟缺档笔数':m['D2分钟缺档笔数'].astype(int),
 'D2跌停收盘假卖笔数':m['D2跌停收盘假卖笔数'].astype(int),
})
out.to_csv(O/'逐月_2018至2021.csv',index=False,encoding='utf-8-sig')
years=[]
for year in ['2018','2019','2020','2021']:
 q=annual[(annual.year.astype(str)==year)&annual.scenario.eq('V2_pause_close')].iloc[0]
 e=annual[(annual.year.astype(str)==year)&annual.scenario.eq('pause_7pct')].iloc[0]
 z=strict_annual[(strict_annual.year.astype(str)==year)&strict_annual.scenario.eq('pause_7pct')].iloc[0]
 a=out[out.月份.str.startswith(year)&~out.停摆]
 years.append({'年份':int(year),'放行月数':len(a),'盈利月数':int(a['7%卖法模型月收益_百分数'].gt(0).sum()),'交易笔数':int(e.model_trades),'纯收盘收益_百分数':round(q.return_*100,4),'上午7%卖法收益_百分数':round(e.return_*100,4),'上午7%卖法最大回撤_百分数':round(e.max_drawdown*100,4),'上午7%卖法交易胜率_百分数':round(e.win_rate*100,4),'严格分钟口径7%卖法收益_百分数':round(z.return_*100,4),'严格分钟口径交易笔数':int(z.model_trades)})
pd.DataFrame(years).to_csv(O/'逐年_2018至2021.csv',index=False,encoding='utf-8-sig')
t=pd.read_csv(R/'trade_decisions.csv')
t=t[t.model_month.ge('2018-01')&~t.paused].copy()
t['close_trade_return_pct']=(t.close_trade_return*100).round(5)
t['early_trade_return_pct']=(t.early_trade_return*100).round(5)
t.to_csv(O/'逐笔_主策略.csv',index=False,encoding='utf-8-sig')
for src,dst in [('quality_by_year.csv','数据覆盖_逐年.csv'),('d2_quality_issues.csv','D2分钟问题逐笔.csv'),('gate_sensitivity.csv','停摆口径差异月.csv'),('risk_split_effect.csv','买前风控_分期.csv'),('risk_account_comparison.csv','买前风控_账户.csv')]:
 (O/dst).write_bytes((R/src).read_bytes())
main=json.loads((R/'primary_overall.json').read_text())['scenarios']
strict=json.loads((R/'rejected_strict_extrema'/'primary_overall.json').read_text())['scenarios']
summary={'main_close':main['V2_pause_close'],'main_7pct':main['pause_7pct'],'strict_7pct':strict['pause_7pct'],'main_shadow':main['shadow_close']}
(O/'总体账户.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(pd.DataFrame(years).to_string(index=False),flush=True)
print('monthly',len(out),'trades',len(t),'numeric',pd.to_numeric(out['7%卖法模型月收益_百分数']).notna().all(),flush=True)
