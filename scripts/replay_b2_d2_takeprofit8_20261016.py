"""D2 8% gross target vs close, frozen entries and overnight cash rotation."""
from pathlib import Path
import json,math
import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import replay
OUT=Path('outputs/stock_automl/b2_d2_takeprofit8_20261016')
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 old=pd.read_pickle('outputs/stock_automl/limit_rebound_four_minutes_r24_20261015/ledger.pkl')
 newer=pd.read_pickle('outputs/stock_automl/b2_next_minute_mean_20261016/ledger.pkl')
 frames={'B2':old[old.policy.eq('four_fit_delay')],'四K均价':newer[newer.variant.eq('4K_immediate_next_mean_end0941')],'五K均价':newer[newer.variant.eq('5K_immediate_next_mean_end0941')]}
 daily=pd.read_pickle('outputs/stock_automl/limit_rebound_shape_top3_r20_20261015/daily_source_subset.pkl').drop_duplicates(['symbol','trade_date']).set_index(['symbol','trade_date'])
 cal=json.load(open('outputs/stock_automl/b2_four_five_clock_20261016/inputs/calendar.json'));ss=[];mm=[];trades=[];ledgers=[]
 for name,src in frames.items():
  f=src.copy();assert len(f)==f.d0_date.nunique()
  for i,r in f.iterrows():
   if pd.isna(r.exit_date):continue
   key=(r.symbol,r.exit_date);assert key in daily.index,key
   q=daily.loc[key];assert abs(q.close-r.sell)<1e-8,(key,q.close,r.sell)
   target=math.ceil((r.entry*1.08-1e-10)*100)/100
   hit=q.high>=target-1e-9
   price=max(float(q.open),target) if hit else float(q.close)
   f.loc[i,'d2_close']=price
   trades.append(dict(方案=name,买入日=r.entry_date,卖出日=r.exit_date,代码=r.symbol,买价=r.entry,目标价=target,D2开盘=q.open,D2最高=q.high,D2收盘=q.close,触发=int(hit),止盈卖价=price,收盘净收益=r.sell/r.entry*.999/1.001-1,止盈净收益=price/r.entry*.999/1.001-1,收益差=(price-r.sell)/r.entry*.999/1.001))
  for mode,x in [('收盘',src),('止盈8%',f)]:
   s,m,d,l,e=replay(x,cal,end='2026-10-14',fee_rate=.001)
   assert not e
   if mode=='收盘':
    refs={'B2':2.1659492677691263,'四K均价':None,'五K均价':None}
    if name=='B2': assert abs(s['final_nav']/1e6-refs[name])<1e-10
    else:
     prior=pd.read_csv('outputs/stock_automl/b2_next_minute_mean_20261016/summary.csv'); key=('4' if name.startswith('四') else '5')+'K_immediate_next_mean_end0941'
     assert abs(s['final_nav']-float(prior[prior.variant.eq(key)].final_nav.iloc[0]))<1e-7
   ss.append(dict(方案=name,卖出=mode,**s));mm.extend([dict(方案=name,卖出=mode,**r) for r in m]);ledgers.append(l.assign(方案=name,卖出=mode))
 pd.DataFrame(ss).to_csv(OUT/'summary.csv',index=False);pd.DataFrame(mm).to_csv(OUT/'monthly.csv',index=False);pd.DataFrame(trades).to_csv(OUT/'trade_comparison.csv',index=False);pd.concat(ledgers).to_pickle(OUT/'ledger.pkl')
 print(pd.DataFrame(ss)[['方案','卖出','entries','closed','total_return','max_drawdown','sharpe']].to_string(index=False))
 for name,g in pd.DataFrame(trades).groupby('方案'):
  h=g[g.触发.eq(1)];print(name,'trigger',len(h),'improved',h.收益差.gt(1e-10).sum(),'worse',h.收益差.lt(-1e-10).sum(),'equal',h.收益差.abs().le(1e-10).sum())
 print(pd.DataFrame(mm)[['方案','卖出','month','return_','drawdown']].to_string(index=False))
if __name__=='__main__':main()
