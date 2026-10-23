from pathlib import Path
import json
import numpy as np
import pandas as pd

R=Path('/data/cyris/work')
O=R/'b2_prebuy_volume_warning_r83_20261022'
S=R/'b2_news_flow_years_r76_20261022'
s=pd.read_pickle(S/'joined_306.pkl')
cal=json.loads((R/'b2_d2_intraday_r67_20261021'/'inputs/calendar.json').read_text())
d=pd.read_csv(O/'306笔D0_D1买前指标与结果.csv')
assert len(s)==len(d)==306 and s.id.nunique()==306
d=d.set_index(d['股票']+'|'+d['D0选中日'])
same='D1首分钟量除板前20同刻最大量'
train_cut=float(d.loc[d.split.eq('train'),same].quantile(.1))
accel_cut=float(d.loc[d.split.eq('train'),'early_volume_accel'].quantile(.2))
flags={
 '无新预警':pd.Series(False,index=d.index),
 'D1前四K跌停一字':d['D1前四K下限一字'].eq(1),
 'D1同刻最大量比训练低10%':d[same].le(train_cut),
 'D1后两K量比训练低20%':d.early_volume_accel.le(accel_cut),
 '既有D1_Q75':d.fit_sens_D1_q75.eq(1),
 '既有D1_Q75加同刻低10%':d.fit_sens_D1_q75.eq(1)|d[same].le(train_cut),
 'D1同刻量低10%且旧D1_Q75未提示':d[same].le(train_cut)&d.fit_sens_D1_q75.eq(0),
}
fee=.001

def run(frame,keep,start,end):
 cash=1e6;peak=cash;maxdd=0.;hold={};events={}
 for row in frame.loc[keep].to_dict('records'):
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
  events.setdefault(row['d2_date'],[]).append((row['sell7_time'],1,row))
 for day in (day for day in cal if start<=day<=end):
  for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not hold else 1.)
    shares=cost/((1+fee)*row['entry']);cash-=cost
    hold[row['id']]={'shares':shares,'d1_close':row['d1_close']}
   else:
    lot=hold.pop(row['id'])
    cash+=lot['shares']*row['sell7']*(1-fee)
  nav=cash+sum(x['shares']*x['d1_close'] for x in hold.values())
  peak=max(peak,nav)
  dd=1-nav/peak
  maxdd=max(maxdd,dd) if day!=start else dd
 assert not hold
 return cash/1e6-1,maxdd

rows=[]
for name,flag in flags.items():
 keep=~flag.reindex(s.id).fillna(False).to_numpy(bool)
 assert len(keep)==306
 for yr,g in s.groupby('d0_year'):
  ix=g.index.to_numpy()
  ret,dd=run(g,keep[ix],str(yr)+'-01-01',g.d2_date.max())
  rows.append({'预警':name,'期间':str(yr),'候选数':len(g),'禁买数':int((~keep[ix]).sum()),
               '账户收益率':ret,'最大回撤':dd,
               '仍未核实卖出':int(g.loc[keep[ix],'seven_unverified'].sum())})
 ret,dd=run(s,keep,'2022-01-01',s.d2_date.max())
 rows.append({'预警':name,'期间':'2022-2026连续','候选数':306,'禁买数':int((~keep).sum()),
              '账户收益率':ret,'最大回撤':dd,
              '仍未核实卖出':int(s.loc[keep,'seven_unverified'].sum())})
out=pd.DataFrame(rows)
baseline=pd.read_csv(S/'逐年独立重置_全部方案.csv')
for _,r in out[out.预警.eq('无新预警')&out.期间.ne('2022-2026连续')].iterrows():
 b=baseline[(baseline.卖法=='morning7')&(baseline.方案=='baseline')&(baseline.D0年.astype(str)==r.期间)].iloc[0]
 assert np.isclose(r.账户收益率,b.账户独立重置收益率,atol=1e-10)
 assert np.isclose(r.最大回撤,b.账户独立重置最大回撤,atol=1e-10)
full=pd.read_csv(S/'全期连续账户_全部方案.csv')
b=full[(full.卖法=='morning7')&(full.方案=='baseline')].iloc[0]
base=out[(out.预警=='无新预警')&(out.期间=='2022-2026连续')].iloc[0]
assert np.isclose(base.账户收益率,b.账户全期连续收益率,atol=1e-10)
out.to_csv(O/'固定预警下_年度与连续账户价格假设.csv',index=False,encoding='utf-8-sig')
print('cutoffs',train_cut,accel_cut)
print(out.to_string(index=False))
