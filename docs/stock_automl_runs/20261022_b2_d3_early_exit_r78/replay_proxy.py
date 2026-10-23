from pathlib import Path
import json
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
P=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
s=pd.read_pickle(P/'joined_306.pkl')
signal=pd.read_csv(R/'306笔D2_1455事前信号.csv')[['id','D2_1455在涨停']]
s=s.merge(signal,on='id',validate='one_to_one')
d=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
s=s.merge(d[['trade_date','stk_code','open']].rename(columns={'trade_date':'d3_date','open':'d3_open'}),on=['d3_date','stk_code'],validate='one_to_one')
assert len(s)==306
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
minute=pd.read_csv(R/'84笔分钟预约卖出对照.csv')
minute=minute[minute.预约卖出时点=='09:31'].set_index('id')
fp=pd.read_csv(R/'2笔1455误报D3分钟核验.csv').set_index('id')
follow=pd.read_csv(R/'4笔跌停后续官方日线.csv')
later={}
for id,stock in [('603527.SH|2024-11-21','603527.SH'),('601212.SH|2026-01-27','601212.SH')]:
 z=follow[follow.stk_code==stock].sort_values('trade_date')
 d0=s.set_index('id').loc[id,'d3_date']
 unlock=z[(z.trade_date>d0)&z.is_limit_price.ne(2)&z.volume.gt(0)].iloc[0]
 later[id]=(unlock.trade_date,float(unlock.close))
print('locked later exits',later)
def clock_start(bar_end):
 if bar_end=='15:00':return '15:00'
 t=pd.Timestamp('2000-01-01 '+bar_end)-pd.Timedelta(minutes=1)
 return t.strftime('%H:%M')
exitplan={}
for x in s.itertuples():
 if not x.D2_1455在涨停:
  exitplan[x.id]=(x.d2_date,'15:00',float(x.d2_close),'D2_close_baseline',True)
 elif x.id in minute.index:
  q=minute.loc[x.id]
  if bool(q.代理成交):
   exitplan[x.id]=(x.d3_date,clock_start(q.实际代理成交时点),float(q.卖出价或未售收盘估值),'D3_first_eligible_minute',True)
  else:
   day,price=later[x.id]
   exitplan[x.id]=(day,'15:00',price,'later_first_nonfloor_close_proxy',True)
 elif x.id in fp.index:
  q=fp.loc[x.id]
  assert bool(q.proxy_fill)
  exitplan[x.id]=(x.d3_date,clock_start(q.first_eligible_clock),float(q.first_eligible_price),'D3_first_eligible_minute',True)
 else:
  # Seven late 2026 cases have daily open but no archived minute path.
  exitplan[x.id]=(x.d3_date,'09:30',float(x.d3_open),'D3_official_open_unverified_minute',False)
assert len(exitplan)==306
def replay(frame,start):
 cash=1e6;peak=cash;hold={};events={};ledger=[];dayrows=[];skipped=[]
 for row in frame.to_dict('records'):
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
  day,clock,price,kind,verified=exitplan[row['id']]
  events.setdefault(day,[]).append((clock,1,row))
 end=max(exitplan[row['id']][0] for row in frame.to_dict('records'))
 marks=follow.set_index(['stk_code','trade_date'])['close']
 for day in (d for d in cal if start<=d<=end):
  for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not hold else 1.)
    if cost<=1e-6:skipped.append(row['id']);continue
    shares=cost/(row['entry']*1.001);cash-=cost;hold[row['id']]=(shares,cost,row)
   else:
    if row['id'] not in hold:continue
    shares,cost,_=hold.pop(row['id'])
    sell_day,sell_clock,price,reason,verified=exitplan[row['id']]
    proceeds=shares*price*.999;cash+=proceeds
    ledger.append(dict(id=row['id'],D0=row['d0_date'],D2=row['d2_date'],D3=row['d3_date'],sell_day=sell_day,sell_clock_proxy=sell_clock,sell_price_proxy=price,sell_proxy_kind=reason,minute_verified=verified,buy_cost=cost,sell_proceeds_assumed=proceeds,pnl=proceeds-cost))
  marked=0
  for shares,cost,row in hold.values():
   if day==row['d1_date']:price=row['d1_close']
   elif day==row['d2_date']:price=row['d2_close']
   elif day==row['d3_date']:price=row['d3_close']
   else:
    price=float(marks.loc[(row['stk_code'],day)])
   marked+=shares*price
  nav=cash+marked;peak=max(peak,nav)
  dayrows.append(dict(date=day,nav=nav,cash=cash,drawdown=1-nav/peak,open_positions=len(hold)))
 assert not hold
 l=pd.DataFrame(ledger);v=pd.DataFrame(dayrows)
 assert abs(cash-(1e6+l.pnl.sum()))<1e-6
 return dict(账户收益率=cash/1e6-1,最大回撤=v.drawdown.max(),期末元=cash,实际买入笔数=len(l),现金不足跳过买入=len(skipped),缺失分钟开盘代理笔数=int((l.sell_proxy_kind=='D3_official_open_unverified_minute').sum()),延期到更后一天笔数=int((l.sell_proxy_kind=='later_first_nonfloor_close_proxy').sum())),l,v
out=[]
for label,frame in [('全期',s)]+[(str(year),g) for year,g in s.groupby('d0_year')]:
 start='2022-01-01' if label=='全期' else label+'-01-01'
 z,l,v=replay(frame,start)
 out.append(dict(范围=label,**z))
 l.to_csv(R/f'{label}_1455持有_分钟成交代理_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
 v.to_csv(R/f'{label}_1455持有_分钟成交代理_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(out).to_csv(R/'1455持有_分钟与解封退出账户.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(out).to_string(index=False))
