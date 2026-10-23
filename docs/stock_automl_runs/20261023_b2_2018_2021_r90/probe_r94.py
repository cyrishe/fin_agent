import sys,json
from pathlib import Path
import pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');sys.path.insert(0,str(R))
for name in ['enriched.pkl','stage_samples.pkl']:
 f=pd.read_pickle(R/name);print(name,f.shape, f.columns.tolist());print(f[['model_month','part']].value_counts().head() if 'part' in f else '')
for name in ['rules_frozen.json','rejected_strict_extrema/rules_frozen.json','overall.json']:
 v=json.loads((R/name).read_text());print(name,str(v)[:1100])
f=pd.read_pickle(R/'daily2/2018-01/training_samples.pkl');print('fit',f.shape, f.columns.tolist())
from scripts.benchmark_automl_1440_inference import db_connection,query
with db_connection(Path('/data/cyris/fin_agent/.env')) as c:
 print('index_schema',query(c,'SHOW COLUMNS FROM kcrp_index_price',()))
 print('index_coverage',query(c,"SELECT idx_code,MIN(trade_date) lo,MAX(trade_date) hi,COUNT(*) n FROM kcrp_index_price WHERE idx_code IN ('000001.SH','000300.SH','000905.SH','000852.SH','399006.SZ') AND trade_date BETWEEN '2017-12-01' AND '2021-12-31' GROUP BY idx_code",()))
 c.rollback()
