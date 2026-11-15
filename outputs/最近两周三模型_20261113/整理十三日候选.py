from pathlib import Path
import json,pandas as pd,numpy as np
P=Path(__file__).parent;x=pd.read_pickle(P/'十三日双模型候选.pkl.gz').merge(pd.read_pickle(P/'十三日原环境评分.pkl.gz'),on=['symbol','d0_date'],validate='one_to_one');q=pd.read_pickle(P/'十三日全市场双源.pkl.gz');x=x.merge(q[['stk_code','name']],on='stk_code',how='left',validate='one_to_one')
for name in ['每日滚动','每月训练','六训二验']:
 o=x.sort_values([name,'symbol'],ascending=[False,True]);x.loc[o.index,name+'排名']=np.arange(1,len(x)+1)
x['票数']=x[[n+'排名' for n in ['每日滚动','每月训练','六训二验']]].le(2).sum(axis=1);x['排名均值']=x[[n+'排名' for n in ['每日滚动','每月训练','六训二验']]].mean(axis=1);s=x[x.票数.gt(0)].sort_values(['票数','排名均值','symbol'],ascending=[False,True,True]);fields=['d0_date','symbol','name','close','票数','每日滚动排名','每月训练排名','六训二验排名'];s[fields].to_csv(P/'十三日三模型候选.csv',index=False);x.to_pickle(P/'十三日全部候选与排名.pkl.gz',compression='gzip');print(s[fields].to_string(index=False));print('唯一股票',len(s),'D1待下个交易日',flush=True)
