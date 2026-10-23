from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import sys,json,hashlib
import pandas as pd
O=Path('/data/cyris/work/b2_monthly_no_k_exit_r89_20261023')
S=Path('/data/cyris/fin_agent_deploy/b2-v2-20261021')
sys.path[:0]=[str(S),str(O/'inputs'),'/data/cyris/work/b2_d2_intraday_r67_20261021/inputs']
from scripts.benchmark_automl_1440_inference import db_connection,query
from backfill_tushare_minutes import Tushare,SlidingRateLimiter,token_from_file
from import_tushare_minutes_2024 import convert
ROOTS=[Path('/data/cyris/work')/n/'minutes' for n in ['b2_d2_intraday_r67_20261021','b2_d2_limit_follow_r69_20261021','b2_floor_minutes_r70_20261021']]
t=pd.read_csv('/data/cyris/work/b2_no_k_r88_20261023/逐笔三方案.csv',dtype={'code':str})
x=t[['d2','code','d1close']].drop_duplicates(['d2','code'])
pairs=[(r.d2,r.code+('.SH' if r.code.startswith('6') else '.SZ')) for r in x.itertuples(index=False)]
ph=','.join(['(%s,%s)']*len(pairs))
with db_connection('/home/che/cyris/fin_agent/.env') as db:
 d=query(db,f'SELECT trade_date,stk_code,open,high,low,close,preclose,volume FROM kcrp_stock_price WHERE (trade_date,stk_code) IN ({ph})',tuple(y for p in pairs for y in p))
 db.rollback()
d['code']=d.stk_code.str[:6];d['trade_date']=d.trade_date.astype(str)
for c in ['open','high','low','close','preclose','volume']:d[c]=pd.to_numeric(d[c])
z=x.merge(d,left_on=['d2','code'],right_on=['trade_date','code'],validate='one_to_one')
assert len(z)==len(x)==214
assert (z.d1close-z.preclose).abs().max()<.011
z['needs_minutes']=z.high/z.d1close>1.07+1e-12
z.to_csv(O/'D2日线与分钟需求.csv',index=False,encoding='utf-8-sig')
CLOCK=[f'{i//60:02d}:{i%60:02d}' for i in list(range(571,691))+list(range(781,901))]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def validate(b,r):
 b=b.copy();b['time']=pd.to_datetime(b.time);b=b.sort_values('time')
 assert b.time.dt.strftime('%Y-%m-%d').eq(r.d2).all(),(r.d2,r.code,'day')
 assert b.time.dt.strftime('%H:%M').tolist()==CLOCK,(r.d2,r.code,'clock',len(b))
 for c in ['open','high','low','close','volume']:b[c]=pd.to_numeric(b[c])
 assert (b[['open','high','low','close']].gt(0).all().all() and b.volume.ge(0).all()),(r.d2,r.code,'nonpositive')
 for key,value in [('open',b.open.iloc[0]),('high',b.high.max()),('low',b.low.min()),('close',b.close.iloc[-1])]:
  assert abs(float(value)-float(getattr(r,key)))<.011,(r.d2,r.code,key,value,getattr(r,key))
 return b
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60))
def one(r):
 target=O/'minutes'/f'{r.d2}_{r.code}.pkl'
 if target.exists():
  b=validate(pd.read_pickle(target),r)
  return dict(d2=r.d2,code=r.code,source='R89 existing',rows=len(b),sha256=sha(target))
 for root in ROOTS:
  p=root/f'{r.d2}_{r.code}.pkl'
  if p.exists():
   try:b=validate(pd.read_pickle(p),r)
   except Exception as e:print('CACHE_REJECT',r.d2,r.code,str(e),flush=True);continue
   b.to_pickle(target)
   return dict(d2=r.d2,code=r.code,source=str(p),rows=len(b),sha256=sha(target))
 code=r.code+('.SH' if r.code.startswith('6') else '.SZ')
 day=r.d2
 fields,rows=api.query('stk_mins',dict(ts_code=code,freq='1min',start_date=day+' 09:30:00',end_date=day+' 15:00:00',limit=8000))
 raw=pd.DataFrame(rows,columns=fields)
 for c in ['open','high','low','close','vol','amount']:raw[c]=pd.to_numeric(raw[c])
 b=validate(convert(raw,day),r)
 b.to_pickle(target)
 return dict(d2=day,code=r.code,source='Tushare stk_mins via api.quicksync.cn',rows=len(b),sha256=sha(target))
needed=list(z[z.needs_minutes].itertuples(index=False))
out=[];fail=[]
with ThreadPoolExecutor(max_workers=4) as pool:
 futures={pool.submit(one,r):r for r in needed}
 for n,f in enumerate(as_completed(futures),1):
  r=futures[f]
  try:out.append(f.result())
  except Exception as e:fail.append(dict(d2=r.d2,code=r.code,error=str(e)))
  if n%10==0 or n==len(needed):print('PROGRESS',n,'/',len(needed),'ok',len(out),'fail',len(fail),flush=True)
(O/'minute_sources.json').write_text(json.dumps(sorted(out,key=lambda q:(q['d2'],q['code'])),ensure_ascii=False,indent=2))
(O/'minute_failures.json').write_text(json.dumps(fail,ensure_ascii=False,indent=2))
print('FINAL',len(out),len(fail),flush=True)
if fail:raise RuntimeError('missing complete minute sources')
