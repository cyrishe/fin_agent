import json,sys
from pathlib import Path
import numpy as np,pandas as pd
O=Path('/data/cyris/work/b2_data_repair_r95_20261023');R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');sys.path.insert(0,str(R))
frames=[];bars=[];demands=[]
for label,root,prefix in [('r90',R,''),('r60',O,'r60_'),('r41',O,'r41_'),('r35',O,'r35_')]:
 f=pd.read_pickle(root/(prefix+'minute_facts.pkl'));f['source_run']=label;frames.append(f)
 b=pd.read_pickle(root/(prefix+'first10.pkl'));b['source_run']=label;bars.append(b)
 a=pd.read_pickle(root/(prefix+'minute_demand.pkl'));a['source_run']=label;demands.append(a)
facts=pd.concat(frames,ignore_index=True);facts=facts[facts.d1_date.notna()].drop_duplicates(['symbol','d1_date'],keep='last');facts['year']=facts.d1_date.str[:4]
facts['passed']=facts.audit_ok.eq(True);facts['price_conflict']=facts.reason.fillna('').str.contains('价格冲突');facts['missing']=facts.reason.eq('分钟缺失');facts['extrema_relaxed']=facts.extrema_proxy_gap_pct.fillna(0).gt(0)
summary=facts.groupby('year').agg(stock_days=('symbol','size'),passed=('passed','sum'),price_conflict=('price_conflict','sum'),missing=('missing','sum'),relaxed_extrema=('extrema_relaxed','sum')).reset_index();summary.to_csv(O/'minute_quality_by_year.csv',index=False,encoding='utf-8-sig');facts.to_pickle(O/'union_facts.pkl')
b=pd.concat(bars,ignore_index=True);b['time']=b.time.astype(str);b=b.drop_duplicates(['symbol','time'],keep='last').sort_values(['symbol','time']);b['day']=b.time.str[:10];b['year']=b.time.str[:4]
b['bar_index']=b.groupby(['symbol','day']).cumcount();b=b[b.bar_index.lt(4)]
b['flat']=b[['open','high','low','close']].max(axis=1).sub(b[['open','high','low','close']].min(axis=1)).le(1e-8)
barstats=b.groupby('year').agg(bars=('flat','size'),flat_bars=('flat','sum'),flat_fraction=('flat','mean')).reset_index();barstats.to_csv(O/'first4_by_year.csv',index=False,encoding='utf-8-sig')
d=pd.concat(demands,ignore_index=True);d=d[d.d1_date.notna()].drop_duplicates(['symbol','d1_date'],keep='last');d.to_pickle(O/'union_demand.pkl')
print('QUALITY',summary.to_string(index=False),flush=True);print('BARS',barstats.to_string(index=False),flush=True)
sources=json.loads((R/'daily_sources.json').read_text());coverage=pd.DataFrame([dict(year=x['month'][:4],rows=x['rows'],days=len(x['dates'])) for x in sources]).groupby('year').sum().reset_index();coverage.to_csv(O/'daily_coverage.csv',index=False,encoding='utf-8-sig')
sig=pd.read_pickle(R/'signals_exit.pkl');sig=sig[sig.d0_date.ge('2018-01-01')];sig['close_ret']=(sig.d2_close*.999+sig.d2_cash_dividend)/(sig.entry*1.001)-1
rr=[]
for year,g in sig.groupby(sig.d0_date.str[:4]):
 hit=g[g.label_3.eq(True)];rr.append(dict(year=year,shadow_trades=len(g),label_hit=int(g.label_3.sum()),close_win=int(g.close_ret.gt(0).sum()),label_hits_but_close_loss=int(hit.close_ret.lt(0).sum()),label_hits_but_loss_fraction=float(hit.close_ret.lt(0).mean())))
pd.DataFrame(rr).to_csv(O/'label_vs_economics.csv',index=False,encoding='utf-8-sig');print('LABEL',rr,flush=True)
