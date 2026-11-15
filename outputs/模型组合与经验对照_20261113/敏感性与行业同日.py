from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).parent;x=pd.read_pickle(P/'全部候选及结果.pkl.gz');rows=[]
for day,g in x.groupby('d0_date'):
 g=g[g.ret.notna()&(g.d1_open-g.d1_cap).abs().ge(.005)];s=g[g.组合入选]
 if not len(s):continue
 rows.append({'D0':day,'年份':day[:4],'组合可算数':len(s),'随机池可算数':len(g),'组合平均价格收益%':s.ret.mean(),'随机池平均价格收益%':g.ret.mean(),'组合上涨率%':100*s.ret.gt(0).mean(),'随机池上涨率%':100*g.ret.gt(0).mean()})
d=pd.DataFrame(rows);d.to_csv(P/'非涨停开盘_逐日敏感性.csv',index=False);z=d.groupby('年份',as_index=False).agg(共同日=('D0','size'),组合股次=('组合可算数','sum'),**{c:(c,'mean') for c in ['组合平均价格收益%','随机池平均价格收益%','组合上涨率%','随机池上涨率%']});z.to_csv(P/'非涨停开盘_逐年敏感性.csv',index=False)
x=pd.read_pickle(P/'带行业全部候选.pkl.gz');x=x[x.组合入选&x.ret.notna()&x.当日行业组.isin(['较弱三分之一','较强三分之一'])];v=x.groupby(['d0_date','当日行业组']).ret.mean().unstack().dropna();v['强减弱']=v['较强三分之一']-v['较弱三分之一'];v['年份']=v.index.str[:4];v.to_csv(P/'行业强弱_同日差异.csv');a=v.groupby('年份',as_index=False).agg(共同日=('强减弱','size'),强行业=('较强三分之一','mean'),弱行业=('较弱三分之一','mean'),强减弱=('强减弱','mean'));a.to_csv(P/'行业强弱_逐年同日.csv',index=False);print('非涨停开盘',z.to_string(index=False));print('行业同日',a.to_string(index=False))
