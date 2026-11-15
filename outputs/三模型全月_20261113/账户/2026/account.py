from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import sys,pandas as pd,numpy as np
from functools import lru_cache
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;S=P;year=sys.argv[1];mode=sys.argv[2];assert mode in ('原始10','滑点05','原始5')
volume_ratio=.05 if mode=='原始5' else .10;slippage=.005 if mode=='滑点05' else 0.
signals=pd.DataFrame(pd.read_pickle(S/'candidates.pkl'));signals=signals[signals.month.str.startswith(year)];qdf=pd.concat([pd.read_pickle(S/'日线.pkl.gz'),pd.read_pickle(S/'日线补充.pkl.gz')],ignore_index=True).drop_duplicates(['symbol','day']);quotes={(r.symbol,r.day):r for r in qdf.itertuples()};f=pd.read_parquet('/Volumes/ext/minite_k_data/复权因子/all.parquet');f['d']=f.d.astype(str);fmap={s:sorted(zip(g.d.tolist(),g.hfq_factor.tolist())) for s,g in f.groupby('symbol')};st=pd.read_csv(R/'outputs/b2_market_3d_20261102/st_intervals_20261102.csv.gz',dtype=str).fillna({'end_date':'9999-12-31'});sm={s:list(zip(g.begin_date,g.end_date,g.st_type)) for s,g in st.groupby('stk_code')};parts=[]
for path in sorted(p for p in (S/'分钟').glob('*.pkl.gz') if not p.name.startswith('.')):
 b=pd.read_pickle(path);b['clock']=pd.to_datetime(b.ts,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%H:%M').astype('category')
 for c in ['open','high','low','close','volume','amount']:b[c]=pd.to_numeric(b[c],errors='coerce')
 b.loc[b.source.eq('kingdomai'),'volume']*=100
 parts.append(b)
allbars=pd.concat(parts,ignore_index=True);del parts,b
indices=allbars.groupby(['symbol','day'],sort=False).indices
@lru_cache(maxsize=512)
def getbar(sym,day):
 index=indices.get((sym,day))
 if index is None:return None
 return list(allbars.iloc[index].sort_values('ts').itertuples(index=False))
evidence=S/'独立行情核验.csv'
if evidence.exists():
 t=pd.read_csv(evidence);t=t[t['独立行情状态'].isin(['记录无成交','不在Tushare当日日线活跃池'])];trusted_zero=set(zip(t.symbol,t.day))
else:trusted_zero=set()
def zero_trade(sym,day,quote,bar):
 known=(sym,day) in trusted_zero and quote is None
 if quote is not None and getattr(quote,'source',None)=='kingdomai':known=float(quote.volume)==0
 return known and not any((v.volume>0 or (np.isfinite(v.amount) and v.amount>0)) for v in (bar or []))
print(year,mode,'分钟股票日',len(indices),'双源无成交证据',len(trusted_zero),flush=True)
def limit(v,m):return float((Decimal(str(v))*Decimal(str(m))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def ranges(h,day,previous):
 vals=[v for a,z,v in sm.get(h['stk_code'],[]) if a<=day<z]
 if len(vals)!=1 or vals[0] not in ('N','R','S','Y','L'):return None
 sym=h['symbol'];rate=.05 if vals[0] in ('S','Y') else .2 if sym.startswith(('688','689')) or (sym.startswith(('300','301')) and day>='2020-08-24') else .1
 return limit(previous,1+rate),limit(previous,1-rate)
clocks=list(pd.date_range('2000-01-01 09:31','2000-01-01 11:30',freq='min').strftime('%H:%M'))+list(pd.date_range('2000-01-01 13:01','2000-01-01 15:00',freq='min').strftime('%H:%M'));summary=[];events=[];equity=[]
for month,sigs in signals.groupby('month',sort=True):
 cash=100000.;holds=[];unknown=0;gap=0;zero_obs=0;peak=100000.;mdd=0.;mktdays=sorted({d for dates in sigs.dates for d in dates});sigs=sigs.copy();sigs['buy_day']=sigs.dates.map(lambda d:d[1] if len(d)>1 else None)
 for day in mktdays:
  available=cash;slots=len(sigs[sigs.buy_day.eq(day)]);allocation=available/slots if slots else 0
  for row in sigs[sigs.buy_day.eq(day)].sort_values('new_rank').to_dict('records'):
   sym=row['qdc_symbol'];d0=row['d0_date'];px=float(row['d1_open']) if pd.notna(row['d1_open']) else np.nan;daily1=quotes.get((sym,day))
   if zero_trade(sym,day,daily1,getbar(sym,day)):
    events.append({'month':month,'day':day,'symbol':row['symbol'],'action':'零成交未买入'});continue
   if daily1 is None or not np.isfinite(px) or px<=0 or abs(float(daily1.open)/px-1)>.005:unknown+=1;events.append({'month':month,'day':day,'symbol':row['symbol'],'action':'D1开盘未知'});continue
   prev=float(row['d1_preclose']) if pd.notna(row['d1_preclose']) else np.nan;rate=float(row['rate']) if pd.notna(row['rate']) else np.nan;cap=limit(prev,1+rate) if np.isfinite(prev) and np.isfinite(rate) and prev>0 else np.nan;given=float(row['d1_cap']) if pd.notna(row['d1_cap']) else np.nan
   if not np.isfinite(cap) or not np.isfinite(given) or abs(cap-given)>.011 or px>cap+.011:unknown+=1;events.append({'month':month,'day':day,'symbol':row['symbol'],'action':'D1板价或除权未知'});continue
   if allocation<=1e-8:events.append({'month':month,'day':day,'symbol':row['symbol'],'action':'资金不足'});continue
   qty=allocation/(cap*1.001);spend=qty*px*1.001;cash-=spend;holds.append({'symbol':row['symbol'],'qdc_symbol':sym,'stk_code':row['stk_code'],'d0':d0,'buy_day':day,'entry':px,'remaining':qty,'last':px,'pending':False,'frozen':False,'issues':set()});events.append({'month':month,'day':day,'symbol':row['symbol'],'action':'假设买入','shares':qty,'price':px,'amount':spend,'order_limit':cap,'reserved':allocation})
  for sym in sorted({h['qdc_symbol'] for h in holds if h['buy_day']<day and not h['frozen']}):
   group=[h for h in holds if h['qdc_symbol']==sym and h['buy_day']<day and not h['frozen']];bar=getbar(sym,day);dayq=quotes.get((sym,day));prevday=max([d for d in mktdays if d<day],default=None);prior=quotes.get((sym,prevday)) if prevday else None
   action_list=fmap.get(sym,[]);today=[i for i,(d,v) in enumerate(action_list) if d==day];adjust_ratio=1.
   if today:
    ai=today[-1];prior_factor=action_list[ai-1][1] if ai>0 else None;new_factor=action_list[ai][1]
    if prior_factor is None or not np.isfinite(prior_factor) or prior_factor<=0 or not np.isfinite(new_factor) or new_factor<=0:
     for h in group:h['frozen']=True;h['issues'].add('复权因子未知')
     gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'复权因子未知'});continue
    adjust_ratio=new_factor/prior_factor
    for h in group:
     oldshares=h['remaining'];h['remaining']*=adjust_ratio;h['last']/=adjust_ratio
     events.append({'month':month,'day':day,'symbol':h['symbol'],'action':'复权再投入近似','shares':h['remaining']-oldshares,'ratio':adjust_ratio,'d0':h['d0']})
   if zero_trade(sym,day,dayq,bar):
    events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'零成交续持'});continue
   prior_close=float((Decimal(str(float(prior.close)/adjust_ratio))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) if prior is not None and pd.notna(prior.close) and prior.close>0 else (float(group[0]['last']) if prevday and zero_trade(sym,prevday,None,getbar(sym,prevday)) else None)
   if bar is None or prior_close is None or dayq is None:
    for h in group:h['issues'].add('日线或分钟缺失')
    gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'日线或分钟缺失'});continue
   capfloor=ranges(group[0],day,prior_close)
   if capfloor is None:
    for h in group:h['issues'].add('涨跌停状态未知')
    gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'涨跌停状态未知'});continue
   cap,floor=capfloor;regular=[x for x in bar if x.clock>='09:31'];seen=0
   for i,b in enumerate(regular):
    if all(h['remaining']<=1e-9 for h in group):break
    if i>=len(clocks) or b.clock!=clocks[i]:
     for h in group:h['issues'].add('分钟缺失或重复')
     gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'分钟缺失或重复','clock':b.clock});break
    seen+=1
    vals=[b.open,b.high,b.low,b.close,b.volume]
    if any(not np.isfinite(x) for x in vals) or b.volume<0:
     for h in group:h['issues'].add('价格或成交量未知')
     gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'价格或成交量未知','clock':b.clock});break
    if b.volume==0 and (not np.isfinite(b.amount) or b.amount<=0):continue
    if b.open<=0 or b.high<=0 or b.low<=0 or b.close<=0 or b.high+1e-4<max(b.open,b.low,b.close) or b.low-1e-4>min(b.open,b.high,b.close) or b.high>cap+.03 or b.low<floor-.03:
     for h in group:h['issues'].add('分钟价格结构或涨跌停异常')
     gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'分钟价格结构或涨跌停异常','clock':b.clock});break
    if b.volume==0 and np.isfinite(b.amount) and b.amount>0:
     zero_obs+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'零量有额观察','clock':b.clock})
    sell_price=b.close*(1-slippage)
    if b.volume>0 and b.low>floor+.005 and sell_price>floor+.005:
     capacity=b.volume*volume_ratio
     for h in sorted(group,key=lambda h:(h['buy_day'],h['d0'])):
      if not h['pending'] or h['remaining']<=1e-9:continue
      qty=min(capacity,h['remaining'])
      if qty<=0:break
      h['remaining']-=qty;capacity-=qty;cash+=qty*sell_price*.999;events.append({'month':month,'day':day,'clock':b.clock,'symbol':h['symbol'],'action':'分钟分批卖出','shares':qty,'price':sell_price,'bar_close':b.close,'bar_volume':b.volume,'d0':h['d0']})
    if (b.volume>0 or (np.isfinite(b.amount) and b.amount>0)) and b.low<cap-.005:
     for h in group:h['pending']=True
   if len(regular)<240 and any(h['remaining']>1e-9 for h in group):
    for h in group:
     if h['remaining']>1e-9:h['issues'].add('分钟不完整')
    gap+=1;events.append({'month':month,'day':day,'symbol':group[0]['symbol'],'action':'路径未知','reason':'分钟不完整'})
   holds=[h for h in holds if h['remaining']>1e-9]
  for h in holds:
   v=quotes.get((h['qdc_symbol'],day))
   if v is not None and pd.notna(v.close) and v.close>0:h['last']=float(v.close)
   elif h['buy_day']<day and not zero_trade(h['qdc_symbol'],day,v,getbar(h['qdc_symbol'],day)):h['issues'].add('收盘估值缺失');gap+=1
  nav=cash+sum(h['remaining']*h['last'] for h in holds);peak=max(peak,nav);mdd=max(mdd,1-nav/peak);equity.append({'month':month,'day':day,'nav':nav,'cash':cash,'positions':len(holds)})
 issues=sorted({v for h in holds for v in h['issues']});summary.append({'month':month,'nav':nav,'return_pct':100*(nav/100000-1),'drawdown_pct':100*mdd,'unknown_orders':unknown,'positions':len(holds),'unknown_sessions':gap,'zero_amount_bars':zero_obs,'issues':','.join(issues),'complete':unknown==0 and not holds and gap==0})
 print(year,mode,month,'unknown',unknown,'open',len(holds),'gaps',gap,'zero',zero_obs,'ret',round((nav/100000-1)*100,2),flush=True)
pd.DataFrame(summary).to_csv(P/f'{year}_{mode}_逐批次.csv',index=False);pd.DataFrame(events).to_pickle(P/f'{year}_{mode}_逐笔.pkl.gz',compression={'method':'gzip','compresslevel':1});pd.DataFrame(equity).to_csv(P/f'{year}_{mode}_每日净值.csv',index=False)
