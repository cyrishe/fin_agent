from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import sys,json,hashlib
import pandas as pd,numpy as np
sys.path.insert(0,'/data/cyris/work/b2_d2_intraday_r67_20261021')
import experiment as old
R=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
S=Path('/data/cyris/work/b2_d2_limit_follow_r69_20261021')
M=Path('/data/cyris/work/b2_model_economics_r73_20261022')
D=Path('/data/cyris/work/b2_d2_intraday_r67_20261021')
P=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022')
(R/'minutes').mkdir(exist_ok=True)
s=pd.read_pickle(S/'all_signals.pkl')
s=s[s.v2_allowed].sort_values('d0_date').copy()
s['id']=s.stk_code+'|'+s.d0_date
assert len(s)==306 and s.id.is_unique
p=pd.read_pickle(P/'predictions306.pkl').set_index('id')
assert s.id.isin(p.index).all()
daily=pd.read_pickle(S/'daily.pkl').set_index(['trade_date','stk_code'])
cal=json.loads((D/'inputs/calendar.json').read_text())
records=[];data=[]
for n,r in enumerate(s.itertuples(),1):
 fn=f'{r.d2_date}_{r.symbol}'
 oldr=M/'minutes'/(fn+'.csv.gz')
 prev=S/'minutes'/(fn+'.pkl')
 prev2=D/'minutes'/(fn+'.pkl')
 if oldr.exists():
  bars=pd.read_csv(oldr);source={'type':'R73 minute','path':str(oldr)}
 elif prev.exists():
  bars=pd.read_pickle(prev);source={'type':'R69 minute','path':str(prev)}
 elif prev2.exists():
  bars=pd.read_pickle(prev2);source={'type':'R67 minute','path':str(prev2)}
 else:
  day=r.d2_date
  archives=[
   Path('/data/cyris/tushare_market_data')/day[:4]/(day.replace('-','')+'.csv.gz'),
   R/'raw/outputs/stock_automl'/('mk_1m_compact_'+day[:4])/(day+'.csv.zst'),
   D/'raw/outputs/stock_automl'/('mk_1m_compact_'+day[:4])/(day+'.csv.zst'),
   D/'raw/outputs/stock_automl/mk_1m_compact'/(day+'.csv.zst'),
   S/'raw/outputs/stock_automl'/('mk_1m_compact_'+day[:4])/(day+'.csv.zst'),
   S/'raw/outputs/stock_automl/mk_1m_compact'/(day+'.csv.zst'),
  ]
  path=next((x for x in archives if x.exists()),None)
  if path is None:raise FileNotFoundError(fn)
  bars=old.extract_archive(path,day,r.symbol)
  source={'type':'archive','path':str(path)}
 bars=bars.copy();bars['time']=pd.to_datetime(bars.time)
 bars=bars.sort_values('time').reset_index(drop=True)
 for c in ['open','high','low','close','volume']:bars[c]=pd.to_numeric(bars[c])
 assert bars.time.dt.strftime('%H:%M').tolist()==old.CLOCK,(fn,'clock')
 assert len(bars)==240 and not bars.time.duplicated().any(),fn
 assert np.isfinite(bars[['open','high','low','close','volume']].to_numpy()).all(),fn
 assert (bars[['open','high','low','close']]>0).all().all(),fn
 assert bars.volume.ge(0).all(),fn
 assert (bars.high+1e-8>=bars[['open','close','low']].max(axis=1)).all(),fn
 assert (bars.low-1e-8<=bars[['open','close','high']].min(axis=1)).all(),fn
 dr=daily.loc[(r.d2_date,r.stk_code)]
 assert abs(float(dr.preclose)-r.d1_close)<.011 and abs(float(dr.close)-r.d2_close)<.011,fn
 assert all(abs(float(v)-float(getattr(dr,key)))<.011 for key,v in {'open':bars.open.iloc[0],'high':bars.high.max(),'low':bars.low.min(),'close':bars.close.iloc[-1]}.items()),fn
 rate=.2 if r.symbol.startswith(('30','68')) else .05 if r.d2_st in ('S','Y') else .1
 floor=float((Decimal(str(r.d1_close))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
 hit=bars[(bars.time.dt.strftime('%H:%M')<='11:30') & ((bars.high/r.d1_close-1)>.07)]
 sell=r.d2_close;selltime='15:00';trigger='';proxy=False
 if len(hit):
  i=int(hit.index[0]);trigger=bars.iloc[i].time.strftime('%H:%M')
  after=bars.iloc[i+1:];eligible=after[(after.open>floor+.001)&(after.volume>0)]
  if len(eligible):
   q=eligible.iloc[0];sell=float(q.open);selltime=(q.time-pd.Timedelta(minutes=1)).strftime('%H:%M');proxy=True
 unverified=bool(not proxy and (r.d2_close<=floor+.001 or bars.iloc[-1].volume<=0))
 source['sha256']=hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()
 rows={'id':r.id,'symbol':r.symbol,'stk_code':r.stk_code,'d0_date':r.d0_date,'d1_date':r.d1_date,'d2_date':r.d2_date,'d3_date':r.d3_date,'d0_year':r.d0_date[:4],'entry':r.entry,'d1_close':r.d1_close,'d2_close':r.d2_close,'d3_close':r.d3_close,'buy_clock':(pd.to_datetime('2000-01-01 '+r.entry_time)-pd.Timedelta(minutes=1)).strftime('%H:%M'),'sell7':sell,'sell7_time':selltime,'trigger7':trigger,'seven_proxy':proxy,'close_floor':bool(r.d2_close<=floor+.001),'close_unverified':bool(r.d2_close<=floor+.001 or bars.iloc[-1].volume<=0),'seven_unverified':unverified,'all_day_locked':bool(bars.high.max()<=floor+.001),'D2开收跌停':int(p.loc[r.id,'y_union']),'连续跌停':int(p.loc[r.id,'连续跌停'])}
 data.append(rows)
 records.append({'id':r.id,'source':source,'bar_count':len(bars),'daily_prices_match':True,'morning7':trigger,'seven_proxy':proxy})
 if n%50==0:print('AUDITED',n,'/306',flush=True)
out=pd.DataFrame(data).sort_values('d0_date').reset_index(drop=True)
out.to_pickle(R/'joined_306.pkl')
out.to_csv(R/'306笔_全年同卖法价格.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
(R/'minute_source_manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
print('SOURCES',pd.Series([x['source']['type'] for x in records]).value_counts().to_dict())
print('TRIGGER',int(out.trigger7.ne('').sum()),'UNVERIFIED',int(out.seven_unverified.sum()))
ref=pd.read_pickle(M/'joined_98.pkl').set_index('id')
t=out[out.d0_year.eq('2026')].set_index('id')
assert set(t.index)==set(ref.index)
for field,orig in [('sell7','sell7'),('sell7_time','sell7_time'),('seven_proxy','seven_proxy'),('seven_unverified','seven_exit_unverified')]:
 assert np.array_equal(t.loc[ref.index,field].to_numpy(),ref[orig].to_numpy()),field
print('2026 R73 minute exit exact match')

