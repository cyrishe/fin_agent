from pathlib import Path
import sys, json, hashlib
import pandas as pd
sys.path.insert(0,'/data/cyris/work/b2_2016_2020_r90_20261023')
from scripts.benchmark_automl_1440_inference import db_connection,query
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
DEST=ROOT/'price_months'
DEST.mkdir(exist_ok=True)
for month in pd.period_range('2018-01','2022-01',freq='M'):
    name=str(month);path=DEST/f'price_{name}.csv.gz'
    if path.exists():continue
    with db_connection(Path('/data/cyris/fin_agent/.env')) as conn:
        frame=query(conn,'SELECT trade_date,stk_code,preclose,open,high,low,close,volume,amount,turn_ratio,is_limit_price,update_time FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s',(str(month.start_time.date()),str(month.end_time.date())))
        conn.rollback()
    assert len(frame)>30000,(name,len(frame))
    assert not frame.duplicated(['trade_date','stk_code']).any()
    path_tmp=path.with_suffix('.tmp')
    frame.to_csv(path_tmp,index=False,compression='gzip')
    path_tmp.replace(path)
    print(name,len(frame),frame.trade_date.nunique(),flush=True)
print('complete',flush=True)
