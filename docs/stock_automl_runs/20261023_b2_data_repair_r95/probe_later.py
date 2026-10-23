import pandas as pd
from pathlib import Path
O=Path('/data/cyris/work/b2_data_repair_r95_20261023')
for name in ['r35','r41','r60']:
 for suffix in ['minute_facts','minute_demand','first10']:
  path=O/f'{name}_{suffix}.pkl'
  if not path.exists():continue
  f=pd.read_pickle(path);print(path.name,type(f),len(f))
  if isinstance(f,pd.DataFrame):
   print(f.columns.tolist() if suffix=='first10' else '');print('year',f.d1_date.str[:4].value_counts().sort_index().to_dict() if 'd1_date' in f else '')
   if 'reason' in f:print(f.groupby([f.d1_date.str[:4],'reason'],dropna=False).size().to_dict())
  else: print('example',str(list(f.items())[:1])[:1300])
print('raw sample')
import json
f=pd.DataFrame(**{})
obj=json.loads((O/'fresh_minutes/2019-05-24_300313_1.json').read_text());print(pd.DataFrame(obj['rows'],columns=obj['fields']).sort_values('trade_time').head(7).to_string(index=False))
