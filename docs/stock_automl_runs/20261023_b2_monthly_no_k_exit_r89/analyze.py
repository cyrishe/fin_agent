from pathlib import Path
from datetime import timedelta
import json,hashlib
import pandas as pd
O=Path('/data/cyris/work/b2_monthly_no_k_exit_r89_20261023')
R=Path('/data/cyris/work/b2_no_k_r88_20261023')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
t=pd.read_csv(R/'逐笔三方案.csv',dtype={'code':str})
d=pd.read_csv(O/'D2日线与分钟需求.csv',dtype={'code':str})
d=d.set_index(['d2','code'])
assert len(d)==214
def sell7(day,code,preclose,close):
 q=d.loc[(day,code)]
 assert abs(float(q.preclose)-preclose)<.011 and abs(float(q.close)-close)<.011
 if not bool(q.needs_minutes):return close,'15:00',0,None,None
 b=pd.read_pickle(O/'minutes'/f'{day}_{code}.pkl').sort_values('time')
 trigger=None
 for i in range(120):
  if float(b.high.iloc[i])/preclose-1>.07+1e-12:
   trigger=i;break
 if trigger is None:return close,'15:00',0,None,None
 for j in range(trigger+1,len(b)):
  row=b.iloc[j]
  if row.time.strftime('%H:%M')>'14:57':break
  if row.volume>0:
   at=(row.time-timedelta(minutes=1)).strftime('%H:%M')
   return float(row.open),at,1,b.time.iloc[trigger].strftime('%H:%M'),str(q.stk_code)
 return close,'15:00',0,b.time.iloc[trigger].strftime('%H:%M'),str(q.stk_code)
items=[]
for r in t.itertuples(index=False):
 sell,when,hit,trigger,_=sell7(r.d2,r.code,float(r.d1close),float(r.exit))
 items.append(dict(方案=r.方案,D0=r.d0,月份=r.d0[:7],D1=r.d1,D2=r.d2,股票=r.code,排名=r.rank,买价=r.entry,D1收盘=r.d1close,D2收盘=r.exit,D2最高=float(d.loc[(r.d2,r.code)].high),D2最高超过7=int(bool(d.loc[(r.d2,r.code)].needs_minutes)),上午7触发成交=hit,触发分钟=trigger,早卖时间=when,早卖价=sell,纯收盘费后收益=r.exit/r.entry*.999/1.001-1,早卖费后收益=sell/r.entry*.999/1.001-1,早卖多得百分点=(sell-r.exit)/r.entry*.999/1.001*100,买时=r.time,来源=r.origin))
a=pd.DataFrame(items)
a.to_csv(O/'逐笔_两种卖法.csv',index=False,encoding='utf-8-sig')
def replay(frame,exit_mode):
 cash=1e6;hold={};peak=cash;dd=0.;events={}
 for r in frame.itertuples(index=False):
  rid=(r.股票,r.D0)
  events.setdefault(r.D1,[]).append((r.买时,0,rid,r))
  clock='15:00' if exit_mode=='纯收盘' else r.早卖时间
  events.setdefault(r.D2,[]).append((clock,1,rid,r))
 start=frame.D0.min()[:7]+'-01';end=frame.D2.max()
 for day in [c for c in cal if start<=c<=end]:
  for clock,kind,key,r in sorted(events.get(day,[]),key=lambda z:(z[0],z[1])):
   if kind==0:
    invest=cash*(.5 if not hold else 1.)
    shares=invest/(r.买价*1.001)
    cash-=invest;hold[key]=(shares,r.D1收盘)
   else:
    shares,_=hold.pop(key)
    price=r.D2收盘 if exit_mode=='纯收盘' else r.早卖价
    cash+=shares*price*.999
  nav=cash+sum(sh*mark for sh,mark in hold.values())
  peak=max(peak,nav);dd=max(dd,1-nav/peak)
 assert not hold
 return cash/1e6-1,dd
monthly=[];summary=[]
for name,g in a.groupby('方案',sort=False):
 for mode,col in [('纯收盘','纯收盘费后收益'),('7%＋收盘','早卖费后收益')]:
  for month,h in g.groupby('月份'):
   ret,dd=replay(h,mode)
   monthly.append(dict(月份=month,方案=name,卖法=mode,笔数=len(h),胜笔=int(h[col].gt(0).sum()),胜率=float(h[col].gt(0).mean()),费后笔均=float(h[col].mean()),独立月账户收益=ret,独立月最大回撤=dd,上午7成交笔数=int(h.上午7触发成交.sum()),D2最高超过7笔数=int(h.D2最高超过7.sum())))
  for period,start,end in [('全段','2025-09','2026-09'),('2025','2025-09','2025-12'),('2026','2026-01','2026-09')]:
   h=g[g.月份.between(start,end)]
   ret,dd=replay(h,mode)
   summary.append(dict(期间=period,方案=name,卖法=mode,笔数=len(h),胜笔=int(h[col].gt(0).sum()),胜率=float(h[col].gt(0).mean()),费后笔均=float(h[col].mean()),账户收益=ret,最大回撤=dd,上午7成交笔数=int(h.上午7触发成交.sum())))
m=pd.DataFrame(monthly).sort_values(['月份','方案','卖法'])
s=pd.DataFrame(summary)
m.to_csv(O/'逐月_三方案两卖法.csv',index=False,encoding='utf-8-sig')
s.to_csv(O/'综合_三方案两卖法.csv',index=False,encoding='utf-8-sig')
v2=s[(s.期间.eq('全段'))&(s.卖法.eq('纯收盘'))].set_index('方案')
reference=pd.read_csv(R/'同口径账户与跌停风险.csv').query("期间 == '全段'").set_index('方案')
for name in ['原策略','去形态保留实体','去四K筛选']:
 assert abs(v2.loc[name,'账户收益']-reference.loc[name,'账户收益率'])<1e-10
 assert abs(v2.loc[name,'最大回撤']-reference.loc[name,'最大回撤'])<1e-10
print('REPRODUCED_R88',[(n,float(v2.loc[n,'账户收益'])) for n in v2.index])
print('SUMMARY')
print(s.to_string(index=False))
print('MONTHLY')
print(m.to_string(index=False))
for mode in ['纯收盘','7%＋收盘']:
 b=m[(m.方案.eq('原策略'))&(m.卖法.eq(mode))].set_index('月份')
 n=m[(m.方案.eq('去四K筛选'))&(m.卖法.eq(mode))].set_index('月份')
 print('STABILITY',mode,'base win rate > noK',int((b.胜率>n.胜率).sum()),'equal',int((b.胜率==n.胜率).sum()),'months',len(b),'base account > noK',int((b.独立月账户收益>n.独立月账户收益).sum()),'base profitable months',int(b.独立月账户收益.gt(0).sum()),'noK profitable months',int(n.独立月账户收益.gt(0).sum()),'base win-rate range',float(b.胜率.min()),float(b.胜率.max()),'noK win-rate range',float(n.胜率.min()),float(n.胜率.max()))
manifest={'R88_trade_sha256':hashlib.sha256((R/'逐笔三方案.csv').read_bytes()).hexdigest(),'D2_daily_sha256':hashlib.sha256((O/'D2日线与分钟需求.csv').read_bytes()).hexdigest(),'minute_source_manifest_sha256':hashlib.sha256((O/'minute_sources.json').read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'R88_close_reproduced':True}
(O/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
