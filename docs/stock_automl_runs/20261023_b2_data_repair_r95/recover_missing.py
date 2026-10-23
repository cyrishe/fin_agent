import sys,json,hashlib
from pathlib import Path
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_data_repair_r95_20261023');sys.path.insert(0,str(R));sys.path.insert(0,'/data/cyris/tushare_backfill')
from backfill_tushare_minutes import Tushare,token_from_file,SlidingRateLimiter
from scripts.import_tushare_minutes_2024 import convert
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60))
raw=O/'recovery_raw';raw.mkdir(exist_ok=True);overlay=O/'minute_overlay';overlay.mkdir(exist_ok=True)
f=pd.read_pickle(O/'union_facts.pkl');d=pd.read_pickle(O/'union_demand.pkl');jobs=[]
for x in f[f.missing].itertuples():
 ref=d[d.symbol.eq(x.symbol)&d.d1_date.eq(x.d1_date)].iloc[0]
 jobs.append(dict(symbol=x.symbol,date=x.d1_date,role='D1',**{c:ref['d1_'+c] for c in ['open','high','low','close','volume']}))
s=pd.read_pickle(R/'signals_exit.pkl')
for x in s[~s.d2_minute_available].itertuples():
 daily=pd.read_csv(R/'price_months'/f'price_{x.d2_date[:7]}.csv.gz',dtype={'stk_code':str,'trade_date':str});ref=daily[daily.trade_date.eq(x.d2_date)&daily.stk_code.str.startswith(x.symbol)].iloc[0]
 jobs.append(dict(symbol=x.symbol,date=x.d2_date,role='D2',**{c:ref[c] for c in ['open','high','low','close','volume']}))
j=pd.DataFrame(jobs).drop_duplicates(['symbol','date']);results=[]
for x in j.to_dict('records'):
 sym,day=x['symbol'],x['date'];path=raw/f'{day}_{sym}.json';pars=dict(ts_code=sym+('.SH' if sym.startswith('6') else '.SZ'),freq='1min',start_date=day+' 09:30:00',end_date=day+' 15:00:00',limit=8000)
 if path.exists():a=json.loads(path.read_text())
 else:
  try:
   fields,rows=api.query('stk_mins',pars);a=dict(fields=fields,rows=rows,params=pars);path.write_text(json.dumps(a,ensure_ascii=False))
  except Exception as e:a={'error':str(e),'fields':[],'rows':[]}
 b=pd.DataFrame(a['rows'],columns=a['fields']);rec=dict(symbol=sym,date=day,role=x['role'],raw_rows=len(b),recovered=False)
 if len(b):
  for c in ['open','high','low','close','vol','amount']:b[c]=pd.to_numeric(b[c],errors='raise')
  try:
   q=convert(b,day).sort_values('time').reset_index(drop=True);observed=dict(open=float(q.open.iloc[0]),high=float(q.high.max()),low=float(q.low.min()),close=float(q.close.iloc[-1]),volume=float(q.volume.sum()))
   rec.update({c+'_diff':observed[c]-x[c] for c in observed})
   rec['ohlc_ok']=all(abs(rec[c+'_diff'])<=.011 for c in ['open','high','low','close']);rec['volume_ok']=abs(rec['volume_diff'])<=max(1,.001*x['volume']);rec['source_rows_returned']=True;rec['volume']=observed['volume'];rec['positive_bars']=int(q.volume.gt(0).sum());rec['recovered']=bool(rec['ohlc_ok'] and rec['volume_ok'] and observed['volume']>0)
   if observed['volume']==0:
    qdir=O/'zero_volume_placeholder';qdir.mkdir(exist_ok=True);q.to_pickle(qdir/f'{day}_{sym}.pkl');rec['note']='全天零成交占位；不可用于成交，也不算补回交易分钟'
   if rec['recovered']:
    q['symbol']=sym;dest=overlay/f'{day}_{sym}.pkl';q.to_pickle(dest);rec['overlay_sha256']=hashlib.sha256(dest.read_bytes()).hexdigest()
  except Exception as e:rec['error']=str(e)
 results.append(rec);pd.DataFrame(results).to_csv(O/'missing_recovery.csv',index=False,encoding='utf-8-sig');print(json.dumps(rec,ensure_ascii=False),flush=True)
print('COMPLETE',len(results),sum(x['recovered'] for x in results),flush=True)
