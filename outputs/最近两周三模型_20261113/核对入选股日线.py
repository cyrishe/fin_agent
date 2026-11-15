from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests,pandas as pd,json,hashlib,time
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;nov=pd.read_pickle(P/'十一月每日名单.pkl.gz');oct=pd.read_pickle(P/'十月三十日全候选.pkl.gz');oct=oct[oct[['每日滚动排名','每月训练排名','六训二验排名']].le(2).any(axis=1)];x=pd.concat([oct[['stk_code','symbol']],nov[['stk_code','symbol']]],ignore_index=True).drop_duplicates('stk_code');url='https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
def fetch(row):
 code=row['stk_code'];p={'symbol':('sh' if code.endswith('.SH') else 'sz')+row['symbol'],'scale':240,'ma':'no','datalen':30}
 for attempt in range(3):
  try:
   r=requests.get(url,params=p,timeout=20);r.raise_for_status();data=r.json();assert data;sha=hashlib.sha256(r.content).hexdigest();a=[]
   for q in data:
    if '2026-10-30'<=q['day']<='2026-11-13':a.append({'stk_code':code,'day':q['day'],'open':float(q['open']),'high':float(q['high']),'low':float(q['low']),'close':float(q['close']),'volume':float(q['volume']),'sha256':sha})
   return a,{'stk_code':code,'rows':len(a),'sha256':sha,'error':None}
  except Exception as e:err=repr(e);time.sleep(.5*(attempt+1))
 return [],{'stk_code':code,'rows':0,'error':err}
rows=[];audit=[]
with ThreadPoolExecutor(max_workers=6) as ex:
 for f in as_completed([ex.submit(fetch,row) for row in x.to_dict('records')]):a,b=f.result();rows.extend(a);audit.append(b)
y=pd.DataFrame(rows);assert not y.duplicated(['stk_code','day']).any();y.to_csv(P/'入选股公开日线.csv',index=False);pd.DataFrame(audit).to_csv(P/'入选股日线来源.csv',index=False);daily=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');daily['trade_date']=daily.trade_date.astype(str);daily=daily.rename(columns={'trade_date':'day'});daily=daily[daily.day.ge('2026-10-30')];z=y.merge(daily[['stk_code','day','open','high','low','close']],on=['stk_code','day'],how='inner',suffixes=('_公开','_原'),validate='one_to_one')
for col in ['open','high','low','close']:z[col+'_差']=(z[col+'_公开']-pd.to_numeric(z[col+'_原'])).abs()
z.to_csv(P/'入选股日线双源差异.csv',index=False);print('股票',len(x),'成功',sum(a['error'] is None for a in audit),'对照股票日',len(z),'四价最大差',z[[c+'_差' for c in ['open','high','low','close']]].max().to_dict(),flush=True)
