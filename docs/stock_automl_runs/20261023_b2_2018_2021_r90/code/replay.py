from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
from collections import defaultdict
import numpy as np,pandas as pd
from scripts import experiment_b2_full_2024_r34 as r34
from scripts.experiment_automl_limit_rebound_d0_open_r5 import cap_price
from scripts.experiment_b2_recent_loss_resume_r55 import prior_return,should_pause
from scripts.replay_limit_rebound_cash_r23 import replay,dump,sha
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
MINUTE=Path('/data/cyris/tushare_market_data')
FEE=.001;INITIAL=1_000_000.
FIRST='2017-05';LAST='2021-12'
MONTHS=[str(m) for m in pd.period_range(FIRST,LAST,freq='M')]
def minute_price():
 sig=pd.read_pickle(ROOT/'signals_account_ready.pkl').copy()
 cal=json.loads((ROOT/'calendar.json').read_text())
 index={d:i for i,d in enumerate(cal)}
 sig['d2_date']=sig.d1_date.map(lambda d:cal[index[d]+1])
 source={}
 for month in sorted(set(sig.d2_date.str[:7])):
  f=pd.read_csv(ROOT/'price_months'/f'price_{month}.csv.gz',dtype={'trade_date':str,'stk_code':str},usecols=['trade_date','stk_code','open','high','low','close','volume','preclose'])
  f['symbol']=f.stk_code.str[:6]
  source.update({(r.trade_date,r.symbol):r for r in f.itertuples(index=False)})
 selected=[];minute_sources=[];fail=[]
 r34.raw_minute_path=lambda day:MINUTE/day[:4]/f'{day.replace("-","")}.csv.gz'
 for i,(day,g) in enumerate(sig.groupby('d2_date'),1):
  path=r34.raw_minute_path(day)
  meta=json.loads(path.with_suffix('').with_suffix('.json').read_text())
  assert meta['sha256']==sha(path) and meta['trade_date']==day.replace('-',''),path
  bars=r34.tushare_bars(day,set(g.symbol))
  minute_sources.append(dict(day=day,path=str(path),sha256=meta['sha256'],bars=meta['bars']))
  for row in g.itertuples(index=False):
   key=(day,row.symbol);b=bars.get(row.symbol);daily=source.get(key)
   if daily is None:
    fail.append(dict(day=day,symbol=row.symbol,reason='D2日线缺失'));continue
   prices=None
   if b is None:
    quality='minute_missing'
    fail.append(dict(day=day,symbol=row.symbol,reason='分钟缺失；收盘回退'))
    extrema_proxy_gap_pct=np.nan;close_gap_pct=np.nan
    first=None;exitbar=None
   else:
    prices=[float(b.open.iloc[0]),float(b.high.max()),float(b.low.min()),float(b.close.iloc[-1])]
    expected=[daily.open,daily.high,daily.low,daily.close]
    core_ok=(np.isclose(prices[0],expected[0],atol=.011,rtol=0)
             and prices[1]<=expected[1]+.011 and prices[2]>=expected[2]-.011
             and np.isclose(b.volume.sum(),daily.volume,rtol=.001,atol=1))
    if not core_ok:
     fail.append(dict(day=day,symbol=row.symbol,reason='D2分钟核心量价冲突',minute=prices,daily=expected))
     continue
    extrema_proxy_gap_pct=max(0.,expected[1]-prices[1],prices[2]-expected[2])/float(daily.preclose)
    close_gap_pct=abs(prices[3]-float(daily.close))/float(daily.preclose)
    quality='close_mismatch' if not np.isclose(prices[3],daily.close,atol=.011,rtol=0) else ('extrema_proxy' if extrema_proxy_gap_pct>.011/float(daily.preclose) else 'exact')
    if quality=='close_mismatch':
     fail.append(dict(day=day,symbol=row.symbol,reason='分钟末价与日线收盘不同；晨盘路径可用',minute_close=prices[3],daily_close=float(daily.close),close_gap_pct=close_gap_pct))
    mask=b.iloc[:120].high.to_numpy()>float(row.d1_close)*1.07+1e-12
    first=int(np.flatnonzero(mask)[0]) if mask.any() else None
    later=b.iloc[first+1:] if first is not None else b.iloc[0:0]
    later=later[later.volume.gt(0)]
    exitbar=later.iloc[0] if len(later) else None
   sell=float(exitbar.open) if exitbar is not None else float(daily.close)
   selltime=pd.Timestamp(exitbar.time).strftime('%H:%M') if exitbar is not None else '15:00'
   rate=.20 if (row.symbol.startswith(('300','301')) and day>='2020-08-24') or row.symbol.startswith(('688','689')) else .10
   floor=cap_price(float(daily.preclose),-rate)
   flooropen=abs(float(daily.open)-floor)<=.005
   floorclose=abs(float(daily.close)-floor)<=.005
   oneprice=(bool(floorclose and np.allclose(b[['open','high','low','close']].to_numpy(),floor,atol=.005,rtol=0)) if b is not None else None)
   selected.append(dict(d0_date=row.d0_date,d1_date=row.d1_date,d2_date=day,symbol=row.symbol,d2_daily_high=float(daily.high),d2_daily_low=float(daily.low),d2_daily_close=float(daily.close),d2_extrema_proxy_gap_pct=extrema_proxy_gap_pct,d2_close_gap_pct=close_gap_pct,d2_quality=quality,d2_minute_available=b is not None,morning_trigger=first is not None,early_sell=exitbar is not None,trigger_bar_end=pd.Timestamp(b.time.iloc[first]).strftime('%H:%M') if first is not None else None,sell_time=selltime,sell_price=sell,floor=floor,d2_open_floor=flooropen,d2_close_floor=floorclose,d2_oneprice_floor=oneprice))
  if i%30==0 or i==sig.d2_date.nunique():print('D2 minute days',i,'/',sig.d2_date.nunique(),'fail',len(fail),flush=True)
 dump(ROOT/'d2_minute_sources.json',minute_sources)
 dump(ROOT/'d2_minute_failures.json',fail)
 assert not any(x['reason']=='D2日线缺失' or x['reason']=='D2分钟核心量价冲突' for x in fail),fail[:5]
 facts=pd.DataFrame(selected)
 assert not facts.duplicated(['d1_date']).any()
 facts.to_pickle(ROOT/'d2_exit_facts.pkl')
 sig=sig.merge(facts,on=['d0_date','d1_date','d2_date','symbol'],validate='one_to_one')
 sig['d2_close_original']=sig.d2_close
 assert np.allclose(sig.d2_close,sig.d2_daily_close,atol=.011,rtol=0)
 sig.to_pickle(ROOT/'signals_exit.pkl')
 print('price exit ready',len(sig),'early',int(sig.early_sell.sum()),'floor close',int(sig.d2_close_floor.sum()),'oneprice floor',int(sig.d2_oneprice_floor.sum()),flush=True)
