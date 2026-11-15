from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests,pandas as pd,json,hashlib,time
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;nov=pd.read_pickle(P/'十一月每日名单.pkl.gz');oct=pd.read_pickle(P/'十月三十日全候选.pkl.gz');oct=oct[oct[['每日滚动排名','每月训练排名','六训二验排名']].le(2).any(axis=1)];x=pd.concat([oct[['stk_code','symbol']],nov[['stk_code','symbol']]],ignore_index=True).drop_duplicates('stk_code');url='http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData'
def fetch(row):
 code=row['stk_code'];payload={'stReq':{'stHeader':{'shtMarket':1 if code.endswith('.SH') else 0},'sCode':row['symbol'],'eLineType':1,'shtStartxh':0,'shtWantNum':8000}}
 for attempt in range(3):
  try:
   r=requests.post(url,json=payload,timeout=35);r.raise_for_status();bars=r.json().get('stRsp',{}).get('vAnalyData',[]);assert bars;out=[]
   for b in bars:
    t=b['sttDateTime'];day=str(t['iDate']);minute=t['shtTime']
    if '20261102'<=day<='20261113' and (571<=minute<=690 or 781<=minute<=900):out.append({'symbol':code.replace('.SH','.XSHG').replace('.SZ','.XSHE'),'day':day[:4]+'-'+day[4:6]+'-'+day[6:],'minute':minute,'open':b['fOpen'],'high':b['fHigh'],'low':b['fLow'],'close':b['fClose'],'volume':float(b['lVolume'])*100,'amount':b['fAmount']})
   return out,{'stk_code':code,'rows':len(out),'source_sha256':hashlib.sha256(r.content).hexdigest(),'error':None}
  except Exception as e:err=repr(e);time.sleep(.5*(attempt+1))
 return [],{'stk_code':code,'rows':0,'error':err}
rows=[];audit=[]
with ThreadPoolExecutor(max_workers=6) as ex:
 for f in as_completed([ex.submit(fetch,r) for r in x.to_dict('records')]):a,b=f.result();rows.extend(a);audit.append(b)
z=pd.DataFrame(rows);assert not z.duplicated(['symbol','day','minute']).any();z.to_pickle(P/'入选股后续分钟.pkl.gz',compression='gzip');pd.DataFrame(audit).to_csv(P/'入选股后续分钟来源.csv',index=False);print('股票',len(x),'成功',sum(a['error'] is None for a in audit),'分钟',len(z),'股票日',z[['symbol','day']].drop_duplicates().shape[0],flush=True)
