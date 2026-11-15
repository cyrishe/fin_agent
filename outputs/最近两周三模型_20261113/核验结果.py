from pathlib import Path
import json,hashlib,pandas as pd,numpy as np
from decimal import Decimal,ROUND_HALF_UP
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;events=pd.read_csv(P/'模拟买卖流水.csv',dtype={'symbol':str});lots=pd.read_csv(P/'实际卖法逐笔.csv',dtype={'symbol':str});equity=pd.read_csv(P/'模拟账户每日净值.csv');chosen=pd.read_csv(P/'每日选股和结果.csv',dtype={'symbol':str});bars=pd.read_pickle(P/'入选股后续分钟.pkl.gz');bars['code']=bars.symbol.str[:6];b=bars.set_index(['code','day','minute']);assert not b.index.duplicated().any();buys=events[events.action.eq('假设买入')];sales=events[events.action.eq('分批卖出')];assert len(buys)==len(lots)==47 and len(chosen)==47;assert not chosen.duplicated(['d0_date','symbol']).any()
joined=sales.merge(lots[['d0','symbol','buy_day']],on=['d0','symbol'],validate='many_to_one');assert joined.day.gt(joined.buy_day).all()
for r in sales.itertuples():
 bar=b.loc[(r.symbol,r.day,int(r.minute))];assert abs(r.price-float(bar.close))<1e-8 and abs(r.bar_volume-float(bar.volume))<1e-8;assert abs(r.cashflow-r.shares*r.price*.999)<1e-6
group=sales.groupby(['day','minute','symbol']).agg(n=('shares','sum'),v=('bar_volume','first'));assert (group.n.le(group.v*.1+1e-6)).all();assert np.allclose(buys.cashflow,-buys.shares*buys.price*1.001,atol=1e-6)
daily=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');daily['trade_date']=daily.trade_date.astype(str);daily['symbol']=daily.stk_code.str[:6];daily=daily[daily.trade_date.ge('2026-11-02')];pre={(r.symbol,r.trade_date):float(r.preclose) for r in daily.itertuples()};q=pd.read_csv(P/'十一月十三日双源校验.csv',dtype={'symbol':str});pre.update({(r.symbol,'2026-11-13'):float(r.前收) for r in q.itertuples() if pd.notna(r.前收)})
st=pd.read_csv(R/'outputs/b2连续两日全量回放_20261113/最新历史ST.csv.gz',dtype=str).fillna({'end_date':'2999-12-31'});sm={s:list(zip(g.begin_date,g.end_date,g.st_type)) for s,g in st.groupby('stk_code')};lotmap={(r.d0,r.symbol):r for r in lots.itertuples()};triggers=0
for (d0,symbol),g in sales.groupby(['d0','symbol']):
 lot=lotmap[(d0,symbol)];first=None
 for day,daybars in bars[(bars.code.eq(symbol))&bars.day.gt(lot.buy_day)&bars.day.le(g.day.max())].groupby('day',sort=True):
  kinds=[t for a,z,t in sm.get(lot.stk_code,[]) if a<=day<z];assert len(kinds)==1;rate=.05 if kinds[0] in ('S','Y') else .2 if symbol.startswith(('688','689','300','301')) else .1;cap=float((Decimal(str(pre[(symbol,day)]))*Decimal(str(1+rate))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP));opening=daybars[(daybars.volume.gt(0))&(daybars.low.lt(cap-.005))].sort_values('minute')
  if len(opening):first=(day,int(opening.minute.iloc[0]));break
 assert first is not None
 for r in g.itertuples():assert (r.day,int(r.minute))>first;triggers+=1
cash=100000.
for r in equity.itertuples():
 cash+=events.loc[events.day.eq(r.日期),'cashflow'].sum();assert abs(cash-r.现金)<1e-5;assert abs(r.现金+r.持仓市值-r.净值)<1e-6
assert abs(cash-json.loads((P/'模拟账户概要.json').read_text())['现金'])<1e-5
for p in sorted((P/'十一月模型').glob('*/*.json')):
 j=json.loads(p.read_text());assert hashlib.sha256(p.with_suffix('.joblib').read_bytes()).hexdigest()==j['模型SHA256'];assert j['最晚标签日']<=j['决策']
v=pd.read_csv(P/'入选股日线双源差异.csv');assert all(v[c+'_差'].le(.011).all() for c in ['open','high','low','close']);h={f:hashlib.sha256((P/f).read_bytes()).hexdigest() for f in ['每日选股和结果.csv','模拟买卖流水.csv','实际卖法逐笔.csv','模拟账户每日净值.csv','十一月验证择优.json']};meta={'信号天数':10,'选股笔数':len(chosen),'日线双源股票日':len(v),'分批卖出记录':len(sales),'开板后卖出核验记录':triggers,'卖量分组':len(group),'现金天数':len(equity),'训练窗口':len(list((P/'十一月模型').glob('*/*.json'))),'十月六训二验旧来源47个分数': '原环境重载逐个完全一致','十月两来源组合名单':'5只全部一致','材料SHA256':h,'范围':'核对资金、报价、T+1、开板后、容量、模型训练截止；真实盘口与成交不能由分钟K证明'};(P/'核验.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False,indent=2))
