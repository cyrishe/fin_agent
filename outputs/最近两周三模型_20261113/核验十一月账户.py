from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import json,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;O=P/'十一月独立';e=pd.read_csv(O/'模拟买卖流水.csv',dtype={'symbol':str});lots=pd.read_csv(O/'实际卖法逐笔.csv',dtype={'symbol':str});eq=pd.read_csv(O/'模拟账户每日净值.csv');chosen=pd.read_pickle(P/'十一月每日名单.pkl.gz');assert len(chosen)==len(lots)==42;assert set(zip(chosen.d0_date,chosen.symbol))==set(zip(lots.d0,lots.symbol));b=pd.read_pickle(P/'入选股后续分钟.pkl.gz');b['code']=b.symbol.str[:6];bar=b.set_index(['code','day','minute']);sell=e[e.action.eq('分批卖出')];buy=e[e.action.eq('假设买入')];assert len(buy)==42
v=sell.merge(lots[['d0','symbol','buy_day']],on=['d0','symbol'],validate='many_to_one');assert v.day.gt(v.buy_day).all();assert np.allclose(buy.cashflow,-buy.shares*buy.price*1.001,atol=1e-6)
for r in sell.itertuples():
 z=bar.loc[(r.symbol,r.day,int(r.minute))];assert abs(r.price-z.close)<1e-8 and abs(r.bar_volume-z.volume)<1e-8 and abs(r.cashflow-r.shares*r.price*.999)<1e-6
g=sell.groupby(['day','minute','symbol']).agg(q=('shares','sum'),v=('bar_volume','first'));assert g.q.le(.1*g.v+1e-6).all();cash=100000.
for r in eq.itertuples():cash+=e.loc[e.day.eq(r.日期),'cashflow'].sum();assert abs(cash-r.现金)<1e-5 and abs(r.现金+r.持仓市值-r.净值)<1e-6
daily=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');daily['trade_date']=daily.trade_date.astype(str);daily['symbol']=daily.stk_code.str[:6];pre={(r.symbol,r.trade_date):float(r.preclose) for r in daily.itertuples()};q=pd.read_csv(P/'十一月十三日双源校验.csv',dtype={'symbol':str});pre.update({(r.symbol,'2026-11-13'):float(r.前收) for r in q.itertuples() if pd.notna(r.前收)});st=pd.read_csv(R/'outputs/b2连续两日全量回放_20261113/最新历史ST.csv.gz',dtype=str).fillna({'end_date':'2999-12-31'});sm={s:list(zip(g.begin_date,g.end_date,g.st_type)) for s,g in st.groupby('stk_code')};lm={(r.d0,r.symbol):r for r in lots.itertuples()};checks=0
for (d0,sym),h in sell.groupby(['d0','symbol']):
 lot=lm[(d0,sym)];first=None
 for day,part in b[b.code.eq(sym)&b.day.gt(lot.buy_day)&b.day.le(h.day.max())].groupby('day',sort=True):
  t=[k for a,z,k in sm.get(lot.stk_code,[]) if a<=day<z];assert len(t)==1;rate=.05 if t[0] in ('S','Y') else .2 if sym.startswith(('688','689','300','301')) else .1;cap=float((Decimal(str(pre[(sym,day)]))*Decimal(str(1+rate))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP));found=part[part.volume.gt(0)&part.low.lt(cap-.005)].sort_values('minute')
  if len(found):first=(day,int(found.minute.iloc[0]));break
 assert first is not None
 for r in h.itertuples():assert (r.day,int(r.minute))>first;checks+=1
meta={'信号日':9,'买入':len(buy),'已卖出':int(lots.已卖完.sum()),'未卖出':int((~lots.已卖完).sum()),'卖出成交片段':len(sell),'开板后时间核验':checks,'资金核验天数':len(eq),'说明':'模型为跨来源历史重建；买入和卖出均为价格容量代理，非真实盘口成交'};(O/'核验.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False))
