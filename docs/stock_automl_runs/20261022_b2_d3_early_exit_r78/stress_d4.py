from pathlib import Path
import json,numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
P=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
s=pd.read_pickle(P/'joined_306.pkl').copy()
z=pd.read_csv(R/'306笔D2_1455事前信号.csv')
s=s.merge(z[['id','D2_1455在涨停']],on='id',validate='one_to_one')
daily=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
s=s.merge(daily[['trade_date','stk_code','open']].rename(columns={'trade_date':'d3_date','open':'d3_open'}),on=['d3_date','stk_code'],validate='one_to_one')
a=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/all_signals.pkl')
a=a[a.v2_allowed].copy();a['id']=a.stk_code+'|'+a.d0_date
s=s.merge(a[['id','d4_close']],on='id',validate='one_to_one')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
idx={day:i for i,day in enumerate(cal)}
s['d4_date']=[cal[idx[d]+1] for d in s.d3_date]
floor4={'000605.SZ|2024-11-20','603527.SH|2024-11-21','002490.SZ|2025-06-19','601212.SH|2026-01-27'}
assert s.set_index('id').loc[list(floor4),'D2_1455在涨停'].all()
fee=.001
def replay(frame,stress,start):
 cash=1e6;peak=cash;hold={};events={};ledger=[];daily=[];skipped=[]
 for row in frame.to_dict('records'):
  flag=bool(row['D2_1455在涨停'])
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
  sell_day=row['d4_date'] if stress and row['id'] in floor4 else row['d3_date'] if flag else row['d2_date']
  sell_clock='15:00' if stress and row['id'] in floor4 else '09:31' if flag else '15:00'
  events.setdefault(sell_day,[]).append((sell_clock,1,row))
 end=max(row['d4_date'] if stress and row['id'] in floor4 else row['d3_date'] if row['D2_1455在涨停'] else row['d2_date'] for row in frame.to_dict('records'))
 for day in (d for d in cal if start<=d<=end):
  for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not hold else 1.)
    if cost<=1e-6:skipped.append(row['id']);continue
    shares=cost/(row['entry']*(1+fee));cash-=cost
    hold[row['id']]=(shares,cost,row)
   else:
    if row['id'] not in hold:continue
    shares,cost,_=hold.pop(row['id'])
    stress_lot=bool(stress and row['id'] in floor4)
    price=row['d4_close'] if stress_lot else row['d3_open'] if row['D2_1455在涨停'] else row['d2_close']
    proceeds=shares*price*(1-fee);cash+=proceeds
    ledger.append(dict(id=row['id'],D0=row['d0_date'],D2=row['d2_date'],D3=row['d3_date'],D4=row['d4_date'],stress_lot=stress_lot,buy_cost=cost,sell_assumed=proceeds,pnl=proceeds-cost,sell_day=day,sell_price=price))
  marked=0
  for shares,cost,row in hold.values():
   price=row['d1_close'] if day==row['d1_date'] else row['d2_close'] if day==row['d2_date'] else row['d3_close'] if day==row['d3_date'] else row['d4_close']
   marked+=shares*price
  nav=cash+marked;peak=max(peak,nav)
  daily.append(dict(date=day,nav=nav,cash=cash,drawdown=1-nav/peak,open_positions=len(hold)))
 assert not hold
 l=pd.DataFrame(ledger);v=pd.DataFrame(daily)
 assert abs(cash-(1e6+l.pnl.sum()))<1e-6
 return dict(return_rate=cash/1e6-1,max_drawdown=v.drawdown.max(),ending_cash=cash,skipped_cash_buys=len(skipped),sold_count=len(l)),l,v
out=[]
for name,stress in [('D3开盘全可售假设',False),('4笔跌停延至D4收盘估值',True)]:
 z,l,v=replay(s,stress,'2022-01-01')
 out.append(dict(scenario=name,**z))
 l.to_csv(R/f'{name}_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
 v.to_csv(R/f'{name}_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(out).to_string(index=False))
pd.DataFrame(out).to_csv(R/'D3开盘跌停4笔D4估值压力.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
