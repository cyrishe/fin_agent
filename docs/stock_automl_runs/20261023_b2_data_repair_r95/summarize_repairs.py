import sys,json,hashlib,gzip,io
from pathlib import Path
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_data_repair_r95_20261023');sys.path.insert(0,str(R))
from scripts.import_tushare_minutes_2024 import convert
def dump(v,n):(O/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str))
# Correctly distinguish all nonzero source differences from differences above the original tolerance.
f=pd.read_pickle(R/'enriched.pkl');rows=[]
for r in json.loads((R/'rules_frozen.json').read_text()):
 if r['month']<'2018-01':continue
 for part in ['train','valid']:
  z=f[f.model_month.eq(r['month'])&f.part.eq(part)].merge(pd.DataFrame(r['selection_keys'][part]),on=['symbol','d0_date'],validate='one_to_one');diff=z.extrema_proxy_gap_pct.fillna(0)*z.d1_preclose
  rows.append(dict(month=r['month'],part=part,used=len(z),any_gap=int(diff.gt(1e-8).sum()),over_tolerance=int(diff.gt(.011).sum())))
tr=pd.DataFrame(rows);tr.to_csv(O/'training_exposure_corrected.csv',index=False,encoding='utf-8-sig');print(tr.groupby('part')[['used','any_gap','over_tolerance']].sum().to_string(),flush=True)
# Show why old archive stocks were unavailable, without changing the frozen archives.
rec=pd.read_csv(O/'missing_recovery.csv',dtype={'symbol':str});archive=[]
for x in rec.itertuples():
 path=Path('/data/cyris/tushare_market_data')/x.date[:4]/f'{x.date.replace("-","")}.csv.gz';row=dict(symbol=x.symbol,date=x.date,archive_exists=path.exists())
 if path.exists():
  lines=[]
  with gzip.open(path,'rb') as h:
   header=h.readline()
   for line in h:
    if line[:6].decode('ascii',errors='ignore')==x.symbol:lines.append(line)
  row['archive_raw_rows']=len(lines)
  if lines:
   a=pd.read_csv(io.BytesIO(header+b''.join(lines)))
   try:convert(a,x.date);row['archive_conversion']='passed'
   except Exception as e:row['archive_conversion']=str(e)
  else:row['archive_conversion']='symbol_not_returned'
 archive.append(row)
pd.DataFrame(archive).to_csv(O/'recovery_archive_causes.csv',index=False,encoding='utf-8-sig')
# Small-sample newly fetched data comparisons and preserved hashes.
ret=pd.read_csv(O/'minute_retries.csv',dtype={'symbol':str});one=ret[ret.attempt.eq(1)]
daily=[]
d=pd.read_pickle(O/'union_demand.pkl')
for p in (O/'fresh_minutes').glob('*_daily.json'):
 date,symbol=p.name.split('_')[:2];a=json.loads(p.read_text());g=pd.DataFrame(a['rows'],columns=a['fields']);ref=d[d.symbol.eq(symbol)&d.d1_date.eq(date)]
 row=dict(symbol=symbol,date=date,rows=len(g))
 if len(g) and len(ref):
  for k in ['open','high','low','close']:row[k+'_diff']=float(g.iloc[0][k])-float(ref.iloc[0]['d1_'+k])
 daily.append(row)
pd.DataFrame(daily).to_csv(O/'fresh_daily_comparison.csv',index=False,encoding='utf-8-sig')
manifest=dict(strategy='imac/B2_V2',definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',production_changed=False,models_retrained=False,original_archive_changed=False,overlay_usage='no positive-volume recoveries; 32 zero-volume sequences quarantined, not usable for execution',endpoint='https://api.quicksync.cn (existing configured Tushare-compatible service)',unique_recovered=int(rec.recovered.sum()),new_source_hashes={str(p.relative_to(O)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['fresh_minutes','recovery_raw','minute_overlay','zero_volume_placeholder','akshare_daily'] for p in (O/folder).glob('*')},script_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in O.glob('*.py')},registration='未提交生产；云盘待同步')
dump(manifest,'manifest.json');print('DONE',flush=True)