def analyze():
 sig=pd.read_pickle(ROOT/'signals_exit.pkl').sort_values('d1_date').copy()
 cal=json.loads((ROOT/'calendar.json').read_text())
 assert set(sig.d0_date.str[:7]).issubset(set(MONTHS))
 end=max(sig.d2_date)
 start=FIRST+'-01'
 shadow,_,snav,sledger,rejected=replay(sig,cal,start=start,end=end,initial=INITIAL,fee_rate=FEE)
 assert not rejected and shadow['entries']==len(sig) and shadow['pending']==0,(rejected,shadow)
 snav=snav.set_index('date').sort_index()
 counts=sig.d0_date.str[:7].value_counts().to_dict()
 gate={}
 for i,month in enumerate(MONTHS):
  prev=MONTHS[i-1] if i else None
  n=counts.get(prev,0) if prev else None
  r5=prior_return(snav,prev,5) if prev else None
  r10=prior_return(snav,prev,10) if prev else None
  gate[month]=dict(previous_month=prev,previous_candidates=n,previous_r5=r5,previous_r10=r10,paused=bool(should_pause(n,r5,r10,'sum_negative')))
 sig['model_month']=sig.d0_date.str[:7]
 sig['paused']=sig.model_month.map(lambda m:gate[m]['paused'])
 allowed=sig[~sig.paused].copy()
 scenarios={}
 for name,data in [('shadow_close',sig),('V2_pause_close',allowed),('main_pause_7pct',allowed.assign(d2_close=allowed.sell_price))]:
  result,month_nav,nav,ledger,bad=replay(data,cal,start=start,end=end,initial=INITIAL,fee_rate=FEE)
  assert not bad and result['entries']==len(data) and result['pending']==0,(name,bad,result)
  nav.to_pickle(ROOT/f'{name}_nav.pkl');ledger.to_pickle(ROOT/f'{name}_ledger.pkl')
  scenarios[name]=result
  print(name,len(data),result['total_return'],result['closed_win_rate'],result['max_drawdown'],flush=True)
 monthly=[];yearly=[];calendar_monthly=[]
 for month in MONTHS:
  g=sig[sig.model_month.eq(month)];q=allowed[allowed.model_month.eq(month)]
  row=dict(month=month,**gate[month],shadow_candidates=len(g),main_candidates=len(q))
  for name,data in [('V2_pause_close',q),('main_pause_7pct',q.assign(d2_close=q.sell_price))]:
   st=month+'-01';ed=max(data.d2_date) if len(data) else str(pd.Period(month).end_time.date())
   result,_,_,ledger,bad=replay(data,cal,start=st,end=ed,initial=INITIAL,fee_rate=FEE)
   assert not bad
   row[name+'_trades']=result['entries'];row[name+'_month_model_return']=result['total_return'];row[name+'_win_rate']=result['closed_win_rate'];row[name+'_mean_trade_return']=result['closed_mean_return']
  monthly.append(row)
 pd.DataFrame(monthly).to_csv(ROOT/'monthly_model.csv',index=False,encoding='utf-8-sig')
 for name in ('shadow_close','V2_pause_close','main_pause_7pct'):
  nav=pd.read_pickle(ROOT/f'{name}_nav.pkl');ledger=pd.read_pickle(ROOT/f'{name}_ledger.pkl')
  for month,g in nav.groupby(nav.date.str[:7],sort=True):
   start_idx=g.index[0]
   opening=INITIAL if start_idx==0 else float(nav.loc[start_idx-1,'nav'])
   values=np.r_[opening,g.nav.to_numpy(float)]
   bought=ledger[ledger.entry_date.str[:7].eq(month)]
   closed=ledger[ledger.exit_date.fillna('').str[:7].eq(month)]
   calendar_monthly.append(dict(month=month,scenario=name,bought=len(bought),sold=len(closed),calendar_return=values[-1]/values[0]-1,calendar_max_drawdown=float(np.max(1-values/np.maximum.accumulate(values))),calendar_sold_win_rate=float(closed.return_.gt(0).mean()) if len(closed) else None))
  for year,g in nav.groupby(nav.date.str[:4],sort=True):
   if year=='2021':continue
   start_idx=g.index[0]
   opening=INITIAL if start_idx==0 else float(nav.loc[start_idx-1,'nav'])
   values=np.r_[opening,g.nav.to_numpy(float)]
   l=ledger[ledger.entry_date.str[:4].eq(year)]
   closed=ledger[ledger.exit_date.fillna('').str[:4].eq(year)]
   yearly.append(dict(year=year,scenario=name,trades=len(l),closed=len(closed),calendar_return=values[-1]/values[0]-1,calendar_max_drawdown=float(np.max(1-values/np.maximum.accumulate(values))),calendar_win_rate=float(closed.return_.gt(0).mean()) if len(closed) else None))
 pd.DataFrame(calendar_monthly).to_csv(ROOT/'monthly_calendar.csv',index=False,encoding='utf-8-sig')
 pd.DataFrame(yearly).to_csv(ROOT/'yearly_calendar.csv',index=False,encoding='utf-8-sig')
 sig['ticker']=sig.symbol+np.where(sig.symbol.str.startswith('6'),'.SH','.SZ')
 sig['close_trade_return']=(sig.d2_close_original*(1-FEE)+sig.d2_cash_dividend)/(sig.entry*(1+FEE))-1
 sig['early_trade_return']=(sig.sell_price*(1-FEE)+sig.d2_cash_dividend)/(sig.entry*(1+FEE))-1
 sig[['model_month','d0_date','d1_date','d2_date','ticker','d0_rank','score','rule_id','entry_time','entry','paused','d2_close_original','morning_trigger','early_sell','trigger_bar_end','sell_time','sell_price','close_trade_return','early_trade_return','d2_open_floor','d2_close_floor','d2_oneprice_floor','d2_quality','d2_minute_available','d2_extrema_proxy_gap_pct','d2_close_gap_pct','d2_cash_dividend']].to_csv(ROOT/'trade_decisions.csv',index=False,encoding='utf-8-sig')
 dump(ROOT/'overall.json',dict(scope=dict(first_model_month=FIRST,last_model_month=LAST,account_start=start,final_d2=end),scenarios=scenarios,gate=gate,production_changed=False))
 print('monthly',len(monthly),'paused',sum(x['paused'] for x in monthly),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['minute_price','analyze']);a=p.parse_args();globals()[a.stage]()
