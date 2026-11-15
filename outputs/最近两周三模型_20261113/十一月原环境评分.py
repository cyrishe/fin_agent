from pathlib import Path
import json,joblib,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;F=R/'outputs/D0分时形态研究_20261113';B=R/'outputs/b2连续两日全量回放_20261113';meta=json.loads((P/'十一月验证择优.json').read_text());choice=meta['选定'];x=pd.read_pickle(B/'近期输入/2026-11/test_samples.pkl.gz');x=x[~x.stk_code.str.endswith('.BJ')].copy();x['qdc_symbol']=x.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False);short=json.loads((F/'字段.json').read_text());long=json.loads((F/'远期字段.json').read_text());x=x.merge(pd.read_pickle(P/'研究行情分时因子.pkl.gz')[['qdc_symbol','d0_date']+short],on=['qdc_symbol','d0_date'],how='left',validate='many_to_one');x=x.merge(pd.read_pickle(P/'十一月远期形态.pkl.gz')[['qdc_symbol','d0_date']+long],on=['qdc_symbol','d0_date'],how='left',validate='many_to_one')
if choice=='原连板模型':
 p=pd.read_csv(B/'months/2026-11/predictions.csv.gz',dtype={'symbol':str});p=p[p.stage.eq('test')][['d0_date','symbol','new_score']].rename(columns={'new_score':'六训二验'});z=x[['d0_date','symbol']].merge(p,on=['d0_date','symbol'],validate='one_to_one')
else:
 b=joblib.load(P/(choice+'.joblib'));x['六训二验']=b['model'].predict(x[b['features']].replace([np.inf,-np.inf],np.nan));z=x[['d0_date','symbol','六训二验']]
z.to_pickle(P/'十一月原环境评分.pkl.gz',compression='gzip');print('六训二验评分',choice,len(z))
