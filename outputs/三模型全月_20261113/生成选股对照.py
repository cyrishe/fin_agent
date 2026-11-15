from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).parent;old=P.parent/'多年份训练与组合对照_20261113';early=P/'早期/全部输入.pkl.gz';late=P/'全部输入.pkl.gz';near=P/'近月/全部输入.pkl.gz';alt=P/'2022备用来源/全部输入.pkl.gz';data={'early':pd.read_pickle(early),'late':pd.read_pickle(late),'near':pd.read_pickle(near),'alt':pd.read_pickle(alt)}; rows=[]
for month in pd.period_range('2018-01','2026-10',freq='M').astype(str):
 y=month[:4];candidate=data['alt' if '2022-01'<=month<='2022-04' else 'early' if month<'2022-01' else 'near' if month=='2026-10' else 'late'];candidate=candidate[candidate.d0_date.str.startswith(month)].copy();q=P/'账户'/y/'每日选股.csv';backup=old/'账户'/y/'每日选股.csv';selection=pd.read_csv(q,dtype={'symbol':str}) if q.exists() else pd.DataFrame();selection=selection[selection['月份'].astype(str).eq(month)] if len(selection) else selection
 if selection.empty and backup.exists():
  selection=pd.read_csv(backup,dtype={'symbol':str});selection=selection[selection['月份'].astype(str).eq(month)&selection['方法'].eq('各取前二去重')]
 if selection.empty:continue
 chosen=candidate.merge(selection[['d0_date','symbol']],on=['d0_date','symbol'],validate='one_to_one');assert len(chosen)==len(selection),(month,len(chosen),len(selection))
 for df in (candidate,chosen):
  df['连续封板']=np.where(df.d1_up.notna()&df.d2_up.notna(),df.d1_up.eq(True)&df.d2_up.eq(True),np.nan)
 row={'月份':month,'选中股次':len(chosen),'候选股次':len(candidate)}
 for label,col in [('D1封板','d1_up'),('D1D2连续封板','连续封板'),('D2收盘收益%','ret')]:
  row['选中'+label+'有数据股次']=int(chosen[col].notna().sum());row['候选'+label+'有数据股次']=int(candidate[col].notna().sum());a=chosen.groupby('d0_date')[col].mean().rename('sel');b=candidate.groupby('d0_date')[col].mean().rename('base');z=pd.concat([a,b],axis=1).dropna();row[label+'共同天数']=len(z);row['选中'+label+'日均']=float(z.sel.mean()) if len(z) else np.nan;row['随机'+label+'日均']=float(z.base.mean()) if len(z) else np.nan
 rows.append(row)
out=pd.DataFrame(rows)
for col in ['选中D1封板日均','随机D1封板日均','选中D1D2连续封板日均','随机D1D2连续封板日均']:
 if col in out:out[col]=out[col]*100
out=out.rename(columns={'选中D1封板日均':'选中D1封板率%','随机D1封板日均':'随机D1封板率%','选中D1D2连续封板日均':'选中D1D2连续封板率%','随机D1D2连续封板日均':'随机D1D2连续封板率%'})
out.to_csv(P/'选股对照_已完成.csv',index=False,encoding='utf-8-sig');print('已计算月份',len(rows))
