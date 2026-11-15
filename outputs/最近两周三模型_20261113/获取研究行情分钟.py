from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests,pandas as pd,json,gzip,time,hashlib
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;raw=P/'研究行情分钟';raw.mkdir(exist_ok=True);parts=[]
for month in ['2026-10','2026-11']:
 x=pd.read_pickle(R/'outputs/b2连续两日全量回放_20261113/近期输入'/month/'test_samples.pkl.gz');x=x[x.d0_date.ge('2026-10-26')];parts.append(x[['stk_code','d0_date']])
x=pd.concat(parts,ignore_index=True);wanted={s:set(g.d0_date.str.replace('-','').astype(int)) for s,g in x.groupby('stk_code')};url='http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData'
def fetch(item):
 code,days=item;p=raw/(code+'.json.gz')
 if p.exists():
  try:
   j=json.loads(gzip.decompress(p.read_bytes()));assert isinstance(j,dict) and 'bars' in j;return {'stk_code':code,'total':j['total'],'saved':len(j['bars']),'status':'已缓存','response_sha256':j['response_sha256']}
  except Exception:p.unlink()
 payload={'stReq':{'stHeader':{'shtMarket':1 if code.endswith('.SH') else 0},'sCode':code[:6],'eLineType':1,'shtStartxh':0,'shtWantNum':8000}}
 for attempt in range(3):
  try:
   response=requests.post(url,json=payload,timeout=35);response.raise_for_status();data=response.json().get('stRsp',{}).get('vAnalyData',[]);assert data
   bars=[b for b in data if b.get('sttDateTime',{}).get('iDate') in days];j={'stk_code':code,'total':len(data),'response_sha256':hashlib.sha256(response.content).hexdigest(),'requested_days':sorted(days),'bars':bars};p.write_bytes(gzip.compress(json.dumps(j,ensure_ascii=False,separators=(',',':')).encode()));return {'stk_code':code,'total':len(data),'saved':len(bars),'status':'成功','response_sha256':j['response_sha256']}
  except Exception as e:error=repr(e);time.sleep(.5*(attempt+1))
 return {'stk_code':code,'status':'失败','error':error}
out=[]
with ThreadPoolExecutor(max_workers=6) as ex:
 for f in as_completed([ex.submit(fetch,item) for item in wanted.items()]):
  out.append(f.result())
  if len(out)%50==0:print('完成',len(out),'/',len(wanted),'成功',sum(i['status'] in ['成功','已缓存'] for i in out),flush=True)
pd.DataFrame(out).sort_values('stk_code').to_csv(P/'研究行情分钟抓取清单.csv',index=False);print('结束',len(out),'成功',sum(i['status'] in ['成功','已缓存'] for i in out),flush=True)
