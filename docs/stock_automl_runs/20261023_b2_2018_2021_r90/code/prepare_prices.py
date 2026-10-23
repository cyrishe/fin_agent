from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
raw=ROOT/'raw';dest=ROOT/'price_months';dest.mkdir(exist_ok=True)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
days=sorted(pd.read_csv(ROOT/'trade_cal.csv',dtype=str).loc[lambda x:x.is_open.eq('1'),'cal_date'].tolist())
for month in pd.period_range('2016-01','2017-12',freq='M'):
 name=str(month);p=dest/f'price_{name}.csv.gz'
 if p.exists():continue
 pairkeys=sorted(x.stem.removeprefix('daily_').removesuffix('.csv') for x in raw.glob('daily_*.csv.gz') if x.name[6:12]==name.replace('-','') or x.name[15:21]==name.replace('-',''))
 assert len(pairkeys)>=7,(name,len(pairkeys))
 d=pd.concat([pd.read_csv(raw/f'daily_{k}.csv.gz',dtype={'ts_code':str,'trade_date':str}) for k in pairkeys],ignore_index=True)
 b=pd.concat([pd.read_csv(raw/f'basic_{k}.csv.gz',dtype={'ts_code':str,'trade_date':str},usecols=['ts_code','trade_date','turnover_rate']) for k in pairkeys],ignore_index=True)
 assert not d.duplicated(['ts_code','trade_date']).any()
 assert not b.duplicated(['ts_code','trade_date']).any()
 f=d.merge(b,on=['ts_code','trade_date'],how='left',validate='one_to_one')
 f=f[f.trade_date.str[:6].eq(name.replace('-',''))].copy()
 f=f.rename(columns={'ts_code':'stk_code','pre_close':'preclose','vol':'volume','turnover_rate':'turn_ratio'})
 f['trade_date']=pd.to_datetime(f.trade_date).dt.strftime('%Y-%m-%d')
 f['volume']=pd.to_numeric(f.volume)*100
 f['amount']=pd.to_numeric(f.amount)*1000
 pre=pd.to_numeric(f.preclose)
 cap=np.floor(pre*110+0.5000001)/100
 f['is_limit_price']=((pd.to_numeric(f.close)-cap).abs()<.001).astype(int)
 assert set(f.trade_date.str.replace('-',''))=={x for x in days if x[:6]==name.replace('-','')},(name,f.trade_date.nunique())
 f[['trade_date','stk_code','preclose','open','high','low','close','volume','amount','turn_ratio','is_limit_price']].to_csv(p,index=False,compression='gzip')
 print(name,len(f),'up',f.is_limit_price.sum(),'missing_turn',f.turn_ratio.isna().sum(),flush=True)
records=[]
for month in pd.period_range('2016-01','2022-01',freq='M'):
 name=str(month);p=dest/f'price_{name}.csv.gz'
 assert p.exists(),p
 q=pd.read_csv(p,usecols=['trade_date','stk_code'],dtype=str)
 assert not q.duplicated(['trade_date','stk_code']).any()
 records.append(dict(month=name,path=str(p),sha256=sha(p),rows=len(q),dates=sorted(q.trade_date.unique())))
(ROOT/'daily_sources.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
print('complete',len(records),sum(x['rows'] for x in records),flush=True)
