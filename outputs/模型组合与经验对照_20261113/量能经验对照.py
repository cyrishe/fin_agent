from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).parent;x=pd.read_pickle(P/'全部候选及结果.pkl.gz');rows=[];picks=[]
for day,g in x.groupby('d0_date',sort=True):
 combo=g[g.组合入选];k=len(combo)
 for name,col in [('D0相对板前量优先','d0_normal_volume_ratio'),('连板成交量递增优先','board_volume_slope')]:
  available=g[g[col].replace([np.inf,-np.inf],np.nan).notna()];chosen=available.sort_values([col,'symbol'],ascending=[False,True]).head(k)
  if len(chosen)<k:continue
  for r in chosen.itertuples():picks.append({'D0':day,'策略':name,'代码':r.symbol,'D2收益%':r.ret,'D3收益%':r.d3_ret,'排序值':getattr(r,col)})
  for horizon,metric in [('D2收盘','ret'),('D3收盘','d3_ret')]:
   if combo[metric].isna().any() or chosen[metric].isna().any():continue
   for m,v in [('组合各取前二',combo[metric]),(name,chosen[metric])]:rows.append({'D0':day,'年份':day[:4],'月份':day[:7],'终点':horizon,'对比':name,'策略':m,'平均收益%':v.mean(),'上涨率%':100*v.gt(0).mean(),'跌至少7%率':100*v.le(-7).mean()})
d=pd.DataFrame(rows);d.to_csv(P/'量能基准逐日.csv',index=False);pd.DataFrame(picks).to_csv(P/'量能基准逐股.csv',index=False);a=d.groupby(['年份','终点','对比','策略'],as_index=False).agg(共同日=('D0','nunique'),**{c:(c,'mean') for c in ['平均收益%','上涨率%','跌至少7%率']});a.to_csv(P/'量能基准逐年.csv',index=False);print(a.to_string(index=False))
