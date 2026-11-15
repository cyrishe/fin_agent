from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import json, hashlib
import pandas as pd, numpy as np, joblib
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor

R=Path('/Volumes/ext/fin_agent'); P=Path(__file__).parent
B=R/'outputs/b2连续两日全量回放_20261113'; A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package/daily'; F=R/'outputs/D0分时形态研究_20261113'
months=pd.period_range('2022-05','2026-09',freq='M').astype(str).tolist(); months=[m for m in months if not ('2024-07'<=m<='2024-12' or '2025-07'<=m<='2026-06')]
base=json.loads((B/'months/2024-08/manifest.json').read_text())['features']; extra=json.loads((F/'字段.json').read_text()); cols=base+extra
factor=pd.read_pickle(F/'D0分时因子_含2022.pkl.gz')[['qdc_symbol','d0_date']+extra]
parts=[]
for month in pd.period_range('2021-11','2026-09',freq='M').astype(str):
 x=pd.read_pickle(A/month/'test_samples.pkl.gz');x=x[~x.stk_code.astype(str).str.endswith('.BJ')].copy();parts.append(x)
data=pd.concat(parts,ignore_index=True);assert not data.duplicated(['d0_date','symbol']).any()
data['qdc_symbol']=data.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False)
data=data.merge(factor,on=['qdc_symbol','d0_date'],how='left',validate='many_to_one')
ok=data.d1_open.gt(0)&data.d1_volume.gt(0)&data.d2_close.gt(0)&data.d1_close.gt(0)&data.d1_preclose.gt(0)&data.d2_preclose.gt(0)&data.close.gt(0)&data.d1_cap.gt(0)&(data.d1_preclose-data.close).abs().le(.011)&(data.d2_preclose-data.d1_close).abs().le(.011)&(data.d1_open-data.d1_cap).le(.011)
data['ret']=(100*(data.d2_close*.999/(data.d1_open*1.001)-1)).where(ok)
data['utility']=data.ret.clip(-15,20)-.7*(-data.ret-5).clip(lower=0,upper=15)
data['d2_date']=data.d2_date.astype('string')

def run(job):
 kind, day = job; cutoff=pd.Timestamp(day[:7]+'-01') if kind=='月训六个月' else pd.Timestamp(day);begin=str((cutoff-pd.DateOffset(months=6)).date())
 # Monthly runs before first trading day; labels mature strictly before that day.
 # Daily runs after D0 close; D2 close from the same day is available.
 end=(data.d2_date.lt(str(cutoff.date())) if kind=='月训六个月' else data.d2_date.le(day)).fillna(False)
 train=data[(data.d0_date.ge(begin))&(data.d0_date.lt(str(cutoff.date())))&end&data.utility.notna()].sort_values(['d0_date','symbol']).copy()
 assert len(train)>500 and train.d2_date.max() <= day
 weight=1/train.groupby('d0_date').d0_date.transform('size');weight/=weight.mean()
 model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestRegressor(n_estimators=200,max_depth=7,min_samples_leaf=50,max_features=.8,n_jobs=1,random_state=20261013))
 model.fit(train[cols].replace([np.inf,-np.inf],np.nan),train.utility,randomforestregressor__sample_weight=weight)
 if kind=='月训六个月': test=data[data.d0_date.str.startswith(day[:7])].copy()
 else: test=data[data.d0_date.eq(day)].copy()
 test['score']=model.predict(test[cols].replace([np.inf,-np.inf],np.nan));test['方法']=kind
 out=P/'模型'/kind;out.mkdir(parents=True,exist_ok=True)
 modelpath=out/(day+'.joblib');joblib.dump({'model':model,'features':cols,'start':begin,'decision':day,'last_mature_label':train.d2_date.max()},modelpath,compress=3)
 pred=test[['d0_date','symbol','stk_code','ret','utility','score','方法']].copy();pred.to_pickle(out/(day+'.pkl.gz'),compression='gzip')
 record={'方法':kind,'决策日':day,'训练起始D0':begin,'训练末D0':train.d0_date.max(),'最晚标签日':train.d2_date.max(),'训练股次':len(train),'训练交易日':train.d0_date.nunique(),'测试股次':len(test),'模型SHA256':hashlib.sha256(modelpath.read_bytes()).hexdigest()}
 (out/(day+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));print(kind,day,len(train),flush=True);return record

if __name__=='__main__':
 data.to_pickle(P/'全部输入.pkl.gz',compression='gzip')
 jobs=[]
 for month in months:
  days=sorted(data.loc[data.d0_date.str.startswith(month),'d0_date'].unique());jobs.append(('月训六个月',days[0]));jobs.extend(('每日滚动六个月',d) for d in days)
 jobs=[j for j in jobs if not (P/'模型'/j[0]/(j[1]+'.json')).exists()]
 with ProcessPoolExecutor(max_workers=2) as pool:
  for f in as_completed([pool.submit(run,j) for j in jobs]): f.result()
