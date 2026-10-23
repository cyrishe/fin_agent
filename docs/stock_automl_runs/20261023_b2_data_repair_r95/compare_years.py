import sys,json
from pathlib import Path
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_data_repair_r95_20261023');sys.path.insert(0,str(R))
def save(f,n):f.to_csv(O/(n+'.csv'),index=False,encoding='utf-8-sig')
f=pd.read_pickle(R/'minute_facts.pkl');d=pd.read_pickle(R/'minute_demand.pkl').drop_duplicates(['symbol','d1_date']);f=f.merge(d[['symbol','d1_date','d1_preclose','d1_volume']],on=['symbol','d1_date'],validate='one_to_one',suffixes=('','_ref'))
f['year']=f.d1_date.str[:4];f['period']=np.where(f.d1_date.lt('2021-04-01'),'2021Q1','2021Q2-Q4')
f['true_gap']=f.extrema_proxy_gap_pct.fillna(0).mul(f.d1_preclose).gt(.011)
f['zero_volume']=f.d1_volume.fillna(0).le(0);f['price_conflict']=f.reason.fillna('').str.contains('价格冲突')
y=f.groupby('year').agg(candidate_days=('symbol','size'),extrema_over_tolerance=('true_gap','sum'),price_conflicts=('price_conflict','sum'),zero_volume_days=('zero_volume','sum')).reset_index();y['extrema_fraction']=y.extrema_over_tolerance/y.candidate_days
tr=pd.read_csv(O/'training_exposure_corrected.csv');tr['year']=tr.month.str[:4];t=tr.groupby(['year','part'])[['used','over_tolerance']].sum().unstack();t.columns=['_'.join(k) for k in t.columns];y=y.merge(t,on='year',how='left')
for c in ['train','valid']:y['fraction_'+c]=y['over_tolerance_'+c]/y['used_'+c]
base=pd.read_csv(R/'primary_model_year.csv',dtype={'year':str});strict=pd.read_csv(R/'rejected_strict_extrema/primary_model_year.csv',dtype={'year':str})
for label,s in [('base',base),('strict',strict)]:
 for scenario in ['pause_7pct','V2_pause_close','shadow_close']:
  a=s[s.scenario.eq(scenario)][['year','return_']].rename(columns={'return_':label+'_'+scenario});y=y.merge(a,on='year',how='left')
save(y,'year_quality_and_results');print('YEAR',y.to_string(index=False),flush=True)
z=f[f.year.eq('2021')].groupby('period').agg(candidate_days=('symbol','size'),extrema_over_tolerance=('true_gap','sum'),price_conflicts=('price_conflict','sum'),zero_volume_days=('zero_volume','sum')).reset_index();z['extrema_fraction']=z.extrema_over_tolerance/z.candidate_days;save(z,'2021_quality_period');print('2021',z.to_string(index=False),flush=True)
m=f[f.year.eq('2021')].assign(month=f[f.year.eq('2021')].d1_date.str[:7]).groupby('month').agg(candidate_days=('symbol','size'),extrema_over_tolerance=('true_gap','sum')).reset_index();save(m,'2021_quality_monthly')
# Quarterly returns: independent starts, D0 attribution, original monthly gates retained.
from scripts.replay_limit_rebound_cash_r23 import replay
cal=json.loads((R/'calendar.json').read_text());out=[]
for label,root in [('base',R),('strict',R/'rejected_strict_extrema')]:
 s=pd.read_pickle(root/'signals_exit.pkl');gates=json.loads((root/'overall.json').read_text())['gate']
 for lo,hi,name in [('2021-01-01','2021-04-01','2021Q1'),('2021-04-01','2022-01-01','2021Q2-Q4')]:
  a=s[s.d0_date.ge(lo)&s.d0_date.lt(hi)].copy();a=a[a.d0_date.str[:7].map(lambda m:not gates[m]['paused'])]
  for scenario in ['close','7pct']:
   b=a.assign(d2_close=a.sell_price) if scenario=='7pct' else a
   v,_,_,_,bad=replay(b,cal,start=lo,end=max(b.d2_date) if len(b) else lo,initial=1e6,fee_rate=.001)
   assert not bad
   out.append(dict(version=label,period=name,scenario=scenario,trades=len(b),return_=v['total_return'],win=v['closed_win_rate']))
save(pd.DataFrame(out),'2021_period_results');print('PERIOD_RET',out,flush=True)
# No-trade response receipts: primary endpoint did not authorize suspension data.
r=[]
for line in (O/'check_daily_and_zero.log').read_text().splitlines():
 if line.startswith('SUSPEND '):r.append(json.loads(line[8:]))
(O/'suspension_probe.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
# Earlier database query returned zero rows: preserve explicit schema and coverage, not a blank CSV.
save(pd.DataFrame([dict(year=y,rows_n=0,days=0,source='kcrp_stock_price read-only query, trade_date < 2018-01-01') for y in [2016,2017]]),'database_pre2018_coverage')
