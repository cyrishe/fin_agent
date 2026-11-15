from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).parent;x=pd.read_pickle(P/'全部候选及结果.pkl.gz');names=['组合各取前二','随机同池','连板数优先','成交额优先'];out=[];details=[]
for day,g in x.groupby('d0_date',sort=True):
 g=g.copy();k=int(g.组合入选.sum());assert 2<=k<=6
 selected={'组合各取前二':g[g.组合入选], '连板数优先':g.sort_values(['board_streak_days','symbol'],ascending=[False,True]).head(k),'成交额优先':g.sort_values(['d0_amount_100m','symbol'],ascending=[False,True]).head(k)}
 for name,z in selected.items():
  assert len(z)==k
  for r in z.itertuples():details.append({'D0':day,'策略':name,'代码':r.symbol,'连板数':r.board_streak_days,'D0成交额亿元':r.d0_amount_100m,'D2价格收益%':r.ret,'D3价格收益%':r.d3_ret})
 for horizon,col in [('D2收盘','ret'),('D3收盘','d3_ret')]:
  pool=g[g[col].notna()];valid=len(pool)>=k and all(z[col].notna().all() for z in selected.values())
  for name in names:
   z=pool if name=='随机同池' else selected[name];v=z[col].dropna();known=z.d1_up.notna()&z.d2_up.notna();out.append({'D0':day,'月份':day[:7],'年份':day[:4],'比较终点':horizon,'策略':name,'应选只数':k,'候选池只数':len(g),'池内可计算只数':len(pool),'已选可计算只数':len(v),'共同可比较':valid,'平均价格收益%':v.mean(),'上涨率%':100*v.gt(0).mean(),'大跌至少7%率':100*v.le(-7).mean(),'涨至少7%率':100*v.ge(7).mean(),'D1收板率%':100*pd.to_numeric(z.d1_up,errors='coerce').mean(),'D2收板率%':100*pd.to_numeric(z.d2_up,errors='coerce').mean(),'D1D2连续收板率%':100*(z.loc[known,'d1_up'].astype(bool)&z.loc[known,'d2_up'].astype(bool)).mean(),'D1涨停开盘率%':100*((z.d1_open-z.d1_cap).abs().lt(.005)).mean()})
daily=pd.DataFrame(out);daily.to_csv(P/'每日同池对照.csv',index=False);pd.DataFrame(details).to_csv(P/'经验策略逐股.csv',index=False)
metrics=['平均价格收益%','上涨率%','大跌至少7%率','涨至少7%率','D1收板率%','D2收板率%','D1D2连续收板率%','D1涨停开盘率%'];valid=daily[daily.共同可比较].copy();annual=valid.groupby(['年份','比较终点','策略'],as_index=False).agg(共同日=('D0','nunique'),**{m:(m,'mean') for m in metrics});annual.to_csv(P/'逐年同日对照.csv',index=False);monthly=valid.groupby(['月份','比较终点','策略'],as_index=False).agg(共同日=('D0','nunique'),**{m:(m,'mean') for m in metrics});monthly.to_csv(P/'逐月同日对照.csv',index=False)
diffs=[];rng=np.random.default_rng(20261113)
for horizon in ['D2收盘','D3收盘']:
 d=valid[valid.比较终点.eq(horizon)];wide=d.pivot(index=['月份','D0'],columns='策略',values='平均价格收益%');assert wide.notna().all().all();months=sorted(set(wide.index.get_level_values(0)))
 for comparator in names[1:]:
  delta=wide['组合各取前二']-wide[comparator];groups=[delta.loc[m].to_numpy() for m in months];samples=np.array([np.concatenate([groups[i] for i in rng.integers(0,len(groups),len(groups))]).mean() for _ in range(10000)]);diffs.append({'终点':horizon,'对照':comparator,'共同天数':len(delta),'月份数':len(months),'收益差百分点':delta.mean(),'月度重采样95%下限':np.quantile(samples,.025),'月度重采样95%上限':np.quantile(samples,.975),'注意':'事后探索，月块重采样不能解除历史策略筛选及同股依赖'})
pd.DataFrame(diffs).to_csv(P/'差异月度重采样.csv',index=False)
print('全部日',x.d0_date.nunique(),'共同日',valid.groupby('比较终点').D0.nunique().to_dict(),flush=True);print(annual.to_string(index=False),flush=True);print(pd.DataFrame(diffs).to_string(index=False),flush=True)
