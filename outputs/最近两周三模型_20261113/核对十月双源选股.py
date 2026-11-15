from pathlib import Path
import json,joblib,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;F=R/'outputs/D0分时形态研究_20261113';short=json.loads((F/'字段.json').read_text());long=json.loads((F/'远期字段.json').read_text());test=pd.read_pickle(P/'十月三十日全候选.pkl.gz');vendor=pd.read_pickle(P/'研究行情分时因子.pkl.gz')[['qdc_symbol','d0_date']+short];test=test.drop(columns=short).merge(vendor,on=['qdc_symbol','d0_date'],validate='one_to_one');extra=pd.read_pickle(F/'远期形态_含十月.pkl.gz')[['qdc_symbol','d0_date']+long];test=test.merge(extra,on=['qdc_symbol','d0_date'],how='left',validate='one_to_one');names=['每日滚动','每月训练','六训二验']
for name in names:
 if name=='六训二验':
  p=pd.read_pickle(P/'十月原环境评分.pkl.gz');test=test.merge(p,on=['d0_date','symbol'],validate='one_to_one');
 else:
  b=joblib.load(P/(name+'.joblib'));test[name+'_研究行情']=b['model'].predict(test[b['features']].replace([np.inf,-np.inf],np.nan))
 ordered=test.sort_values([name+'_研究行情','symbol'],ascending=[False,True]);test.loc[ordered.index,name+'_研究排名']=np.arange(1,len(test)+1)
test['研究票数']=test[[n+'_研究排名' for n in names]].le(2).sum(axis=1);test['原票数']=test[[n+'排名' for n in names]].le(2).sum(axis=1);test[['symbol','原票数','研究票数']+[n+'排名' for n in names]+[n+'_研究排名' for n in names]].to_csv(P/'十月三十日双源排名.csv',index=False);a=set(test[test.原票数.gt(0)].symbol);b=set(test[test.研究票数.gt(0)].symbol);print('旧来源',sorted(a),'研究行情',sorted(b),'共同',len(a&b),'只原有',sorted(a-b),'只研究',sorted(b-a));print(test[test.原票数.gt(0)|test.研究票数.gt(0)][['symbol','原票数','研究票数']+[n+'排名' for n in names]+[n+'_研究排名' for n in names]].to_string(index=False))
