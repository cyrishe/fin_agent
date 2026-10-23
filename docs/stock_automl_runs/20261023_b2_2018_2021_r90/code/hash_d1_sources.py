from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import json,pandas as pd,hashlib
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
M=Path('/data/cyris/tushare_market_data')
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def one(day):
 p=M/day[:4]/f'{day.replace("-","")}.csv.gz'
 q=p.with_suffix('').with_suffix('.json')
 meta=json.loads(q.read_text())
 digest=sha(p)
 assert digest==meta['sha256'] and meta['trade_date']==day.replace('-',''),day
 return dict(day=day,path=str(p),sha256=digest,bars=meta['bars'])
if __name__=='__main__':
 d=pd.read_pickle(R/'minute_demand.pkl')
 days=sorted(d.d1_date.dropna().unique())
 with ProcessPoolExecutor(max_workers=4) as pool:
  rows=list(pool.map(one,days))
 (R/'d1_minute_sources.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 print('hashed D1 day files',len(rows),'first',days[0],'last',days[-1],flush=True)
