from pathlib import Path
import json
import pandas as pd
import numpy as np
R=Path('/data/cyris/work/b2_d1_floor_minutes_r84_20261022')
S=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
s=pd.read_pickle(S/'joined_306.pkl')
d=pd.read_csv(R/'306笔D1开盘五分钟逐笔.csv')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
d['id']=d['股票']+'|'+d['D0选中日']
d=d.set_index('id').loc[s.id].reset_index()
assert len(d)==len(s)==306 and d.id.equals(s.id)
flags={
'基准不额外过滤':np.zeros(len(d),dtype=bool),
'开盘即跌停':d['开盘即跌停'].eq(1).to_numpy(),
'前五分钟任何收盘跌停':d['前五分钟收盘跌停数'].ge(1).to_numpy(),
'前五分钟五根均收盘跌停':d['前五分钟收盘跌停数'].eq(5).to_numpy(),
'前四分钟四根均收盘跌停':d['前四分钟收盘跌停数'].eq(4).to_numpy(),
'09:35收盘跌停':d['0935收盘跌停'].eq(1).to_numpy(),
'既有D1_Q75':d['既有D1_Q75预警'].eq(1).to_numpy(),
'既有D1_Q75再加前五根均跌停':(d['既有D1_Q75预警'].eq(1)|d['前五分钟收盘跌停数'].eq(5)).to_numpy()
}
def run(frame,keep,mode,start,end):
 cash=1e6;peak=cash;maxdd=0;hold={};events={}
 for row in frame.loc[keep].to_dict('records'):
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
  events.setdefault(row['d2_date'],[]).append(('15:00' if mode=='close' else row['sell7_time'],1,row))
 for day in (day for day in cal if start<=day<=end):
  for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not hold else 1.)
    shares=cost/(1.001*row['entry'])
    cash-=cost
    hold[row['id']]={'shares':shares,'d1_close':row['d1_close']}
   else:
    lot=hold.pop(row['id'])
    cash+=lot['shares']*(row['d2_close'] if mode=='close' else row['sell7'])*.999
  nav=cash+sum(x['shares']*x['d1_close'] for x in hold.values())
  peak=max(peak,nav);maxdd=max(maxdd,1-nav/peak)
 assert not hold
 return cash/1e6-1,maxdd
rows=[]
for mode in ['morning7','close']:
 for name,flag in flags.items():
  keep=~flag
  for yr,g in s.groupby('d0_year'):
   ix=g.index.to_numpy()
   ret,dd=run(g,keep[ix],mode,str(yr)+'-01-01',g.d2_date.max())
   rows.append({'卖法':mode,'规则':name,'期间':str(yr),'候选笔数':len(g),'禁买笔数':int(flag[ix].sum()),
                '账户收益率_pct':ret*100,'最大回撤_pct':dd*100,
                '剩余未核实卖出笔数':int(g.loc[keep[ix], 'seven_unverified' if mode=='morning7' else 'close_unverified'].sum())})
  ret,dd=run(s,keep,mode,'2022-01-01',s.d2_date.max())
  rows.append({'卖法':mode,'规则':name,'期间':'2022-2026连续','候选笔数':len(s),'禁买笔数':int(flag.sum()),
               '账户收益率_pct':ret*100,'最大回撤_pct':dd*100,
               '剩余未核实卖出笔数':int(s.loc[keep, 'seven_unverified' if mode=='morning7' else 'close_unverified'].sum())})
a=pd.DataFrame(rows)
ref=pd.read_csv(S/'逐年独立重置_全部方案.csv')
for _,r in a[(a.规则=='基准不额外过滤')&(a.期间!='2022-2026连续')].iterrows():
 e=ref[(ref.卖法==r.卖法)&(ref.方案=='baseline')&(ref.D0年.astype(str)==r.期间)].iloc[0]
 assert abs(r['账户收益率_pct']/100-e['账户独立重置收益率'])<1e-9
 assert abs(r['最大回撤_pct']/100-e['账户独立重置最大回撤'])<1e-9
whole=pd.read_csv(S/'全期连续账户_全部方案.csv')
for _,r in a[(a.规则=='基准不额外过滤')&(a.期间=='2022-2026连续')].iterrows():
 e=whole[(whole.卖法==r.卖法)&(whole.方案=='baseline')].iloc[0]
 assert abs(r['账户收益率_pct']/100-e['账户全期连续收益率'])<1e-9
 assert abs(r['最大回撤_pct']/100-e['账户全期最大回撤'])<1e-9
assert flags['前四分钟四根均收盘跌停'].tolist()==flags['前五分钟五根均收盘跌停'].tolist()
assert (flags['既有D1_Q75再加前五根均跌停']==flags['既有D1_Q75']).all()
a.to_csv(R/'各禁买口径逐年与连续账户价格假设.csv',index=False,encoding='utf-8-sig')
print(a[(a.卖法=='morning7')&(a.规则.isin(['基准不额外过滤','开盘即跌停','前五分钟任何收盘跌停','前五分钟五根均收盘跌停','既有D1_Q75']))].to_string(index=False))
print(a[(a.卖法=='close')&(a.规则.isin(['基准不额外过滤','前五分钟五根均收盘跌停']))].to_string(index=False))
