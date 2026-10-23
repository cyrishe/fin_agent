import sys,json,importlib.util
from pathlib import Path
import pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');sys.path.insert(0,str(R))
for n in ['akshare','requests','py_mini_racer']:
 print('module',n,bool(importlib.util.find_spec(n)))
for path in [Path('/data/cyris/fin_agent_deploy/b2-v2-20261021/assets/reference/four_all_top3.pkl'),R/'minute_demand.pkl',R/'minute_facts.pkl']:
 f=pd.read_pickle(path);print(path.name,f.shape,f.columns.tolist());
 if 'd1_date' in f:print('years',f.d1_date.str[:4].value_counts().sort_index().to_dict())
 if 'reason' in f:print('reason',f.reason.value_counts().to_dict())
s=json.loads((R/'daily_sources.json').read_text());print('daily_years',pd.DataFrame([dict(year=x['month'][:4],rows=x['rows'],days=len(x['dates'])) for x in s]).groupby('year').sum().to_dict('index'))
a=pd.read_csv(R/'minute_extrema_audit.csv',dtype={'symbol':str,'d1_date':str});print('example',a[a.open_close_match.eq(True)].sort_values('max_extreme_gap_pct',ascending=False).head(3).to_dict('records'))
print('calendar2016_17',pd.read_csv(R/'trade_cal.csv').query('is_open == 1').groupby(lambda i:str(pd.read_csv(R/'trade_cal.csv').iloc[i].cal_date)[:4]).size().to_dict())
