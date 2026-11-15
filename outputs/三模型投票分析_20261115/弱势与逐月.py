from pathlib import Path
import json,numpy as np,pandas as pd
P=Path(__file__).parent;R=P.parent;d=pd.read_csv(P/'各模型逐日.csv');base=pd.read_csv(R/'三模型近期连续落后_20261115/逐日对照.csv');
q=d[d.指标.eq('D1收板')];mat=q.pivot(index='D0',columns='方案',values='日均');valid=q.pivot(index='D0',columns='方案',values='完整').fillna(False).astype(bool);b=base[base.指标.eq('D1收板')].set_index('D0');j=mat.join(b[['随机期望','连板优先','组合完整','连板完整']],how='left');ok=valid['两票及以上']&valid['一票']&valid['组合等权']&j.组合完整.fillna(False)&j.连板完整.fillna(False)&j.随机期望.notna();j=j[ok].copy();j['月']=j.index.str[:7];j['年']=j.index.str[:4]
j.groupby('年')[['一票','两票及以上','组合等权','按票数加权','随机期望','连板优先']].agg(['size','mean']).to_csv(P/'逐年同日封板.csv',encoding='utf-8-sig')
j[j.index>='2026-01-01'].groupby('月')[['一票','两票及以上','组合等权','按票数加权','随机期望','连板优先']].agg(['size','mean']).to_csv(P/'2026逐月同日封板.csv',encoding='utf-8-sig')
caldir=R/'b2_stage_review_r140_r142_20261026/archive_package/inputs/source_calendars';cal=set().union(*(set(json.loads(f.read_text())) for f in caldir.glob('*.json')));daily=pd.read_pickle(R/'b2_d0_live_rebuild_20261112/daily_source.pkl.gz');cal|=set(daily.trade_date.astype(str));cal.add('2026-11-13');cal=sorted(cal);idx={x:i for i,x in enumerate(cal)}
raw=b[['组合','随机期望','组合完整']].copy();raw['差']=raw.组合-raw.随机期望;raw.loc[~raw.组合完整,'差']=np.nan
rolling=[]
for day in j.index:
 i=idx.get(day,-1)
 if i<5:continue
 previous=cal[i-5:i]; vals=raw.reindex(previous)['差'];
 if vals.notna().all():rolling.append({'D0':day,'前五日封板差':vals.mean()})
t=j.reset_index().merge(pd.DataFrame(rolling),on='D0',how='left');t=t[t.前五日封板差.notna()].copy();t['前五日较随机']=np.where(t.前五日封板差.lt(0),'较弱','非较弱');t.to_csv(P/'滚动五日状态与同日结果.csv',index=False,encoding='utf-8-sig')
out=t.groupby(['年','前五日较随机'])[['一票','两票及以上','组合等权','按票数加权','随机期望','连板优先']].agg(['size','mean']);out.to_csv(P/'滚动五日弱势逐年.csv',encoding='utf-8-sig')
print(out.round(2).to_string());print(j[j.index>='2026-01-01'].groupby('月')[['一票','两票及以上','组合等权','随机期望','连板优先']].mean().round(2).to_string())
