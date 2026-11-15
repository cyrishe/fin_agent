from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests,pandas as pd,json,gzip,time,hashlib
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;raw=P/'公开分钟原始';raw.mkdir(exist_ok=True);x=pd.read_pickle(R/'outputs/b2连续两日全量回放_20261113/近期输入/2026-11/test_samples.pkl.gz');codes=sorted(set(x.stk_code));url='https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
def fetch(code):
 p=raw/(code+'.json.gz')
 if p.exists():
  try:
   obj=json.loads(gzip.decompress(p.read_bytes()));assert isinstance(obj,list) and len(obj);return {'stk_code':code,'rows':len(obj),'first':obj[0]['day'],'last':obj[-1]['day'],'status':'已缓存'}
  except Exception:p.unlink()
 prefix='sh' if code.endswith('.SH') else 'sz' if code.endswith('.SZ') else None
 if not prefix:return {'stk_code':code,'status':'非沪深'}
 for attempt in range(3):
  try:
   r=requests.get(url,params={'symbol':prefix+code[:6],'scale':1,'ma':'no','datalen':1970},timeout=15);r.raise_for_status();obj=r.json()
   if not isinstance(obj,list) or not obj:raise ValueError('无分钟数据')
   p.write_bytes(gzip.compress(json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()));return {'stk_code':code,'rows':len(obj),'first':obj[0]['day'],'last':obj[-1]['day'],'status':'成功','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
  except Exception as e:
   error=repr(e);time.sleep(.3*(attempt+1))
 return {'stk_code':code,'status':'失败','error':error}
results=[]
with ThreadPoolExecutor(max_workers=6) as pool:
 for f in as_completed([pool.submit(fetch,code) for code in codes]):
  results.append(f.result())
  if len(results)%50==0:print('已完成',len(results),'/',len(codes),'成功',sum(a['status'] in ['成功','已缓存'] for a in results),flush=True)
pd.DataFrame(results).sort_values('stk_code').to_csv(P/'公开分钟抓取清单.csv',index=False);print('完成',len(results),'成功',sum(a['status'] in ['成功','已缓存'] for a in results),flush=True)
