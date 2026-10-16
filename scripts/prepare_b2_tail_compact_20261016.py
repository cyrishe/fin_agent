import json,pandas as pd
from pathlib import Path
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.mk_minute_archive import day_file,COMPACT_ROOT
p=Path('outputs/stock_automl/b2_d2_tail_vwap_20261016');raw=json.load(open(p/'raw.json'));missing=pd.DataFrame([{'code':r['code'],'day':r['day']} for r in raw if len(r['bars'])!=240]);parts=[]
for day,g in missing.groupby('day'):
 try:
  path=day_file(COMPACT_ROOT,day);f=filtered_local(path,set(g.code));f['symbol']=f.symbol.str[:6];f['day']=day;f['source']=str(path);parts.append(f)
 except FileNotFoundError:pass
pd.concat(parts).to_pickle(p/'compact.pkl');print('compact rows',sum(len(x) for x in parts))
