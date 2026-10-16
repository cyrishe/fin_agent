"""Frozen entries; target before 14:50 then last-ten-minute VWAP."""
from pathlib import Path
import pandas as pd
import numpy as np
import json,math
from scripts.replay_limit_rebound_cash_r23 import replay
P=Path('outputs/stock_automl/b2_d2_tail_vwap_20261016')
def main():
 raw=json.load(open(P/'raw.json'));market={};issues=[];receipt=[]
 compact=pd.read_pickle(P/'compact.pkl')
 daily=pd.read_pickle('outputs/stock_automl/limit_rebound_shape_top3_r20_20261015/daily_source_subset.pkl').drop_duplicates(['symbol','trade_date']).set_index(['symbol','trade_date'])
 for r in raw:
  b=pd.DataFrame(r['bars']);key=(r['code'],r['day']);source='database_finalized'
  if len(b)!=240:
   z=compact[compact.symbol.eq(key[0])&compact.day.eq(key[1])]
   if len(z)==240:
    b=z.rename(columns={'time':'bar_end_time','open':'open_price','high':'high_price','low':'low_price','close':'latest_price','turnover':'amount'}).copy();b.volume=b.volume/100;source=z.source.iloc[0]
  if len(b)!=240:
   path=Path('outputs/stock_automl/limit_rebound_shape_top3_r20_20261015/minute_days')/(r['day']+'_raw.pkl')
   if path.exists():
    a=pd.read_pickle(path);a=a[a.symbol.eq(r['code'])]
    for src,g in a.groupby('source'):
     if len(g)==240:
      b=g.rename(columns={'time':'bar_end_time','open':'open_price','high':'high_price','low':'low_price','close':'latest_price','turnover':'amount'}).copy();b.volume=b.volume/100;source='archive:'+src;break
  if len(b)!=240:issues.append(dict(code=key[0],day=key[1],reason='分钟不足240',n=len(b)));continue
  for c in ['open_price','high_price','low_price','latest_price','volume','amount']:b[c]=pd.to_numeric(b[c])
  b=b.sort_values('bar_end_time');b['clock']=pd.to_datetime(b.bar_end_time).dt.strftime('%H:%M');q=daily.loc[key]
  assert abs(b.latest_price.iloc[-1]-q.close)<.011,key
  assert abs(b.high_price.max()-q.high)<.011,key
  assert abs(b.low_price.min()-q.low)<.011,key
  assert np.isclose(b.volume.sum()*100,q.volume,rtol=.001),key
  tail=b[b.clock.gt('14:50')&b.clock.le('15:00')];assert len(tail)==10,key
  assert tail.volume.sum()>0 and tail.amount.notna().all(),key
  vwap=tail.amount.sum()/(tail.volume.sum()*100)
  assert tail.low_price.min()-.011<=vwap<=tail.high_price.max()+.011,(key,vwap)
  pre=b[b.clock.le('14:50')].high_price.max()
  market[key]=dict(vwap=vwap,prehigh=pre,dayhigh=b.high_price.max(),tail_amount=tail.amount.sum(),tail_shares=tail.volume.sum()*100)
  receipt.append(dict(code=key[0],day=key[1],source=source,**market[key]))
 assert not issues,issues
 l=pd.read_pickle('outputs/stock_automl/b2_d2_takeprofit8_20261016/ledger.pkl');l=l[l.卖出.eq('收盘')]
 cal=json.load(open('outputs/stock_automl/b2_four_five_clock_20261016/inputs/calendar.json'));summ=[];monthly=[];details=[]
 for name,src in l.groupby('方案'):
  frames={m:src.copy() for m in ['收盘','全天8%否则收盘','仅尾盘均价','1450前8%否则尾盘均价','全天8%否则尾盘均价_事后对照']}
  for i,r in src.iterrows():
   if pd.isna(r.exit_date):continue
   z=market[(r.symbol,r.exit_date)];target=math.ceil((r.entry*1.08-1e-10)*100)/100;early=z['prehigh']>=target-1e-9;allhit=z['dayhigh']>=target-1e-9
   prices={'收盘':r.sell,'全天8%否则收盘':target if allhit else r.sell,'仅尾盘均价':z['vwap'],'1450前8%否则尾盘均价':target if early else z['vwap'],'全天8%否则尾盘均价_事后对照':target if allhit else z['vwap']}
   for mode,price in prices.items():frames[mode].loc[i,'d2_close']=price
   details.append(dict(方案=name,买入日=r.entry_date,卖出日=r.exit_date,代码=r.symbol,买价=r.entry,目标价=target,早触发=int(early),晚触发=int(allhit and not early),收盘价=r.sell,尾盘均价=z['vwap'],新卖价=prices['1450前8%否则尾盘均价'],**{k:v for k,v in z.items() if k.startswith('tail_')}))
  for mode,f in frames.items():
   s,m,*_=replay(f,cal,end='2026-10-14',fee_rate=.001);summ.append(dict(方案=name,卖出=mode,**s));monthly.extend([dict(方案=name,卖出=mode,**r) for r in m])
 pd.DataFrame(summ).to_csv(P/'summary.csv',index=False);pd.DataFrame(monthly).to_csv(P/'monthly.csv',index=False);pd.DataFrame(details).to_csv(P/'trades.csv',index=False);pd.DataFrame(receipt).to_csv(P/'minute_verification.csv',index=False)
 print(pd.DataFrame(summ)[['方案','卖出','total_return','max_drawdown','sharpe']].to_string(index=False));print(pd.DataFrame(details).groupby('方案')[['早触发','晚触发']].sum().to_string())
if __name__=='__main__':main()
