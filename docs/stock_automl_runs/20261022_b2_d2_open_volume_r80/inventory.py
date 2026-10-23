from pathlib import Path
import json
import pandas as pd
ROOT=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
x=pd.read_csv('/data/cyris/work/b2_d2_floor_volume_r79_20261022/100笔D2触跌停_成交量逐笔.csv',dtype={'股票':str})
p=pd.read_csv('/data/cyris/work/b2_board_volume_r71_20261022/preboard_daily_source.csv.gz',dtype={'stk_code':str})
keys=x[['股票','首板日']].drop_duplicates()
y=p.merge(keys,left_on=['stk_code','对应首板日'],right_on=['股票','首板日'])
required=sorted(set(y.trade_date))
dirs=[]
for root in ['/data/cyris/work','/data/cyris/fin_agent/outputs/stock_automl']:
 for d in Path(root).rglob('mk_1m_compact*'):
  if d.is_dir():dirs.append(d)
available={}
for year in ['2022','2023','2024']:
 for q in (Path('/data/cyris/tushare_market_data')/year).glob('*.csv.gz'):
  name=q.name[:8]
  available[f'{name[:4]}-{name[4:6]}-{name[6:8]}']=str(q)
for d in dirs:
 for q in d.glob('*.csv.zst'):
  if q.stat().st_size>1000:available.setdefault(q.name[:10],str(q))
missing=[day for day in required if day not in available]
local_relative=[]
for day in missing:
 suffix=f'mk_1m_compact_{day[:4]}' if day[:4] in ('2024','2025') else 'mk_1m_compact'
 local_relative.append(f'{suffix}/{day}.csv.zst')
y.to_csv(ROOT/'preboard_daily_selected.csv.gz',index=False,compression='gzip')
(ROOT/'missing_archive_relative_paths.txt').write_text(chr(10).join(local_relative)+chr(10))
(ROOT/'inventory.json').write_text(json.dumps({'sample_trades':len(x),'unique_preboard_pairs':len(keys),'preboard_rows':len(y),'required_dates':len(required),'server_available_dates':len(required)-len(missing),'missing_dates':missing,'server_source_paths':{day:available[day] for day in required if day in available}},ensure_ascii=False,indent=2)+chr(10))
print('sample',len(x),'unique preboard pairs',len(keys),'rows',len(y),'unique dates',len(required),'missing',len(missing))
print('missing by year',pd.Series([d[:4] for d in missing]).value_counts().to_dict())
