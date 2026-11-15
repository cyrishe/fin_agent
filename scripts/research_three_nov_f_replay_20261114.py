from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import json, pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=R/'outputs/最近两周三模型_20261113';O=P/'十一月F风控_20261114';x=pd.read_pickle(O/'候选风险分数.pkl.gz');cal=sorted(set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str))|{'2026-11-13'});idx={d:i for i,d in enumerate(cal)}; quotes=pd.read_csv(P/'十一月十三日双源校验.csv',dtype={'symbol':str});sina=pd.read_csv(P/'入选股公开日线.csv',dtype={'stk_code':str});q=quotes.merge(sina[sina.day.eq('2026-11-13')],left_on='symbol',right_on=sina[sina.day.eq('2026-11-13')].stk_code.str[:6],how='left',validate='one_to_one');q['校验通过']=q.公开源最新分钟日期.fillna('').str.startswith('2026-11-13')&q.最新价差.le(.011)&(q.开盘-q.open).abs().le(.011)&(q.最新价-q.close).abs().le(.011);qmap={r.symbol:r for r in q.itertuples()};daily=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');daily['trade_date']=daily.trade_date.astype(str);daily['symbol']=daily.stk_code.str[:6];daily=daily[daily.trade_date.ge('2026-10-30')];quotesmap={(r.symbol,r.trade_date):r for r in daily.itertuples()};st=pd.read_csv(R/'outputs/b2连续两日全量回放_20261113/最新历史ST.csv.gz',dtype=str).fillna({'end_date':'2999-12-31'});sm={s:list(zip(g.begin_date,g.end_date,g.st_type)) for s,g in st.groupby('stk_code')};bars=pd.read_pickle(P/'入选股后续分钟.pkl.gz');bm={(s,d):g.sort_values('minute') for (s,d),g in bars.groupby(['symbol','day'],sort=False)};expected=list(range(571,691))+list(range(781,901))
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
 return .05 if types[0] in ('S','Y') else .2 if code[:6].startswith(('688','689','300','301')) else .1
out=[];events=[];K=100000
for r in x.itertuples(index=False):
 day=r.d0_date;key=idx[day];buyday=cal[key+1] if key+1<len(cal) else None; record={'d0_date':day,'symbol':r.symbol,'名称':r.股票名称,'选中池命中':r.选中池命中,'全池命中':r.全池命中,'排除':r.排除,'buy_day':buyday,'D1买入价':np.nan,'D1收盘':np.nan,'D2日期':cal[key+2] if key+2<len(cal) else None,'卖完日期':None,'原策略收益%':np.nan,'截至13日估值收益%':np.nan,'状态':None,'问题':None}
 if buyday is None:record['状态']='D1未发生';out.append(record);continue
 quote=dq(r.symbol,buyday);orig=float(r.d1_open) if pd.notna(r.d1_open) else np.nan
 if quote is None or not np.isfinite(quote['open']) or quote['open']<=0:record['状态']='数据未知';record['问题']='买入日价格未知';out.append(record);continue
 if np.isfinite(orig) and abs(quote['open']/orig-1)>.005:record['状态']='数据未知';record['问题']='买入价双源不符';out.append(record);continue
 rate=float(r.rate);cap=limit(float(quote['preclose']),1+rate);origcap=float(r.d1_cap) if pd.notna(r.d1_cap) else cap
 if abs(quote['preclose']-float(r.close))>.011 or abs(cap-origcap)>.011 or quote['open']>cap+.011:record['状态']='数据未知';record['问题']='买入日除权或板价未知';out.append(record);continue
 shares=K/(quote['open']*1.001);rem=shares;cash=0.;pending=False;last=quote['close'];record.update({'D1买入价':quote['open'],'D1收盘':quote['close'],'状态':'持有中'});entry=quote['open']
 for i in range(key+2,len(cal)):
  sday=cal[i];qday=dq(r.symbol,sday);old=dq(r.symbol,cal[i-1]);rate=rates(r.stk_code,sday);bar=bm.get((r.qdc_symbol,sday))
  if qday is None or old is None or rate is None or bar is None or bar.minute.tolist()!=expected:record['状态']='数据未知';record['问题']='卖出行情或状态不完整';break
  if abs(qday['preclose']-old['close'])>.011:record['状态']='数据未知';record['问题']='持仓除权需核实';break
  cap=limit(qday['preclose'],1+rate);floor=limit(qday['preclose'],1-rate)
  for b in bar.itertuples():
   if min(b.open,b.high,b.low,b.close)<=0 or b.high+1e-4<max(b.open,b.close,b.low) or b.low-1e-4>min(b.open,b.close,b.high) or b.volume<0 or b.high>cap+.03 or b.low<floor-.03:record['状态']='数据未知';record['问题']='分钟价格或涨跌停异常';break
   if b.volume<=0:continue
   if pending and b.low>floor+.005 and b.close>floor+.005:
    qty=min(b.volume*.1,rem);rem-=qty;cash+=qty*b.close*.999;events.append({'d0_date':day,'symbol':r.symbol,'day':sday,'minute':b.minute,'price':b.close,'qty':qty,'bar_volume':b.volume,'fee_cash':qty*b.close*.999});record['卖完日期']=sday
    if rem<=1e-8:record['状态']='已卖完';record['原策略收益%']=100*(cash/K-1);break
   if b.low<cap-.005:pending=True
  last=qday['close']
  if record['状态'] in ('数据未知','已卖完'):break
 if record['状态']=='持有中':record['截至13日估值收益%']=100*((cash+rem*last*.999)/K-1)
 out.append(record)
z=pd.DataFrame(out);z.to_csv(O/'原名单与过滤逐笔.csv',index=False,encoding='utf-8-sig');e=pd.DataFrame(events);e.to_csv(O/'固定10万卖出分钟.csv',index=False,encoding='utf-8-sig');assert (e.qty<=e.bar_volume*.1+1e-7).all();assert (e.day>e.d0_date).all();tot=[]
for name,g in [('原策略',z),('任一F排除',z[~z.排除]),('被排除',z[z.排除])]:
 for day,v in [('全期',g)]+list(g.groupby('d0_date',sort=True)):
  done=v[v.状态.eq('已卖完')];ret=done['原策略收益%'];tot.append({'方案':name,'D0':day,'候选':len(v),'已卖完':len(done),'持有中':int(v.状态.eq('持有中').sum()),'D1未发生':int(v.状态.eq('D1未发生').sum()),'数据未知':int(v.状态.eq('数据未知').sum()),'盈利笔数':int((ret>0).sum()),'亏损笔数':int((ret<0).sum()),'平均收益%':ret.mean(),'亏超7':int((ret< -7).sum()),'已实现收益合计元':float(ret.sum()*1000)})
pd.DataFrame(tot).to_csv(O/'逐日和全期汇总.csv',index=False,encoding='utf-8-sig');print(z.to_string(index=False));print(pd.DataFrame(tot).query("D0=='全期'").to_string(index=False));print('售出片段',len(e))
