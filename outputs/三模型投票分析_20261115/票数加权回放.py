from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import json,sys,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=R/'outputs/最近两周三模型_20261113';O=R/'outputs/三模型投票分析_20261115/票数加权账户';O.mkdir(exist_ok=True);nov=pd.read_pickle(P/'十一月每日名单.pkl.gz');oct=pd.read_pickle(P/'十月三十日全候选.pkl.gz');oct=oct[oct[['每日滚动排名','每月训练排名','六训二验排名']].le(2).any(axis=1)].copy();oct['票数']=oct['前二票数'];sigs=nov.copy() if '--十一月' in sys.argv else pd.concat([oct,nov],ignore_index=True);assert (len(sigs),sigs.d0_date.nunique())==((42,9) if '--十一月' in sys.argv else (47,10))
cal=sorted(set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str))|{'2026-11-13'});idx={d:i for i,d in enumerate(cal)};sigs['buy_day']=sigs.d0_date.map(lambda d:cal[idx[d]+1] if idx[d]+1<len(cal) else None);sigs['d2_day']=sigs.d0_date.map(lambda d:cal[idx[d]+2] if idx[d]+2<len(cal) else None)
quotes=pd.read_csv(P/'十一月十三日双源校验.csv',dtype={'symbol':str});sina=pd.read_csv(P/'入选股公开日线.csv',dtype={'stk_code':str});sina13=sina[sina.day.eq('2026-11-13')].copy();q=quotes.merge(sina13,left_on='symbol',right_on=sina13.stk_code.str[:6],how='left',validate='one_to_one');q['校验通过']=q.公开源最新分钟日期.fillna('').str.startswith('2026-11-13') & q.最新价差.le(.011) & (q.开盘-q.open).abs().le(.011) & (q.最新价-q.close).abs().le(.011);names=dict(zip(q.symbol,q.名称));qmap={r.symbol:r for r in q.itertuples()}
daily=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');daily['trade_date']=daily.trade_date.astype(str);daily['symbol']=daily.stk_code.str[:6];daily=daily[daily.trade_date.ge('2026-10-30')];
for c in ['preclose','open','high','low','close','volume','amount']:daily[c]=pd.to_numeric(daily[c],errors='coerce')
quotesmap={(r.symbol,r.trade_date):r for r in daily.itertuples()};st=pd.read_csv(R/'outputs/b2连续两日全量回放_20261113/最新历史ST.csv.gz',dtype=str).fillna({'end_date':'2999-12-31'});sm={s:list(zip(g.begin_date,g.end_date,g.st_type)) for s,g in st.groupby('stk_code')}
bars=pd.read_pickle(P/'入选股后续分钟.pkl.gz');bm={(s,d):g.sort_values('minute') for (s,d),g in bars.groupby(['symbol','day'],sort=False)};expected=list(range(571,691))+list(range(781,901))
def limit(v,m):return float((Decimal(str(v))*Decimal(str(m))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def dq(symbol,day):
 if day!='2026-11-13':
  r=quotesmap.get((symbol,day));return None if r is None else {'preclose':float(r.preclose),'open':float(r.open),'high':float(r.high),'low':float(r.low),'close':float(r.close),'volume':float(r.volume)}
 a=qmap.get(symbol)
 if a is None or not a.校验通过:return None
 return {'preclose':float(a.前收),'open':float(a.开盘),'high':float(a.最高),'low':float(a.最低),'close':float(a.最新价),'volume':float(a.成交量手)*100}
def rates(code,day):
 types=[t for a,z,t in sm.get(code,[]) if a<=day<z]
 if len(types)!=1 or types[0] not in ('N','R','S','Y','L'):return None
 s=code[:6];return .05 if types[0] in ('S','Y') else .2 if s.startswith(('688','689','300','301')) else .1
lots=[];cash=100000.;peak=cash;worst=0.;events=[];equity=[];unknown=[]
for day in [d for d in cal if '2026-11-02'<=d<='2026-11-13']:
 dayrows=sigs[sigs.buy_day.eq(day)].sort_values(['票数','排名均值','symbol'],ascending=[False,True,True]);n=len(dayrows);budget=cash;weightsum=dayrows['票数'].sum()
 for r in dayrows.itertuples():
  allocation=budget*r.票数/weightsum
  quote=dq(r.symbol,day);given=r.d1_open if pd.notna(r.d1_open) else np.nan
  if quote is None or not np.isfinite(quote['open']) or quote['open']<=0:unknown.append({'day':day,'symbol':r.symbol,'reason':'买入日价格未知'});continue
  if np.isfinite(given) and abs(quote['open']/float(given)-1)>.005:unknown.append({'day':day,'symbol':r.symbol,'reason':'买入价双源不符'});continue
  rate=float(r.rate);cap=limit(float(quote['preclose']),1+rate);original=float(r.d1_cap) if pd.notna(r.d1_cap) else cap
  if abs(quote['preclose']-float(r.close))>.011 or abs(cap-original)>.011 or quote['open']>cap+.011:unknown.append({'day':day,'symbol':r.symbol,'reason':'买入日除权或板价未知'});continue
  if allocation<=1e-8:continue
  qty=allocation/(cap*1.001);amount=qty*quote['open']*1.001;cash-=amount;lot={'d0':r.d0_date,'buy_day':day,'symbol':r.symbol,'stk_code':r.stk_code,'qdc_symbol':r.qdc_symbol,'entry':quote['open'],'cap':cap,'qty':qty,'remaining':qty,'cost':amount,'proceeds':0.,'last':quote['open'],'pending':False,'sell_end':None,'issue':None};lots.append(lot);events.append({'day':day,'d0':r.d0_date,'symbol':r.symbol,'action':'假设买入','shares':qty,'price':quote['open'],'cashflow':-amount,'limit':cap})
 for sym in sorted({h['symbol'] for h in lots if h['buy_day']<day and h['remaining']>1e-8 and not h['issue']}):
  group=[h for h in lots if h['symbol']==sym and h['buy_day']<day and h['remaining']>1e-8 and not h['issue']];quote=dq(sym,day);rate=rates(group[0]['stk_code'],day);bar=bm.get((group[0]['qdc_symbol'],day));prev=cal[idx[day]-1];old=dq(sym,prev)
  if quote is None or old is None or rate is None or bar is None or bar.minute.tolist()!=expected:
   for h in group:h['issue']='卖出行情或状态不完整'
   unknown.append({'day':day,'symbol':sym,'reason':'卖出行情或状态不完整'});continue
  if abs(float(quote['preclose'])-float(old['close']))>.011:
   for h in group:h['issue']='持仓除权需核实'
   unknown.append({'day':day,'symbol':sym,'reason':'持仓除权需核实'});continue
  cap=limit(quote['preclose'],1+rate);floor=limit(quote['preclose'],1-rate)
  for b in bar.itertuples():
   if all(h['remaining']<=1e-8 for h in group):break
   if min(b.open,b.high,b.low,b.close)<=0 or b.high+1e-4<max(b.open,b.close,b.low) or b.low-1e-4>min(b.open,b.close,b.high) or b.volume<0 or b.high>cap+.03 or b.low<floor-.03:
    for h in group:h['issue']='分钟价格或涨跌停异常'
    unknown.append({'day':day,'symbol':sym,'minute':b.minute,'reason':'分钟价格或涨跌停异常'});break
   if b.volume<=0:continue
   if b.low>floor+.005 and b.close>floor+.005:
    capacity=b.volume*.10
    for h in sorted(group,key=lambda h:(h['buy_day'],h['d0'])):
     if not h['pending'] or h['remaining']<=1e-8:continue
     qty=min(capacity,h['remaining'])
     if qty<=0:break
     proceeds=qty*b.close*.999;h['remaining']-=qty;h['proceeds']+=proceeds;cash+=proceeds;capacity-=qty;h['sell_end']=day;events.append({'day':day,'minute':b.minute,'d0':h['d0'],'symbol':sym,'action':'分批卖出','shares':qty,'price':b.close,'cashflow':proceeds,'bar_volume':b.volume})
   if b.low<cap-.005:
    for h in group:h['pending']=True
 for h in lots:
  if h['remaining']<=1e-8:continue
  quote=dq(h['symbol'],day)
  if quote is not None and quote['close']>0:h['last']=quote['close']
  elif h['buy_day']<day:h['issue']=h['issue'] or '收盘估值缺失';unknown.append({'day':day,'symbol':h['symbol'],'reason':'收盘估值缺失'})
 nav=cash+sum(h['remaining']*h['last'] for h in lots);peak=max(peak,nav);worst=max(worst,1-nav/peak);equity.append({'日期':day,'现金':cash,'持仓市值':nav-cash,'净值':nav,'收盘回撤%':100*(1-nav/peak),'持仓笔数':sum(h['remaining']>1e-8 for h in lots)})
for h in lots:
 h['名称']=names.get(h['symbol']);h['已卖完']=h['remaining']<=1e-8;h['已卖出金额']=h['proceeds'];h['剩余市值']=h['remaining']*h['last'];h['截至13日含估值收益%']=100*((h['proceeds']+h['remaining']*h['last'])/h['cost']-1);h['实际卖完收益%']=100*(h['proceeds']/h['cost']-1) if h['已卖完'] else np.nan
result=pd.DataFrame(lots);result.to_csv(O/'实际卖法逐笔.csv',index=False);e=pd.DataFrame(events);e.to_csv(O/'模拟买卖流水.csv',index=False);pd.DataFrame(equity).to_csv(O/'模拟账户每日净值.csv',index=False);pd.DataFrame(unknown,columns=['day','symbol','reason','minute']).to_csv(O/'未核实事项.csv',index=False)
if len(e):
 sold=e[e.action.eq('分批卖出')];groups=sold.groupby(['day','minute','symbol']).agg(shares=('shares','sum'),volume=('bar_volume','first'));assert (groups.shares<=groups.volume*.1+1e-6).all();assert (result.remaining.ge(-1e-6)).all();assert abs(100000+e.cashflow.sum()-cash)<1e-5
meta={'期初':100000,'截止':'2026-11-13','现金':cash,'估值净值':nav,'截至当日收益%':100*(nav/100000-1),'最大收盘回撤%':worst*100,'买入笔数':len(result),'已卖完笔数':int(result.已卖完.sum()),'未卖完笔数':int((~result.已卖完).sum()),'待核实记录':len(unknown),'说明':'D1假设开盘买入，信号从D2起，开板下一分钟起卖、每分钟10%容量，13日尚持仓按收盘估值，非已实现盈亏及非真实成交'};(O/'模拟账户概要.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False,indent=2));print(pd.DataFrame(unknown).to_string(index=False))
