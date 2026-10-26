"""Summarize every frozen CO24 arm, with unknown execution kept explicit."""
import math,json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,log_loss
from scripts.research_b1_six_factors_co24 import OUT,REPORT,SOURCE,END,BASE,SIX,ARMS,PROBS,read,save,sha,rank_topk

PERIODS={'2025_H1':('2025-01-01','2025-06-30'),'2025_H2':('2025-07-01','2025-12-31'),'2026_H1':('2026-01-01','2026-06-30'),'2026_Jul':('2026-07-01','2026-07-31'),'2026_all':('2026-01-01',END),'all':('2025-01-01',END)}

def stats(d):
 d=d.sort_values('signal_date');t=d[d.executed & d.net.notna()];r=d.daily_net.dropna().to_numpy();eq=np.r_[1.,np.cumprod(1+r)];dd=1-eq/np.maximum.accumulate(eq)
 before=d[d.selected & d.target.notna()];tag=t[t.target.notna()]
 return dict(days=len(d),selected=int(d.selected.sum()),trades=len(t),unknown_gate=int((d.selected&d.amount_gate.isna()).sum()),unknown_exit=int((d.executed&d.net.isna()).sum()),unknown_target=int((d.selected&d.target.isna()).sum()),net_compound_pct=(eq[-1]-1)*100,max_settlement_dd_pct=dd.max()*100,mean_net_pct=t.net.mean()*100,median_net_pct=t.net.median()*100,strong_pct=tag.target.ge(.03-1e-10).mean()*100,acceptable_pct=(tag.target.ge(.01-1e-10)&tag.target.lt(.03-1e-10)).mean()*100,error_pct=tag.target.lt(.01-1e-10).mean()*100,loss2=int(t.net.lt(-.02).sum()),loss2_pct=t.net.lt(-.02).mean()*100,loss5=int(t.net.lt(-.05).sum()),worst_trade_pct=t.net.min()*100,cvar5_pct=t.net.nsmallest(max(1,math.ceil(len(t)*.05))).mean()*100,pre_gate_strong_pct=before.target.ge(.03-1e-10).mean()*100,pre_gate_acceptable_pct=(before.target.ge(.01-1e-10)&before.target.lt(.03-1e-10)).mean()*100,pre_gate_error_pct=before.target.lt(.01-1e-10).mean()*100)

def assoc(panel):
 d=panel.copy();d['strong']=np.where(d.target.notna(),d.target.ge(.03-1e-10),np.nan);d['negative_target']=np.where(d.target.notna(),d.target.lt(0),np.nan);d['loss2']=np.where(d.net.notna(),d.net.lt(-.02),np.nan)
 discovery=d[d.signal_date.lt('2025-07-01')&d.next_date.le('2025-07-01')]
 rules=[];rows=[]
 for factor in SIX:
  v=discovery[factor].dropna();lo,hi=v.quantile([.25,.75]) if len(v) else (np.nan,np.nan)
  rules.append(dict(factor=factor,lo=lo,hi=hi,n=len(v),separable=bool(lo<hi)))
  if not lo<hi:continue
  periods={**PERIODS,**{m:(m+'-01',m+'-31') for m in sorted(d.signal_date.str[:7].unique())}}
  for period,(start,end) in periods.items():
   frame=d[d.signal_date.between(start,end)]
   if period=='2025_H1':frame=frame[frame.next_date.le('2025-07-01')]
   if period=='2025_H2':frame=frame[frame.next_date.le('2026-01-01')]
   frame=frame[frame.target.notna() & frame.net.notna()]
   low=frame[frame[factor].le(lo)];high=frame[frame[factor].ge(hi)]
   common=set(low.signal_date)&set(high.signal_date)
   for side,part in [('low',low[low.signal_date.isin(common)]),('high',high[high.signal_date.isin(common)])]:
    means=part.groupby('signal_date')[['strong','negative_target','loss2','net']].mean()
    rows.append(dict(factor=factor,period=period,side=side,rows=len(part),days=len(means),strong_pct=means.strong.mean()*100,negative_target_pct=means.negative_target.mean()*100,loss2_pct=means.loss2.mean()*100,mean_net_pct=means.net.mean()*100))
 pd.DataFrame(rules).to_csv(REPORT/'factor_group_rules.csv',index=False)
 pd.DataFrame(rows).to_csv(REPORT/'factor_groups.csv',index=False)
 related=['last5_return','last30_return','last30_close_position','day_max_drawdown','day_volume_hhi','day_vwap_distance','last30_vwap_distance','day_volume_acceleration','signal_return','volume_ratio']
 cor=d[SIX+related].corr(method='spearman').loc[SIX,related]
 cor.to_csv(REPORT/'factor_redundancy.csv')

