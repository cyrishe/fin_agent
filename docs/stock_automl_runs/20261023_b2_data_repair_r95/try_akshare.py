import sys,json,time,subprocess
from pathlib import Path
O=Path('/data/cyris/work/b2_data_repair_r95_20261023');A=O/'akshare_daily';A.mkdir(exist_ok=True)
jobs=[('tx','sh600000','20140101','20171231'),('em','000001','20140101','20171231'),('sina','sz300313','20190524','20190524'),('tx','sz300313','20190524','20190524'),('tx','sz002457','20200714','20200714'),('tx','sh600579','20180226','20180226'),('tx','sz002927','20180309','20180309')]
jobs += [('em','600579','20180201','20180331'),('sina','sh600579','20180201','20180331'),('tx','sh600579','20180201','20180331'),('em','002927','20180301','20180331'),('sina','sz002927','20180301','20180331'),('tx','sz002927','20180301','20180331')]
if len(sys.argv)>1:
 import requests,akshare as ak
 from functools import wraps
 original=requests.sessions.Session.request
 @wraps(original)
 def bounded(self,*args,**kwargs):
  if kwargs.get('timeout') is None:kwargs['timeout']=15
  return original(self,*args,**kwargs)
 requests.sessions.Session.request=bounded
 provider,symbol,start,end=jobs[int(sys.argv[1])];t=time.time();name=f'{provider}_{symbol}_{start}_{end}'
 try:
  if provider=='tx':f=ak.stock_zh_a_hist_tx(symbol=symbol,start_date=start,end_date=end,adjust='',timeout=15)
  elif provider=='em':f=ak.stock_zh_a_hist(symbol=symbol,period='daily',start_date=start,end_date=end,adjust='',timeout=15)
  else:f=ak.stock_zh_a_daily(symbol=symbol,start_date=start,end_date=end,adjust='')
  f.to_csv(A/(name+'.csv'),index=False,encoding='utf-8-sig');rec=dict(provider=provider,symbol=symbol,start=start,end=end,rows=len(f),columns=f.columns.tolist(),akshare_version=ak.__version__,seconds=time.time()-t)
 except Exception as e:rec=dict(provider=provider,symbol=symbol,start=start,end=end,rows=None,error=type(e).__name__+': '+str(e)[:700],akshare_version=ak.__version__,seconds=time.time()-t)
 (A/(name+'.json')).write_text(json.dumps(rec,ensure_ascii=False,indent=2));print(json.dumps(rec,ensure_ascii=False),flush=True)
else:
 for i,job in enumerate(jobs):
  name=f'{job[0]}_{job[1]}_{job[2]}_{job[3]}'
  if (A/(name+'.json')).exists():continue
  try:p=subprocess.run([sys.executable,__file__,str(i)],timeout=70,text=True,capture_output=True);print(p.stdout[-2500:],flush=True);print(p.stderr[-400:] if p.returncode else '',flush=True)
  except subprocess.TimeoutExpired:print('TIMEOUT',job,flush=True)
