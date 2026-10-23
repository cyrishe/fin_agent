import sys,json,gzip,io,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_data_repair_r95_20261023');sys.path.insert(0,str(R));sys.path.insert(0,str(O))
from scripts.import_tushare_minutes_2024 import convert as oldconvert
from import_tushare_minutes_fixed import convert as fixedconvert
# Classify primary extrema contradictions without any nonzero/tolerance confusion.
f=pd.read_csv(R/'minute_extrema_audit.csv',dtype={'symbol':str});f['year']=f.d1_date.str[:4]
f['high_missing']=f.high_diff.lt(-.011);f['low_missing']=f.low_diff.gt(.011);f['outside_daily']=f.high_diff.gt(.011)|f.low_diff.lt(-.011)
f['extrema_inside_only']=f.open_close_match&f.volume_match&~f.outside_daily&(f.high_missing|f.low_missing)
a=f[f.extrema_inside_only];s=a.groupby('year').agg(total=('symbol','size'),high_missing=('high_missing','sum'),low_missing=('low_missing','sum'));s['both']=(a.high_missing&a.low_missing).groupby(a.year).sum();s.to_csv(O/'extrema_pattern_by_year.csv',encoding='utf-8-sig');print('PATTERN',s.to_string(),flush=True)
f.to_csv(O/'extrema_pattern_detail.csv',index=False,encoding='utf-8-sig')
# Re-extract every price-conflict candidate available in original archives; preserve raw bytes.
a=pd.read_pickle(O/'union_facts.pkl');d=pd.read_pickle(O/'union_demand.pkl');a=a[a.price_conflict].merge(d[['symbol','d1_date','d1_open','d1_high','d1_low','d1_close','d1_volume']],on=['symbol','d1_date'],validate='one_to_one')
rawout=O/'auction_conflict_raw';rawout.mkdir(exist_ok=True);out=O/'minute_overlay';out.mkdir(exist_ok=True)
def work(item):
 day,g=item;path=Path('/data/cyris/tushare_market_data')/day[:4]/f'{day.replace("-","")}.csv.gz';syms=set(g.symbol);lines={s:[] for s in syms};results=[]
 if path.exists():
  with gzip.open(path,'rb') as h:
   header=h.readline()
   for line in h:
    sym=line[:6].decode('ascii',errors='ignore')
    if sym in syms:lines[sym].append(line)
 for x in g.itertuples():
  rec=dict(symbol=x.symbol,date=day)
  if lines[x.symbol]:
   data=header+b''.join(lines[x.symbol]);(rawout/f'{day}_{x.symbol}.csv').write_bytes(data);b=pd.read_csv(io.BytesIO(data))
  else:
   p=O/'fresh_minutes'/f'{day}_{x.symbol}_1.json'
   if not p.exists():results.append(dict(**rec,error='no raw source'));continue
   q=json.loads(p.read_text());b=pd.DataFrame(q['rows'],columns=q['fields'])
   for c in ['open','high','low','close','vol','amount']:b[c]=pd.to_numeric(b[c])
  b=b.sort_values('trade_time');auction=b[b.trade_time.str.endswith('09:30:00')].iloc[0];rec.update(auction_volume=float(auction.vol),auction_open=float(auction.open),first_regular_open=float(b.iloc[1].open))
  for label,fn in [('old',oldconvert),('fixed',fixedconvert)]:
   q=fn(b,day).sort_values('time');obs={'open':q.open.iloc[0],'high':q.high.max(),'low':q.low.min(),'close':q.close.iloc[-1]}
   for k,v in obs.items():rec[label+'_'+k+'_diff']=float(v-getattr(x,'d1_'+k))
   rec[label+'_ohlc_ok']=all(abs(rec[label+'_'+k+'_diff'])<=.011 for k in obs)
  rec['volume_ok']=abs(float(q.volume.sum())-x.d1_volume)<=max(1,.001*x.d1_volume);rec['repaired']=not rec['old_ohlc_ok'] and rec['fixed_ohlc_ok'] and rec['volume_ok'] and q.volume.sum()>0
  if rec['repaired']:q.to_pickle(out/f'{day}_{x.symbol}.pkl')
  results.append(rec)
 return results
rows=[]
with ThreadPoolExecutor(max_workers=3) as pool:
 for z in pool.map(work,a.groupby('d1_date')):
  rows.extend(z);print('AUDITED',len(rows),flush=True)
f=pd.DataFrame(rows);f.to_csv(O/'auction_fix_audit.csv',index=False,encoding='utf-8-sig');print('FIX',f.groupby(f.date.str[:4])[['repaired','fixed_ohlc_ok']].sum().to_string(),flush=True)
