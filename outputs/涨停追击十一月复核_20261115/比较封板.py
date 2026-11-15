from pathlib import Path
import pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent/outputs');P=Path(__file__).parent;N=R/'最近两周三模型_20261113';x=pd.read_pickle(N/'十一月全候选与排名.pkl.gz');q=pd.read_csv(N/'十一月十三日双源校验.csv',dtype={'symbol':str});q['usable']=q.公开源最新分钟日期.fillna('').str.startswith('2026-11-13')&q.最新价差.le(.011);x=x.merge(q[['symbol','前收','原始涨停标记','usable']],on='symbol',how='left',validate='many_to_one');mask=x.d0_date.eq('2026-11-12')&x.usable.fillna(False)&(x.前收-x.close).abs().le(.011);x.loc[mask,'d1_up']=x.loc[mask,'原始涨停标记'].astype(bool)
f=pd.read_csv(P/'风险明细.csv',dtype={'symbol':str});x=x.merge(f[['d0_date','symbol','排除']],on=['d0_date','symbol'],how='left',validate='one_to_one');rows=[]
for day,g in x.groupby('d0_date'):
 chosen=g[g.票数.gt(0)];assert chosen.排除.notna().all();filtered=chosen[~chosen.排除.astype(bool)];k=len(filtered);board=g.sort_values(['board_streak_days','symbol'],ascending=[False,True]).head(k);pool=g[g.d1_up.notna()];valid=k>0 and filtered.d1_up.notna().all() and board.d1_up.notna().all() and len(pool)>0
 rows.append({'D0':day,'过滤后选股数':k,'候选池已知':len(pool),'可比较':valid,'过滤后封板%':100*pd.to_numeric(filtered.d1_up).mean() if valid else np.nan,'随机期望封板%':100*pd.to_numeric(pool.d1_up).mean() if valid else np.nan,'连板优先封板%':100*pd.to_numeric(board.d1_up).mean() if valid else np.nan,'过滤后封板数':int(pd.to_numeric(filtered.d1_up).sum()) if valid else 0})
t=pd.DataFrame(rows);t.to_csv(P/'过滤后D1同池对照.csv',index=False,encoding='utf-8-sig');print(t.round(2).to_string(index=False));print(t[t.可比较][['过滤后封板%','随机期望封板%','连板优先封板%']].mean().round(3).to_string())
