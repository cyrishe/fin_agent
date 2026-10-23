"""Fixed chronological sample, independent daily OHLC, server-only."""
import json,sys,subprocess,time
from pathlib import Path
import pandas as pd,numpy as np
O=Path('/data/cyris/work/b2_data_repair_r95_20261023');R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');A=O/'akshare_year_sample';A.mkdir(exist_ok=True)
if len(sys.argv)>1:
 import requests,akshare as ak
 original=requests.sessions.Session.request
 def bounded(self,*args,**kwargs):
  if kwargs.get('timeout') is None:kwargs['timeout']=12
  return original(self,*args,**kwargs)
 requests.sessions.Session.request=bounded
 jobs=json.loads((O/'akshare_year_sample_jobs.json').read_text());x=jobs[int(sys.argv[1])];s=x['symbol'];date=x['d1_date'];name=date+'_'+s;t=time.time()
 try:
  f=ak.stock_zh_a_daily(symbol=('sh' if s.startswith('6') else 'sz')+s,start_date=x['start'],end_date=x['end'],adjust='');f.to_csv(A/(name+'.csv'),index=False,encoding='utf-8-sig');z=dict(**x,rows=len(f),seconds=time.time()-t)
 except Exception as e:z=dict(**x,error=str(e)[:400],seconds=time.time()-t)
 (A/(name+'.json')).write_text(json.dumps(z,ensure_ascii=False,indent=2));print(json.dumps(z,ensure_ascii=False),flush=True)
else:
 d=pd.read_pickle(O/'union_demand.pkl');jobs=[]
 for year in ['2018','2019','2020','2021']:
  g=d[d.d1_date.str.startswith(year)&d.d1_volume.gt(0)&~d.symbol.str.startswith(('4','8','9'))].sort_values(['d1_date','symbol'])
  for idx in np.linspace(0,len(g)-1,10,dtype=int):
   x=g.iloc[idx];day=pd.Timestamp(x.d1_date);jobs.append(dict(symbol=x.symbol,d1_date=x.d1_date,start=(day-pd.Timedelta(days=20)).strftime('%Y%m%d'),end=(day+pd.Timedelta(days=20)).strftime('%Y%m%d')))
 (O/'akshare_year_sample_jobs.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2))
 for i,x in enumerate(jobs):
  if (A/(x['d1_date']+'_'+x['symbol']+'.json')).exists():continue
  try:p=subprocess.run([sys.executable,__file__,str(i)],timeout=35,capture_output=True,text=True);print(p.stdout[-1000:],flush=True)
  except subprocess.TimeoutExpired:print('TIMEOUT',x,flush=True)
 # Compare only the prespecified candidate day; avoids inflating denominator with adjacent days.
 results=[]
 for x in jobs:
  p=A/(x['d1_date']+'_'+x['symbol']+'.csv');rec=dict(**x,fetched=False)
  if p.exists():
   try:f=pd.read_csv(p);q=f[f.date.astype(str).eq(x['d1_date'])]
   except Exception:q=pd.DataFrame()
   rec['fetched']=len(q)==1
   if len(q)==1:
    ref=d[d.symbol.eq(x['symbol'])&d.d1_date.eq(x['d1_date'])].iloc[0]
    for k in ['open','high','low','close','volume']:rec[k+'_diff']=float(q.iloc[0][k])-float(ref['d1_'+k])
    rec['ohlc_ok']=all(abs(rec[k+'_diff'])<=.011 for k in ['open','high','low','close'])
  results.append(rec)
 f=pd.DataFrame(results);f.to_csv(O/'akshare_year_sample_comparison.csv',index=False,encoding='utf-8-sig');print(f.groupby(f.d1_date.str[:4])[['fetched','ohlc_ok']].sum().to_string(),flush=True)
