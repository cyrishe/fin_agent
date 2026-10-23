import sys,json
from pathlib import Path
import pandas as pd
O=Path('/data/cyris/work/b2_data_repair_r95_20261023');sys.path.insert(0,'/data/cyris/tushare_backfill')
from backfill_tushare_minutes import Tushare,token_from_file,SlidingRateLimiter
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60));P=O/'early_minute_probe';P.mkdir(exist_ok=True)
jobs=[('000001.SZ',x) for x in ['2015-06-12','2015-08-24','2015-12-31']]+[(s,'2016-01-05') for s in ['000022.SZ','001872.SZ','000043.SZ','001914.SZ','300114.SZ','302132.SZ','601360.SH']]
res=[]
for symbol,day in jobs:
 p=P/(day+'_'+symbol+'.json')
 if p.exists():x=json.loads(p.read_text())
 else:
  try:
   fields,rows=api.query('stk_mins',dict(ts_code=symbol,freq='1min',start_date=day+' 09:30:00',end_date=day+' 15:00:00',limit=8000));x=dict(fields=fields,rows=rows)
  except Exception as e:x=dict(fields=[],rows=[],error=str(e))
  p.write_text(json.dumps(x,ensure_ascii=False))
 f=pd.DataFrame(x['rows'],columns=x['fields']);r=dict(symbol=symbol,date=day,rows=len(f),error=x.get('error'))
 if len(f):r.update(volume=float(pd.to_numeric(f.vol).sum()),positive_bars=int(pd.to_numeric(f.vol).gt(0).sum()),first=str(f.trade_time.min()),last=str(f.trade_time.max()))
 res.append(r);pd.DataFrame(res).to_csv(O/'early_minute_probe.csv',index=False,encoding='utf-8-sig');print(json.dumps(r,ensure_ascii=False),flush=True)
 if x.get('error') and '权限' in x['error']:break
# Calendar-backed file coverage, separate from per-stock completeness.
p=O/'early_calendar.json'
if p.exists():x=json.loads(p.read_text())
else:
 fields,rows=api.query('trade_cal',dict(exchange='SSE',start_date='20150101',end_date='20161231',is_open='1'));x=dict(fields=fields,rows=rows);p.write_text(json.dumps(x))
c=pd.DataFrame(x['rows'],columns=x['fields']);c=c[c.is_open.astype(str).eq('1')];c['month']=c.cal_date.str[:4]+'-'+c.cal_date.str[4:6];c['file']=c.cal_date.map(lambda d:(Path('/data/cyris/tushare_market_data')/d[:4]/(d+'.csv.gz')).exists())
f=c.groupby('month').agg(expected_days=('cal_date','size'),archive_days=('file','sum')).reset_index();f['missing_days']=f.expected_days-f.archive_days;f.to_csv(O/'early_calendar_coverage.csv',index=False,encoding='utf-8-sig');print('CALENDAR',f.to_string(index=False),flush=True)
# Compare 2015 probe bars with independent AkShare/Eastmoney daily sample already fetched.
sys.path.insert(0,str(O));from import_tushare_minutes_fixed import convert
ref=pd.read_csv(O/'akshare_daily/em_000001_20140101_20171231.csv');ref=ref.rename(columns={'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'});rs=[]
for day in ['2015-06-12','2015-08-24','2015-12-31']:
 x=json.loads((P/(day+'_000001.SZ.json')).read_text());b=pd.DataFrame(x['rows'],columns=x['fields'])
 for col in ['open','high','low','close','vol','amount']:b[col]=pd.to_numeric(b[col])
 b=convert(b,day).sort_values('time');q=ref[ref.date.astype(str).eq(day)].iloc[0];obs=dict(open=b.open.iloc[0],high=b.high.max(),low=b.low.min(),close=b.close.iloc[-1],volume=b.volume.sum());row=dict(date=day)
 for k,v in obs.items():row[k+'_diff']=float(v-q[k]*(100 if k=='volume' else 1))
 row['ohlc_ok']=all(abs(row[k+'_diff'])<=.011 for k in ['open','high','low','close']);rs.append(row)
pd.DataFrame(rs).to_csv(O/'2015_probe_daily_check.csv',index=False,encoding='utf-8-sig');print('EARLYCHECK',rs,flush=True)
