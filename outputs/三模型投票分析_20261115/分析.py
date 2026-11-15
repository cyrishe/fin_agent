from pathlib import Path
import numpy as np,pandas as pd
R=Path('/Volumes/ext/fin_agent/outputs');P=Path(__file__).parent;H=R/'三模型全月_20261113';B=R/'三模型近期连续落后_20261115';N=R/'最近两周三模型_20261113/十一月独立'
details=pd.read_csv(B/'逐股对照.csv',dtype={'代码':str});base=pd.read_csv(B/'逐日对照.csv');hist=[]
for y in range(2018,2027):
 s=pd.read_csv(H/f'账户/{y}/每日选股.csv',dtype={'symbol':str});hist.append(s[['d0_date','symbol','日排','月排','验排','前二票数']])
sel=pd.concat(hist,ignore_index=True);recent=pd.read_csv(N/'最近十日每日选股.csv',dtype={'symbol':str});recent=recent.rename(columns={'每日滚动排名':'日排','每月训练排名':'月排','六训二验排名':'验排','票数':'前二票数'});sel=pd.concat([sel,recent[['d0_date','symbol','日排','月排','验排','前二票数']]],ignore_index=True)
z=details[details.策略.eq('组合')].merge(sel,left_on=['D0','代码'],right_on=['d0_date','symbol'],validate='one_to_one');assert len(z)==len(details[details.策略.eq('组合')]);assert (z[['日排','月排','验排']].le(2).sum(axis=1)==z.前二票数).all()
for c in ['D1封板','D2封板']:
 z[c]=z[c].map({'True':1,'False':0,True:1,False:0})
z['连续收板']=z['D1封板']*z['D2封板'];z['D2亏超7']=np.where(z['D2收益%'].notna(),(z['D2收益%']<=-7).astype(int)*100,np.nan);z['D1收板']=z['D1封板']*100;z['连续收板']=z['连续收板']*100
z.to_csv(P/'逐股票数与结果.csv',index=False,encoding='utf-8-sig')
rows=[]
for day,g in z.groupby('D0',sort=True):
 for metric in ['D1收板','连续收板','D2收益%','D2亏超7']:
  for name,sub in [('日模型前二',g[g.日排.le(2)]),('月模型前二',g[g.月排.le(2)]),('六训二验前二',g[g.验排.le(2)]),('组合等权',g),('一票',g[g.前二票数.eq(1)]),('两票',g[g.前二票数.eq(2)]),('三票',g[g.前二票数.eq(3)]),('两票及以上',g[g.前二票数.ge(2)])]:
   v=sub[metric];rows.append({'D0':day,'指标':metric,'方案':name,'股票数':len(sub),'已知数':v.notna().sum(),'完整':len(sub)>0 and v.notna().all(),'日均':v.mean() if len(v) else np.nan})
  v=g[metric];rows.append({'D0':day,'指标':metric,'方案':'按票数加权','股票数':len(g),'已知数':v.notna().sum(),'完整':v.notna().all(),'日均':np.average(v,weights=g.前二票数) if v.notna().all() else np.nan})
d=pd.DataFrame(rows);d.to_csv(P/'各模型逐日.csv',index=False,encoding='utf-8-sig')
summ=[];paired=[]
periods={'2024年':'2024-01-01,2024-12-31','2025年':'2025-01-01,2025-12-31','2026年1至10月':'2026-01-01,2026-10-31','11月近期':'2026-11-02,2026-11-12','本次连续弱势':'2026-10-27,2026-11-05','2024弱势1':'2024-03-04,2024-03-15','2024弱势2':'2024-07-05,2024-07-19','2024弱势3':'2024-07-30,2024-08-12'}
for label,dates in periods.items():
 start,end=dates.split(','); part=d[d.D0.between(start,end)]
 for metric in d.指标.unique():
  q=part[part.指标.eq(metric)];mat=q.pivot(index='D0',columns='方案',values='日均');valid=q.pivot(index='D0',columns='方案',values='完整').fillna(False).astype(bool)
  for name in mat.columns:
   day=valid[name]; vals=mat.loc[day,name]; counts=q[(q.方案==name)&q.完整]; sub=z[z.D0.isin(counts.D0)]
   if name=='日模型前二':sub=sub[sub.日排.le(2)]
   elif name=='月模型前二':sub=sub[sub.月排.le(2)]
   elif name=='六训二验前二':sub=sub[sub.验排.le(2)]
   elif name=='一票':sub=sub[sub.前二票数.eq(1)]
   elif name=='两票':sub=sub[sub.前二票数.eq(2)]
   elif name=='三票':sub=sub[sub.前二票数.eq(3)]
   elif name=='两票及以上':sub=sub[sub.前二票数.ge(2)]
   summ.append({'区间':label,'指标':metric,'方案':name,'有效日数':len(vals),'股次数':len(sub),'逐日等权':vals.mean(),'按股次':sub[metric].mean() if name!='按票数加权' else np.nan})
  for n in ['两票及以上','按票数加权']:
   for other in ['一票','组合等权']:
    mask=valid[n]&valid[other];pair=mat.loc[mask,[n,other]].dropna(); paired.append({'区间':label,'指标':metric,'比较':n+'减'+other,'共同日':len(pair),'差值':(pair[n]-pair[other]).mean(),'更好天数':(pair[n]>pair[other]+1e-9).sum(),'更差天数':(pair[n]<pair[other]-1e-9).sum()})
pd.DataFrame(summ).to_csv(P/'分段汇总.csv',index=False,encoding='utf-8-sig');pd.DataFrame(paired).to_csv(P/'同日配对.csv',index=False,encoding='utf-8-sig')
# On the same days where both one-vote and 2+ groups have complete outcomes,
# compare against each day's original full-pool expectation and streak baseline.
out=[]
for metric,bmetric in [('D1收板','D1收板'),('连续收板','D1D2连续收板'),('D2收益%','D2价格收益')]:
 q=d[d.指标.eq(metric)]; m=q.pivot(index='D0',columns='方案',values='日均');v=q.pivot(index='D0',columns='方案',values='完整').fillna(False).astype(bool);bb=base[base.指标.eq(bmetric)].set_index('D0');j=m.join(bb[['随机期望','连板优先','组合完整','连板完整']],how='left');ok=v['两票及以上']&v['一票']&v['组合等权']&j.组合完整.fillna(False)&j.连板完整.fillna(False)&j.随机期望.notna();j=j[ok]
 for label,dates in periods.items():
  start,end=dates.split(',');a=j.loc[(j.index>=start)&(j.index<=end)]
  out.append({'区间':label,'指标':metric,'共同日':len(a),**{c:a[c].mean() for c in ['一票','两票及以上','组合等权','按票数加权','随机期望','连板优先']}})
pd.DataFrame(out).to_csv(P/'有重合且有单票同日对照.csv',index=False,encoding='utf-8-sig')
print('完成',len(z),'股次',z.D0.nunique(),'日')
