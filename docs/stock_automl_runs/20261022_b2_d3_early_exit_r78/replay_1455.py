from pathlib import Path
import json
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
P=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
s=pd.read_pickle(P/'joined_306.pkl').copy()
z=pd.read_csv(R/'306笔D2_1455事前信号.csv')
s=s.merge(z[['id','D2_1455在涨停','D2最终涨停']],on='id',validate='one_to_one')
d=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
s=s.merge(d[['trade_date','stk_code','open']].rename(columns={'trade_date':'d3_date','open':'d3_open'}),on=['d3_date','stk_code'],validate='one_to_one')
assert len(s)==306
calendar=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
fee=.001
def replay(frame,policy,exit_mode,start):
 cash=1e6;peak=cash;held={};events={};ledger=[];daily=[];skipped=[]
 for row in frame.to_dict('records'):
  hold=bool(row[policy]) if policy!='baseline' else False
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row,hold))
  sell_day=row['d3_date'] if hold else row['d2_date']
  clock=('09:31' if exit_mode=='open' else '15:00') if hold else '15:00'
  events.setdefault(sell_day,[]).append((clock,1,row,hold))
 end=max(row['d3_date'] if (policy!='baseline' and bool(row[policy])) else row['d2_date'] for row in frame.to_dict('records'))
 for day in (d for d in calendar if start<=d<=end):
  for clock,kind,row,hold in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not held else 1.)
    if cost<=1e-6:
     skipped.append(row['id']);continue
    shares=cost/(row['entry']*(1+fee));cash-=cost
    held[row['id']]=(shares,cost,row)
   else:
    if row['id'] not in held:continue
    shares,cost,_=held.pop(row['id'])
    price=row['d3_open'] if hold and exit_mode=='open' else row['d3_close'] if hold else row['d2_close']
    proceeds=shares*price*(1-fee);cash+=proceeds
    ledger.append(dict(id=row['id'],D0=row['d0_date'],D1=row['d1_date'],D2=row['d2_date'],D3=row['d3_date'],held_to_D3=hold,buy_cost=cost,sell_proceeds_assumed=proceeds,pnl=proceeds-cost,return_after_fee=proceeds/cost-1,sell_day_assumed=day,sell_time_assumed=clock,sell_price_assumed=price))
  mark=0.
  for shares,cost,row in held.values():
   price=row['d1_close'] if day==row['d1_date'] else row['d2_close'] if day==row['d2_date'] else row['d3_close']
   mark+=shares*price
  nav=cash+mark;peak=max(peak,nav)
  daily.append(dict(date=day,nav=nav,cash=cash,drawdown=1-nav/peak,open_positions=len(held)))
 assert not held
 l=pd.DataFrame(ledger);v=pd.DataFrame(daily)
 assert abs(cash-(1e6+l.pnl.sum()))<1e-6
 return dict(账户收益率=cash/1e6-1,最大回撤=v.drawdown.max(),期末元=cash,结清日=end,延持笔数=int(l.held_to_D3.sum()),现金不足跳过买入=len(skipped),实际买入笔数=len(l)),l,v
base,l,v=replay(s,'baseline','open','2022-01-01')
prior=pd.read_csv(P/'全期_close_baseline_daily.csv')
assert len(v)==len(prior) and np.allclose(v.nav,prior.nav,atol=1e-6,rtol=0)
rows=[dict(范围='全期',规则='原V2 D2收盘',**base)]
for policy,name in [('D2_1455在涨停','14:55已涨停'),('D2最终涨停','最终收盘涨停_后验')]:
 for mode in ['open','close']:
  label=f'{name}_D3{mode}'
  z,l,v=replay(s,policy,mode,'2022-01-01')
  rows.append(dict(范围='全期',规则=label,**z))
  l.to_csv(R/f'全期_{label}_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
  v.to_csv(R/f'全期_{label}_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
  for year,g in s.groupby('d0_year'):
   y,ly,vy=replay(g,policy,mode,year+'-01-01')
   rows.append(dict(范围=year,规则=label,**y))
   ly.to_csv(R/f'{year}_{label}_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
   vy.to_csv(R/f'{year}_{label}_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
f=pd.DataFrame(rows)
f.to_csv(R/'D2_1455条件延持账户.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(f.to_string(index=False))
for p in ['D2_1455在涨停','D2最终涨停']:
 g=s[s[p]]
 print(p,'n',len(g),'d3open delta vs d2close mean pp',((g.d3_open-g.d2_close)/g.entry*.999/1.001*100).mean(),'d3close',((g.d3_close-g.d2_close)/g.entry*.999/1.001*100).mean())
