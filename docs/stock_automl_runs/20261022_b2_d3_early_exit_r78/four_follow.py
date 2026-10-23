import sys,json
from pathlib import Path
import pandas as pd
sys.path.insert(0,'/data/cyris/fin_agent_deploy/b2-20261015')
from scripts.benchmark_automl_1440_inference import db_connection,query
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
items=[('000605.SZ','2024-11-25'),('603527.SH','2024-11-26'),('002490.SZ','2025-06-24'),('601212.SH','2026-01-30')]
parts=[]
with db_connection(Path('/data/cyris/fin_agent/.env')) as conn:
 for stock,day in items:
  i=cal.index(day);end=cal[min(i+15,len(cal)-1)]
  z=query(conn,'SELECT stk_code,trade_date,preclose,open,high,low,close,volume,is_limit_price FROM kcrp_stock_price WHERE stk_code=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date',(stock,day,end))
  z['case_start']=day
  parts.append(z)
 conn.rollback()
all=pd.concat(parts,ignore_index=True)
all.to_csv(R/'4笔跌停后续官方日线.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
for (stock,day),z in zip(items,parts):
 print(stock,day,'rows',len(z));print(z[['trade_date','open','high','low','close','volume','is_limit_price']].head(8).to_string(index=False))
