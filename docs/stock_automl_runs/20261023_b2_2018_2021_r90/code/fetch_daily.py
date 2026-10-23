from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0,'/data/cyris/tushare_backfill')
from backfill_tushare_minutes import Tushare,token_from_file,SlidingRateLimiter
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60))
def q(name,params):
    fields,rows=api.query(name,params)
    return pd.DataFrame(rows,columns=fields)
cal=ROOT/'trade_cal.csv'
if not cal.exists(): q('trade_cal',dict(exchange='SSE',start_date='20160101',end_date='20171231')).to_csv(cal,index=False)
days=sorted(pd.read_csv(cal,dtype=str).loc[lambda x:x.is_open.eq('1'),'cal_date'].tolist())
assert len(days)>470 and days[0]=='20160104' and days[-1]=='20171229',(len(days),days[:1],days[-1:])
for i in range(0,len(days),2):
    pair=days[i:i+2];key=f'{pair[0]}_{pair[-1]}'
    for api_name,prefix in [('daily','daily'),('daily_basic','basic')]:
        path=ROOT/'raw'/f'{prefix}_{key}.csv.gz'
        if path.exists():continue
        frame=q(api_name,dict(start_date=pair[0],end_date=pair[-1]))
        assert set(frame.trade_date)==set(pair),(api_name,key,len(frame),frame.trade_date.unique())
        assert len(frame)<8000,(api_name,key,len(frame))
        assert frame.ts_code.str[:6].str.isnumeric().all()
        temp=path.with_suffix('.tmp')
        frame.to_csv(temp,index=False,compression='gzip')
        temp.replace(path)
    if i%20==0:print('fetched',i+len(pair),'/',len(days),key,flush=True)
print('complete',len(days),len(list((ROOT/'raw').glob('daily_*.csv.gz'))),len(list((ROOT/'raw').glob('basic_*.csv.gz'))),flush=True)
