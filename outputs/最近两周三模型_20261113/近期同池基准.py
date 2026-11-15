from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).parent;x=pd.read_pickle(P/'十一月全候选与排名.pkl.gz');q=pd.read_csv(P/'十一月十三日双源校验.csv',dtype={'symbol':str});q['usable']=q.公开源最新分钟日期.fillna('').str.startswith('2026-11-13')&q.最新价差.le(.011);q=q[['symbol','前收','开盘','最新价','原始涨停标记','usable']];x=x.merge(q,on='symbol',how='left',validate='many_to_one');a=x.d0_date.eq('2026-11-12') & x.usable.fillna(False)&(x.前收-x.close).abs().le(.011);x.loc[a,'d1_up']=x.loc[a,'原始涨停标记'].astype(bool);x.loc[a,'d1_open']=x.loc[a,'开盘'];x.loc[a,'d1_close']=x.loc[a,'最新价'];b=x.d0_date.eq('2026-11-11') & x.usable.fillna(False)&(x.前收-x.d1_close).abs().le(.011);x.loc[b,'d2_up']=x.loc[b,'原始涨停标记'].astype(bool);x.loc[b,'d2_close']=x.loc[b,'最新价'];x.loc[b,'d2_preclose']=x.loc[b,'前收'];x['价格收益']=(100*(x.d2_close*.999/(x.d1_open*1.001)-1)).where(x.d1_open.gt(0)&x.d2_close.gt(0)&x.d1_volume.gt(0)&(x.d1_preclose-x.close).abs().le(.011)&(x.d2_preclose-x.d1_close).abs().le(.011));rows=[]
for day,g in x.groupby('d0_date'):
 for label,mask in [('D1收板',g.d1_up.notna()),('D1D2连续收板',g.d1_up.notna()&g.d2_up.notna()),('D2价格收益',g.价格收益.notna())]:
  target=g[g.票数.gt(0)];known=mask & (g.d1_up.notna() if label=='D1收板' else True);pool=g[known];chosen=target[target.index.isin(pool.index)];complete=len(chosen)==len(target) and len(pool)>0
  if label=='D1收板':p=pool.d1_up.astype(bool);v=chosen.d1_up.astype(bool)
  elif label=='D1D2连续收板':p=pool.d1_up.astype(bool)&pool.d2_up.astype(bool);v=chosen.d1_up.astype(bool)&chosen.d2_up.astype(bool)
  else:p=pool.价格收益.gt(0);v=chosen.价格收益.gt(0)
  rows.append({'D0':day,'指标':label,'完整对比':complete,'组合股次':len(target),'组合已知':len(chosen),'同池已知':len(pool),'组合命中率%':100*v.mean() if len(v) else np.nan,'同池随机期望%':100*p.mean() if len(p) else np.nan,'组合命中数':int(v.sum()),'同池命中数':int(p.sum())})
z=pd.DataFrame(rows);z.to_csv(P/'十一月独立/近期同池逐日对照.csv',index=False);valid=z[z.完整对比];summary=valid.groupby('指标',as_index=False).agg(共同日=('D0','size'),组合股次=('组合股次','sum'),组合命中=('组合命中数','sum'),**{'组合逐日平均%':('组合命中率%','mean'),'同池随机逐日平均%':('同池随机期望%','mean')});summary.to_csv(P/'十一月独立/近期同池汇总.csv',index=False);print(summary.to_string(index=False));print(z.to_string(index=False))
