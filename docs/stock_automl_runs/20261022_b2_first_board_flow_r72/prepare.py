from pathlib import Path
import sys,json,hashlib
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
R71=Path('/data/cyris/work/b2_board_volume_r71_20261022')
R69=Path('/data/cyris/work/b2_d2_limit_follow_r69_20261021')
sys.path.insert(0,'/data/cyris/fin_agent_deploy/b2-20261015')
from scripts.benchmark_automl_1440_inference import db_connection,query
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
old=pd.read_csv(R71/'preboard_daily_source.csv.gz',dtype={'stk_code':str,'trade_date':str,'对应首板日':str})
pairs=sorted(set(zip(old.trade_date,old.stk_code)))
rows=[]
with db_connection('/home/che/cyris/fin_agent/.env') as db:
 for i in range(0,len(pairs),350):
  part=pairs[i:i+350]
  sql='SELECT trade_date,stk_code,preclose,open,high,low,close,volume,amount,turn_ratio,is_limit_price FROM kcrp_stock_price WHERE (trade_date,stk_code) IN ('+','.join(['(%s,%s)']*len(part))+')'
  rows.append(query(db,sql,tuple(v for pair in part for v in pair)))
 db.rollback()
raw=pd.concat(rows,ignore_index=True)
raw.trade_date=raw.trade_date.astype(str)
assert len(raw)==len(pairs),(len(raw),len(pairs))
check=old.merge(raw,on=['trade_date','stk_code'],validate='many_to_one',suffixes=('_old',''))
assert len(check)==len(old)
for c in ['volume','amount','turn_ratio','close']:
 assert np.allclose(pd.to_numeric(check[c]),pd.to_numeric(check[c+'_old']),rtol=1e-9,atol=.001),c
raw.sort_values(['stk_code','trade_date']).to_csv(R/'preboard_ohlcv_source.csv.gz',index=False)
m={'sources':{'r69_signals_sha256':sha(R69/'all_signals.pkl'),
 'r69_daily_sha256':sha(R69/'daily.pkl'),
 'r71_preboard_sha256':sha(R71/'preboard_daily_source.csv.gz'),
 'r71_board_sha256':sha(R71/'board_daily_source.csv.gz')},
 'fetch':{'table':'kingdomai.kcrp_stock_price','mode':'read-only','unique_pairs':len(pairs),'actual_rows':len(raw),'original_episode_rows':len(old)},
 'files':{'preboard_ohlcv_source.csv.gz':sha(R/'preboard_ohlcv_source.csv.gz')}}
(R/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(m,ensure_ascii=False,indent=2))
