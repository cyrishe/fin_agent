from pathlib import Path
import json,pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
s=pd.read_pickle(R/'signals_exit.pkl');s=s[s.d0_date.ge('2018-01-01')].copy()
gate=json.loads((R/'overall.json').read_text())['gate']
s['paused']=s.d0_date.str[:7].map(lambda m:gate[m]['paused'])
s['year']=s.d0_date.str[:4]
rows=[]
for year,g in s.groupby('year'):
 a=g[~g.paused]
 rows.append(dict(year=year,shadow_signals=len(g),allowed_trades=len(a),early_exits=int(a.early_sell.sum()),d2_minute_missing=int(a.d2_quality.eq('minute_missing').sum()),d2_close_mismatch=int(a.d2_quality.eq('close_mismatch').sum()),d2_extrema_proxy=int(a.d2_quality.eq('extrema_proxy').sum()),d1_extrema_gap=int(a.extrema_proxy_gap_pct.fillna(0).gt(0).sum()),d2_open_floor=int(a.d2_open_floor.sum()),d2_close_floor=int(a.d2_close_floor.sum()),d2_oneprice_floor=int(a.d2_oneprice_floor.fillna(False).sum()),paused_months=sum(gate[f'{year}-{i:02}']['paused'] for i in range(1,13))))
pd.DataFrame(rows).to_csv(R/'quality_by_year.csv',index=False,encoding='utf-8-sig')
strict=json.loads((R/'rejected_strict_extrema'/'overall.json').read_text())['gate']
flips=[dict(month=m,relaxed_paused=gate[m]['paused'],strict_paused=strict[m]['paused'],relaxed_r5=gate[m]['previous_r5'],relaxed_r10=gate[m]['previous_r10'],strict_r5=strict[m]['previous_r5'],strict_r10=strict[m]['previous_r10']) for m in sorted(gate) if m>='2018-01' and gate[m]['paused']!=strict[m]['paused']]
pd.DataFrame(flips).to_csv(R/'gate_sensitivity.csv',index=False,encoding='utf-8-sig')
issues=s[s.d2_quality.isin(['minute_missing','close_mismatch'])][['d0_date','d1_date','d2_date','symbol','paused','d2_quality','d2_close_gap_pct','d2_daily_close','morning_trigger','early_sell','sell_price']]
issues.to_csv(R/'d2_quality_issues.csv',index=False,encoding='utf-8-sig')
print(pd.DataFrame(rows).to_string(index=False),flush=True)
print('gate flips',len(flips),'issues',len(issues),flush=True)
