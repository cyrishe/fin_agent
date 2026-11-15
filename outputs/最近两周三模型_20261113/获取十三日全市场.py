from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests,pandas as pd,hashlib,json,time
P=Path(__file__).parent;listurl='https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData';quoteurl='http://jzyzwup.upoem1.com/json/hq_basichq/stockHq'
def page(i):
 p={'page':i,'num':100,'sort':'symbol','asc':1,'node':'hs_a','symbol':'','_s_r_a':'page'}
 for k in range(3):
  try:r=requests.get(listurl,params=p,timeout=15);r.raise_for_status();z=r.json();assert isinstance(z,list);return z
  except Exception:
   if k==2:raise
   time.sleep(.4*(k+1))
with ThreadPoolExecutor(max_workers=6) as pool:items=sum(pool.map(page,range(1,57)),[])
s=pd.DataFrame(items);assert not s.symbol.duplicated().any();s=s[s.symbol.str.startswith(('sh','sz'))].copy();s['stk_code']=s.code+ s.symbol.str[:2].map({'sh':'.SH','sz':'.SZ'});s.to_csv(P/'十三日全市场公开行情.csv',index=False);stock=s[['stk_code','code']].to_dict('records')
def fetch(batch):
 p={'stReq':{'vStock':[{'shtSetcode':1 if a['stk_code'].endswith('.SH') else 0,'sCode':a['code']} for a in batch],'eHqData':3}};r=requests.post(quoteurl,json=p,timeout=25);r.raise_for_status();sha=hashlib.sha256(r.content).hexdigest();out=[]
 for a in r.json().get('stRsp',{}).get('vStockHq',[]):
  q=a.get('stSimHq') or {};ex=a.get('stExHq') or {};out.append({'symbol':str(a.get('sCode',''))[:6],'名称':a.get('sName'),'最新价':q.get('fNowPrice'),'开盘':q.get('fOpen'),'前收':q.get('fClose'),'最高':q.get('fHigh'),'最低':q.get('fLow'),'成交量':float(q.get('lVolume') or 0)*100,'成交额':q.get('fAmount'),'换手率':ex.get('fTurnoverRate'),'封板标记':(q.get('ztData') or {}).get('bZT'),'来源SHA256':sha})
 return out
batches=[stock[i:i+150] for i in range(0,len(stock),150)]
with ThreadPoolExecutor(max_workers=5) as pool:rows=sum(pool.map(fetch,batches),[])
v=pd.DataFrame(rows);v['symbol']=v.symbol.astype(str).str.zfill(6);assert not v.symbol.duplicated().any();s['symbol6']=s.code.astype(str).str.zfill(6);z=s.merge(v,left_on='symbol6',right_on='symbol',how='left',validate='one_to_one');
for c in ['trade','settlement','open','high','low','volume','amount','turnoverratio']:z[c]=pd.to_numeric(z[c],errors='coerce')
for a,b in [('trade','最新价'),('settlement','前收'),('open','开盘'),('high','最高'),('low','最低')]:z[a+'差']=(z[a]-pd.to_numeric(z[b],errors='coerce')).abs()
z['量比']=pd.to_numeric(z.成交量,errors='coerce')/z.volume.replace(0,float('nan'));z['换手差']=(z.turnoverratio-pd.to_numeric(z.换手率,errors='coerce')).abs();z.to_pickle(P/'十三日全市场双源.pkl.gz',compression='gzip');z.to_csv(P/'十三日全市场双源.csv',index=False);print('新浪沪深',len(s),'行情服务返回',len(v),'双源交集',int(z.最新价.notna().sum()),'有效价格差>1分',int((z['trade差']>.011).sum()),'开盘差>1分',int((z['open差']>.011).sum()),'换手率差>0.05点',int((z.换手差>.05).sum()),'封板',int(z.封板标记.eq(1).sum()),flush=True)
