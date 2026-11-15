from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,hashlib,requests,pandas as pd,numpy as np,gzip
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/b2连续两日全量回放_20261113';parts=[]
for month in ['2026-10','2026-11']:
 x=pd.read_pickle(B/'近期输入'/month/'test_samples.pkl.gz');parts.append(x[x.d0_date.ge('2026-10-26')][['stk_code','symbol']])
codes=pd.concat(parts).drop_duplicates('stk_code').to_dict('records');url='http://jzyzwup.upoem1.com/json/hq_basichq/stockHq'
def fetch(batch):
 p={'stReq':{'vStock':[{'shtSetcode':1 if a['stk_code'].endswith('.SH') else 0,'sCode':a['symbol']} for a in batch],'eHqData':1}};r=requests.post(url,json=p,timeout=20);r.raise_for_status();raw=r.json().get('stRsp',{}).get('vStockHq',[]);sha=hashlib.sha256(r.content).hexdigest();out=[]
 for item in raw:
  q=item.get('stSimHq') or {};zt=q.get('ztData') or {};out.append({'symbol':str(item.get('sCode',''))[:6],'名称':item.get('sName'),'最新价':q.get('fNowPrice'),'开盘':q.get('fOpen'),'前收':q.get('fClose'),'最高':q.get('fHigh'),'最低':q.get('fLow'),'成交量手':q.get('lVolume'),'成交额':q.get('fAmount'),'原始涨停标记':zt.get('bZT'),'来源响应SHA256':sha})
 return out
batches=[codes[i:i+150] for i in range(0,len(codes),150)]
with ThreadPoolExecutor(max_workers=4) as ex:out=sum(ex.map(fetch,batches),[])
z=pd.DataFrame(out);assert not z.duplicated('symbol').any();z.to_csv(P/'十一月十三日行情与名称.csv',index=False)
z['公开源最新分钟日期']=None;z['公开源最新分钟收盘']=np.nan
for i,r in z.iterrows():
 stock=next((c['stk_code'] for c in codes if c['symbol']==r.symbol),None);path=P/'公开分钟原始'/(str(stock)+'.json.gz')
 if not path.exists():continue
 a=json.loads(gzip.decompress(path.read_bytes()));last=a[-1];z.loc[i,'公开源最新分钟日期']=last['day'];z.loc[i,'公开源最新分钟收盘']=float(last['close'])
z['最新价差']=(pd.to_numeric(z.最新价,errors='coerce')-z.公开源最新分钟收盘).abs();z.to_csv(P/'十一月十三日双源校验.csv',index=False);v=z[z.公开源最新分钟日期.str.startswith('2026-11-13',na=False)];print('requested',len(codes),'returned',len(z),'有当日分钟',len(v),'末价≤1分',int(v.最新价差.le(.011).sum()),flush=True)
