from pathlib import Path
import sys,json,time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor,as_completed
sys.path.insert(0,'/data/cyris/fin_agent_deploy/b2-20261015')
from dotenv import load_dotenv
load_dotenv('/home/che/cyris/fin_agent/.env')
from scripts.benchmark_automl_1440_inference import db_connection,query
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022');(R/'flow_raw').mkdir(exist_ok=True)
s=pd.read_pickle('/data/cyris/work/b2_news_factor_r74_20261022/cohort709.pkl')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
jobs=[]
for code,g in s.groupby('stk_code'):
 start=cal[max(0,cal.index(g.d0_date.min())-21)]
 jobs.append((code,start,g.d3_date.max()))
def batch(jobs):
 receipts=[]
 with db_connection() as c:
  for code,start,end in jobs:
   p=R/'flow_raw'/(code+'.pkl')
   if not p.exists():
    x=query(c,"SELECT * FROM kcrp_stock_moneyflow WHERE stk_code IN (%s,%s) AND trade_date BETWEEN %s AND %s ORDER BY trade_date",(code[:6],code,start,end))
    for k in x:
     if k.endswith(('value','ratio')):x[k]=pd.to_numeric(x[k],errors='coerce').astype(float)
    x.to_pickle(p)
   else:x=pd.read_pickle(p)
   receipts.append({'stock':code,'start':start,'end':end,'rows':len(x)})
  c.rollback()
 return receipts
out=[]
with ThreadPoolExecutor(max_workers=3) as pool:
 for v in as_completed([pool.submit(batch,jobs[i::3]) for i in range(3)]):
  out+=v.result();print('stocks',len(out),'rows',sum(x['rows'] for x in out),flush=True)
pd.DataFrame(out).to_csv(R/'flow_fetch_coverage.csv',index=False)
print('DONE',len(out),flush=True)