def common_comparison(daily):
 unknown=daily.selected & (daily.amount_gate.isna() | (daily.executed & daily.net.isna()))
 bad=set(daily.loc[unknown,'signal_date']);clean=daily[~daily.signal_date.isin(bad)].copy()
 daily.loc[unknown,['arm','signal_date','symbol6','amount_gate','signal_price','close40','next_date']].to_csv(REPORT/'unknown_decisions.csv',index=False)
 rows=[];months=[]
 for arm,g in clean.groupby('arm'):
  for period,(start,end) in PERIODS.items():
   f=g[g.signal_date.between(start,end)];m=f.groupby('month').daily_net.apply(lambda x:np.prod(1+x)-1)
   rows.append(dict(arm=arm,period=period,positive_months=int(m.gt(0).sum()),months=len(m),**stats(f)))
  for month,f in g.groupby('month'):months.append(dict(arm=arm,month=month,**stats(f)))
 pd.DataFrame(rows).to_csv(REPORT/'common_known_summary.csv',index=False)
 pd.DataFrame(months).to_csv(REPORT/'common_known_monthly.csv',index=False)
 save(REPORT/'common_known_scope.json',dict(excluded_dates=sorted(bad),retained_dates=int(clean.signal_date.nunique()),explanation='Ex-post evaluation on jointly observable dates only, not an executable avoidance rule. In particular future suspension cannot be removed prospectively.'))
 return len(months)

