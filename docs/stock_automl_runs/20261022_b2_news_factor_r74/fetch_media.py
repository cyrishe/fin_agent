from pathlib import Path
import pandas as pd,json,requests,threading,time,re
from concurrent.futures import ThreadPoolExecutor,as_completed
R=Path('/data/cyris/work/b2_news_factor_r74_20261022');(R/'sina_windows').mkdir(exist_ok=True)
s=pd.read_pickle(R/'cohort709.pkl')
local=threading.local()
rate_lock=threading.Lock()
access_stopped=threading.Event()
next_request=time.monotonic()+65
def rate_wait():
 global next_request
 with rate_lock:
  wait=max(0,next_request-time.monotonic());next_request=max(next_request,time.monotonic())+1.2
 if wait:time.sleep(wait)
def sess():
 if not hasattr(local,'s'):
  local.s=requests.Session();local.s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://search.sina.com.cn/search'})
 return local.s
def fetch(row):
 if access_stopped.is_set():return {'id':row['id'],'ok':False,'error':'source requested login; collection stopped'}
 fn=R/'sina_windows'/(row['id'].replace('|','_')+'.json')
 if fn.exists():
  x=json.loads(fn.read_text())
  if x.get('ok'):return x
 official=json.loads((R/'cninfo_windows'/(row['id'].replace('|','_')+'.json')).read_text())
 hist=[d for d in official['items'] if d['adjunctUrl'].split('/')[1]<=row['d0_date']]
 name=hist[0]['secName'] if hist else row['stk_name']
 names=list(dict.fromkeys([row['symbol'],str(name).replace('*ST','').removeprefix('ST')]))
 out={'id':row['id'],'start':row['start10'],'cutoff':row['d1_date']+' 09:30:00','terms':names,'ok':False,'items':[],'queries':[]}
 try:
  for term in names:
   if term!=names[0] and out['items']:break
   for page in range(1,51):
    params={'q':term,'tp':'news','sort':1,'page':page,'size':20,'from':'search_result','stime':int(pd.Timestamp(row['start10'],tz='Asia/Shanghai').timestamp()),'etime':int(pd.Timestamp(out['cutoff'],tz='Asia/Shanghai').timestamp())}
    for attempt in range(3):
     try:
      rate_wait()
      r=sess().get('https://search.sina.com.cn/api/news',params=params,timeout=20)
      if r.status_code in (401,403,429):
       access_stopped.set()
       raise RuntimeError('source access limit or login required; stopped')
      r.raise_for_status();j=r.json()
      if '登录' in str(j.get('message','')):access_stopped.set()
      if j.get('code')!=0:raise RuntimeError('search response code '+str(j.get('code'))+' '+str(j.get('message'))[:100])
      break
     except Exception:
      if access_stopped.is_set() or attempt==2:raise
      time.sleep(2+attempt)
    d=j.get('data') or {};items=d.get('list') or [];out['queries'].append({'params':params,'response':j});out['items']+=items
    if page*20>=int(d.get('total') or 0) or not items:break
    time.sleep(.2)
   else:raise RuntimeError('pagination truncated after 1000 results')
  # Never silently accept a provider that ignored dates.
  for x in out['items']:
   tm=x.get('dataTime') or x.get('time') or ''
   if tm and not row['start10']<=tm<=out['cutoff']:raise ValueError('provider returned out-of-window article')
  out['ok']=True
 except Exception as e:out['error']=type(e).__name__+': '+str(e)[:200]
 out['retrieved_at']=pd.Timestamp.now(tz='Asia/Shanghai').isoformat()
 fn.write_text(json.dumps(out,ensure_ascii=False));time.sleep(.25);return out
records=s.to_dict('records');records.sort(key=lambda r:(not (r['v2_allowed'] and r['D2D3连续收盘跌停']),r['d0_date']))
results=[]
with ThreadPoolExecutor(max_workers=1) as pool:
 for f in as_completed([pool.submit(fetch,r) for r in records]):
  x=f.result();results.append({k:v for k,v in x.items() if k not in ['items','queries']})
  if len(results)%25==0:print('media',len(results),'/709 ok',sum(x['ok'] for x in results),flush=True)
pd.DataFrame(results).to_csv(R/'media_fetch_coverage.csv',index=False,encoding='utf-8-sig')
print('DONE media',len(results),'ok',sum(x['ok'] for x in results),flush=True)

