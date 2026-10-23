from pathlib import Path
import json
import pandas as pd
O=Path('/data/cyris/work/b2_idle_cross_r87_20261023')
a=pd.read_csv(O/'逐日原Top3轮空与未评分替补.csv',dtype={'base_code':str,'Q75_unchecked_alt_code':str,'body_prefilter_code':str}).set_index('D0')
d=pd.read_csv('/data/cyris/work/b2_floor_countermeasure_r86_20261022/306笔_综合因子与基线.csv',encoding='utf-8-sig',dtype={'symbol':str})
d=d[d.d0_date.between('2025-09-01','2026-09-30')].copy()
f=pd.read_pickle('/data/cyris/fin_agent_deploy/b2-v2-20261021/assets/reference/four_all_top3.pkl')
f=f[f.part.eq('test')].set_index(['d0_date','symbol'])
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
def original(r):
 return dict(id=r.id,d0=r.d0_date,d1=r.d1_date,d2=r.d2_date,entry=float(r.entry),d1close=float(r.d1_close),exit=float(r.d2_close),time=r.buy_clock,origin='原')
def substitute(day,code,price,when):
 q=f.loc[(day,code)]
 d1=str(q.d1_date)
 d2=cal[cal.index(d1)+1]
 return dict(id=code+'|'+day,d0=day,d1=d1,d2=d2,entry=float(price),d1close=float(q.d1_close),exit=float(q.d2_close),time=str(when)[11:16],origin='未评分替补')
base=[original(r) for r in d.itertuples(index=False)]
skip=[original(r) for r in d.itertuples(index=False) if not int(r.D1_Q75)]
replace=skip.copy()
for day,r in a[a.Q75_flag.eq(1)&a.Q75_unchecked_alt_code.notna()].iterrows():
 replace.append(substitute(day,r.Q75_unchecked_alt_code,r.Q75_unchecked_alt_entry,r.Q75_unchecked_alt_time))
body=base.copy()
for day,r in a[a.base_buy.eq(0)&a.body_prefilter_code.notna()].iterrows():
 body.append(substitute(day,r.body_prefilter_code,r.body_prefilter_entry,r.body_prefilter_time))
def replay(rows,start,end):
 cash=1e6;hold={};peak=cash;maxdd=0.;events={}
 for r in rows:
  events.setdefault(r['d1'],[]).append((r['time'],0,r))
  events.setdefault(r['d2'],[]).append(('15:00',1,r))
 for day in [d for d in cal if start<=d<=end]:
  for clock,kind,r in sorted(events.get(day,[]),key=lambda t:(t[0],t[1])):
   if kind==0:
    invest=cash*(.5 if not hold else 1.)
    shares=invest/(r['entry']*1.001)
    cash-=invest
    hold[r['id']]=(shares,r['d1close'])
   else:
    shares,_=hold.pop(r['id'])
    cash+=shares*r['exit']*.999
  nav=cash+sum(sh*mark for sh,mark in hold.values())
  peak=max(peak,nav);maxdd=max(maxdd,1-nav/peak)
 assert not hold
 return cash/1e6-1,maxdd
out=[]
for name,rows in [('原买入',base),('Q75禁买不替补',skip),('Q75禁买未评分替补',replace),('原买入加实体提前筛选',body)]:
 ret,dd=replay(rows,'2025-09-01',max(x['d2'] for x in rows))
 out.append(dict(方案=name,笔数=len(rows),账户收益率=ret,最大回撤=dd,收益元=ret*1e6))
pd.DataFrame(out).to_csv(O/'同起点账户敏感性.csv',index=False,encoding='utf-8-sig')
print(pd.DataFrame(out).to_string(index=False))
print('per1w alternative',float(a.loc[a.Q75_unchecked_alt_code.notna(),'Q75_unchecked_alt_net'].sum()*1e4))