def main():
 panel=read(OUT/'panel.csv.gz');panel['target']=panel.second_high/panel.signal_price-1;panel['net']=panel.close40*(1-.001)/(panel.signal_price*(1+.001))-1
 folds=[f for f in json.loads((SOURCE/'stage1_folds.json').read_text()) if f['day']<=END]
 frames=[];checks=[]
 for fold in folds:
  dest=OUT/'folds'/fold['day'];m=json.loads((dest/'complete.json').read_text())
  assert m['scores_sha256']==sha(dest/'scores.csv.gz')
  frames.append(read(dest/'scores.csv.gz'));checks.append(m)
 scores=pd.concat(frames,ignore_index=True);assert not scores.duplicated(['arm','sample_id']).any()
 full=scores.merge(panel,on=['sample_id','signal_date','symbol6'],validate='many_to_one')
 persisted=['sample_id','signal_date','next_date','symbol6','arm','predicted_class','signal_price','second_high','close40','target','net',*BASE,*SIX,*PROBS]
 full[persisted].to_csv(OUT/'oof_predictions.csv.gz',index=False,float_format='%.17g',compression={'method':'gzip','mtime':0})
 classification=[]
 for (arm,month),g in full.groupby(['arm',full.signal_date.str[:7]]):
  f=g[g.target.notna()];yy=f.target.ge(.03-1e-10);daily=[]
  for _,dd in f.groupby('signal_date'):
   y=dd.target.ge(.03-1e-10)
   if y.nunique()==2:daily.append(roc_auc_score(y,dd.p_ge3))
  classification.append(dict(arm=arm,month=month,rows=len(f),auc_ge3=roc_auc_score(yy,f.p_ge3) if yy.nunique()==2 else np.nan,mean_daily_auc_ge3=np.mean(daily),strong_base_pct=yy.mean()*100,mean_probability=f.p_ge3.mean()))
 pd.DataFrame(classification).to_csv(REPORT/'classification_monthly.csv',index=False)
 selected=pd.concat([rank_topk(g,1) for _,g in full.groupby('arm')],ignore_index=True)
 amounts=pd.concat([read(p) for p in sorted((SOURCE/'amounts').glob('*.csv.gz')) if p.name[:10]<=END],ignore_index=True)
 assert not amounts.duplicated(['date','symbol6']).any()
 calendar=read(SOURCE/'calendar.csv').date.tolist();wide=amounts.pivot(index='date',columns='symbol6',values='amount').reindex(calendar)
 med=wide.shift().rolling(20,min_periods=20).median()
 old=read(SOURCE/'oof_with_context.csv.gz').set_index(['signal_date','symbol6'])
 for col in ['amount','prior20_amount_median','amount_gate']:selected[col]=np.nan
 for i,r in selected.iterrows():
  key=(r.signal_date,r.symbol6)
  now=wide.at[key[0],key[1]] if key[1] in wide else np.nan
  prior=med.at[key[0],key[1]] if key[1] in med else np.nan
  gate=float(now>=prior) if np.isfinite(now) and np.isfinite(prior) and prior>0 else np.nan
  if key in old.index:now,prior,gate=old.loc[key,['amount','prior20_amount_median','amount_gate']]
  selected.loc[i,['amount','prior20_amount_median','amount_gate']]=[now,prior,gate]
 days=[f['day'] for f in folds];grid=pd.MultiIndex.from_product([list(ARMS),days],names=['arm','signal_date']).to_frame(index=False)
 daily=grid.merge(selected,on=['arm','signal_date'],how='left',validate='one_to_one')
 daily['selected']=daily.symbol6.notna();daily['executed']=daily.selected&daily.amount_gate.eq(1)
 daily['daily_net']=np.where(daily.executed,daily.net,0.)
 daily.loc[daily.selected&daily.amount_gate.isna(),'daily_net']=np.nan
 daily['month']=daily.signal_date.str[:7]
 daily.to_csv(REPORT/'daily_picks.csv.gz',index=False,float_format='%.17g')
 baseline=daily[daily.arm.eq('A7')&daily.selected].set_index(['signal_date','symbol6'])
 oldtop=old[old.selection_rank.eq(1)&old.index.get_level_values(0).isin(days)]
 assert set(baseline.index)==set(oldtop.index)
 np.testing.assert_allclose(baseline.amount_gate,oldtop.reindex(baseline.index).amount_gate,equal_nan=True)
 summaries=[];monthly=[];pressure=[];changes=[]
 for arm,g in daily.groupby('arm'):
  for period,(start,end) in PERIODS.items():
   f=g[g.signal_date.between(start,end)];s=stats(f);months=f.groupby('month').daily_net.apply(lambda x:np.prod(1+x.dropna())-1)
   summaries.append(dict(arm=arm,period=period,positive_months=int(months.gt(0).sum()),months=len(months),**s))
  for month,f in g.groupby('month'):monthly.append(dict(arm=arm,month=month,**stats(f)))
  mr=g.groupby('month').daily_net.apply(lambda x:np.prod(1+x.dropna())-1);best=mr.idxmax()
  p1=g[g.month.ne(best)];p2=g.copy();tr=p2[p2.executed&p2.net.gt(0)];ids=tr.groupby('month').net.idxmax();p2.loc[ids,'daily_net']=0;p2.loc[ids,'executed']=False
  pressure.append(dict(arm=arm,best_month=best,ex_best_month_pct=stats(p1)['net_compound_pct'],ex_month_best_trade_pct=stats(p2)['net_compound_pct']))
  if arm=='A7':continue
  a=daily[daily.arm.eq('A7')].set_index('signal_date');b=g.set_index('signal_date');same=a.symbol6.eq(b.symbol6)&b.executed
  lost=a.executed&~same;new=b.executed&~(a.executed&a.symbol6.eq(b.symbol6))
  changes.append(dict(arm=arm,baseline_loss2_not_retained=int((lost&a.net.lt(-.02)).sum()),baseline_profitable_not_retained=int((lost&a.net.gt(0)).sum()),new_trades=int(new.sum()),new_loss2=int((new&b.net.lt(-.02)).sum()),same_execution_days=int((a.executed&same).sum())))
 pd.DataFrame(summaries).to_csv(REPORT/'summary.csv',index=False);pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv',index=False)
 pd.DataFrame(pressure).to_csv(REPORT/'stress.csv',index=False);pd.DataFrame(changes).to_csv(REPORT/'selection_changes.csv',index=False)
 assoc(panel)
 common_checks=common_comparison(daily)
 # Independent arithmetic loop checks each month's gross/cost equity and drawdown.
 for r in monthly:
  d=daily[daily.arm.eq(r['arm'])&daily.month.eq(r['month'])].sort_values('signal_date');balance=peak=1.;maxdd=0.
  for x in d.itertuples():
   if x.executed and pd.notna(x.close40):balance*=x.close40*(1-.001)/(x.signal_price*(1+.001))
   peak=max(peak,balance);maxdd=max(maxdd,1-balance/peak)
  np.testing.assert_allclose([(balance-1)*100,maxdd*100],[r['net_compound_pct'],r['max_settlement_dd_pct']],atol=1e-10)
 save(REPORT/'verification.json',dict(folds=len(folds),models=len(folds)*len(ARMS),all_baselines_reproduced=True,original_top1_matched=len(baseline),monthly_cash_checks=len(monthly),common_monthly_rows=common_checks,training_panel_sha256=sha(OUT/'panel.csv.gz'),oof_sha256=sha(OUT/'oof_predictions.csv.gz'),code_sha256=sha(__file__)))
 print(pd.DataFrame(summaries).query("period=='all'").to_string(index=False),flush=True)

if __name__=='__main__':main()
