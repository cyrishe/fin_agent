import sys,json,hashlib
from pathlib import Path
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_data_repair_r95_20261023');sys.path.insert(0,str(R));sys.path.insert(0,'/data/cyris/tushare_backfill')
from backfill_tushare_minutes import Tushare,token_from_file,SlidingRateLimiter
from scripts import experiment_b2_full_2024_r34 as r34
r34.raw_minute_path=lambda day:Path('/data/cyris/tushare_market_data')/day[:4]/f'{day.replace("-","")}.csv.gz'
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60))
rawdir=O/'fresh_minutes';rawdir.mkdir(exist_ok=True)
audit=pd.read_csv(R/'minute_extrema_audit.csv',dtype={'symbol':str,'d1_date':str});demand=pd.read_pickle(R/'minute_demand.pkl');facts=pd.read_pickle(R/'minute_facts.pkl')
samples=[]
for year in ['2019','2020','2021']:
 g=audit[audit.d1_date.str.startswith(year)&audit.open_close_match.eq(True)&audit.volume_match.eq(True)].sort_values('max_extreme_gap_pct',ascending=False)
 samples.extend(g.head(2).assign(kind='extreme_top').to_dict('records'));samples.extend(g.iloc[len(g)//2:len(g)//2+1].assign(kind='extreme_middle').to_dict('records'))
samples.extend(facts[facts.reason.eq('分钟缺失')].head(3).assign(kind='missing').to_dict('records'))
samples.extend(facts[facts.reason.eq('分钟日线开收价格冲突')].head(2).assign(kind='open_close_conflict').to_dict('records'))
samples.extend(facts[facts.d1_date.str.startswith('2018')&facts.audit_ok].head(1).assign(kind='control').to_dict('records'))
later=pd.read_pickle(O/'union_facts.pkl');later_demand=pd.read_pickle(O/'union_demand.pkl')
demand=pd.concat([demand,later_demand],ignore_index=True).drop_duplicates(['symbol','d1_date'],keep='last')
samples.extend(later[later.year.ge('2022')&later.price_conflict].assign(kind='later_price_conflict').to_dict('records'))
for year in ['2022','2023','2024','2025','2026']:
 samples.extend(later[later.year.eq(year)&later.passed].head(1).assign(kind='later_control').to_dict('records'))
results=[]
for case in samples:
 sym,day=case['symbol'],case['d1_date'];code=sym+('.SH' if sym.startswith('6') else '.BJ' if sym.startswith(('4','8','9')) else '.SZ');ref=demand[demand.symbol.eq(sym)&demand.d1_date.eq(day)].iloc[0]
 old=r34.tushare_bars(day,{sym}).get(sym)
 for attempt in [1,2]:
  pars=dict(ts_code=code,freq='1min',start_date=day+' 09:30:00',end_date=day+' 15:00:00',limit=8000)
  path=rawdir/f'{day}_{sym}_{attempt}.json'
  if path.exists() and json.loads(path.read_text())['params']==pars: obj=json.loads(path.read_text());fields,rows=obj['fields'],obj['rows']
  else:
   fields,rows=api.query('stk_mins',pars);path.write_text(json.dumps(dict(api='stk_mins',params=pars,fields=fields,rows=rows),ensure_ascii=False))
  f=pd.DataFrame(rows,columns=fields)
  for c in ['open','high','low','close','vol','amount']:
   if c in f:f[c]=pd.to_numeric(f[c],errors='raise')
  rec=dict(symbol=sym,date=day,kind=case['kind'],attempt=attempt,raw_rows=len(f),old_rows=0 if old is None else len(old),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
  if len(f):
   rec['raw_high']=float(f.high.max());rec['raw_low']=float(f.low.min());rec['raw_ohlc_equal_fraction']=float((f.open.eq(f.close)&f.high.eq(f.low)).mean())
   try:
    b=r34.convert(f,day).sort_values('time').reset_index(drop=True);rec['rows']=len(b)
    rec['high_diff']=float(b.high.max()-ref.d1_high);rec['low_diff']=float(b.low.min()-ref.d1_low);rec['open_diff']=float(b.open.iloc[0]-ref.d1_open);rec['close_diff']=float(b.close.iloc[-1]-ref.d1_close)
    rec['ohlc_match']=all(abs(rec[c])<=.011 for c in ['high_diff','low_diff','open_diff','close_diff'])
    rec['same_as_archive']=old is not None and len(b)==len(old) and np.allclose(b[['open','high','low','close','volume']].to_numpy(float),old[['open','high','low','close','volume']].to_numpy(float),rtol=0,atol=1e-6)
   except Exception as e:rec['conversion_error']=str(e)
  results.append(rec);pd.DataFrame(results).to_csv(O/'minute_retries.csv',index=False,encoding='utf-8-sig');print(json.dumps(rec,ensure_ascii=False),flush=True)
 # Independent current daily from same API, to distinguish source daily conflicts.
 path=rawdir/f'{day}_{sym}_daily.json'
 if not path.exists() or sym.startswith(('4','8','9')):
  fields,rows=api.query('daily',dict(ts_code=code,trade_date=day.replace('-','')));path.write_text(json.dumps(dict(fields=fields,rows=rows),ensure_ascii=False))
print('COMPLETE',len(results),flush=True)
