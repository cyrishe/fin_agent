"""CO28 fixed point-in-time factor transforms and monthly forward comparisons."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse, json, time
import numpy as np
import pandas as pd
import joblib, sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from scripts.train_low_base_co26 import FEATURES, labels, replay
from scripts.research_gentle_rise_gr1 import sha

PREV=Path('outputs/stock_automl/low_base_ml_co26_20261027')
OUT=Path('outputs/stock_automl/low_base_adaptive_co28_20261027')
DOC=Path('docs/stock_automl_runs/20261027_low_base_adaptive_co28')
STATE=['market_return','market_ret5','market_ret20','market_vol20','low_fraction']
RANK=['rank_'+f for f in FEATURES]
VARIANTS={'rank_lr':(6,False,'absolute','lr'), 'state_lr':(6,True,'absolute','lr'), 'rank_rf':(6,False,'absolute','rf'), 'state_rf':(6,True,'absolute','rf'), 'relative_lr':(6,True,'relative','lr'), 'short_lr':(3,True,'absolute','lr'), 'long_lr':(12,True,'absolute','lr')}
DATA=None

def build():
 s=pd.read_parquet(PREV/'samples.parquet');assert len(s)==1260732 and not s.duplicated(['date','stk_code']).any()
 for f in FEATURES:s['rank_'+f]=s.groupby('date',sort=False)[f].rank(pct=True).astype('float32')
 m=pd.read_csv('docs/stock_automl_runs/20261027_low_base_co25/daily_counts.csv').sort_values('date')
 m['market_ret5']=(1+m.market_return).rolling(5,min_periods=5).apply(np.prod,raw=True)-1
 m['market_ret20']=(1+m.market_return).rolling(20,min_periods=20).apply(np.prod,raw=True)-1
 m['market_vol20']=m.market_return.rolling(20,min_periods=20).std()
 m['low_fraction']=m['low']/m.eligible
 assert not m.date.duplicated().any();s=s.merge(m[['date']+STATE],on='date',how='left',validate='many_to_one',sort=False)
 for h in [2,3]:
  pct=s.groupby('date',sort=False)[f'net{h}'].rank(pct=True)
  s[f'relative{h}']=np.where(pct.isna(),np.nan,np.where(pct<=.3,-1,np.where(pct>.7,1,0)))
 assert s.sample_id.is_unique
 OUT.mkdir(parents=True,exist_ok=True);s.to_parquet(OUT/'samples.parquet',index=False)
 (OUT/'build.json').write_text(json.dumps({'source_sha256':sha(PREV/'samples.parquet'),'sample_sha256':sha(OUT/'samples.parquet'),'market_source_sha256':sha(Path('docs/stock_automl_runs/20261027_low_base_co25/daily_counts.csv')),'design_sha256':sha(DOC/'DESIGN.md'),'rows':len(s),'features':FEATURES,'rank':RANK,'state':STATE},indent=2))
 print('built',len(s),flush=True)

def init():
 global DATA
 DATA=pd.read_parquet(OUT/'samples.parquet')

def run(month):
 phase='validation' if month<'2026-01' else 'test';out=OUT/phase/month;out.mkdir(parents=True,exist_ok=True)
 if (out/'complete.json').exists():return month
 start=time.monotonic(); entries=[]
 with threadpool_limits(limits=1):
  for h in (2,3):
   test=DATA.loc[DATA.month.eq(month)].copy();m=pd.Period(month);first=str(m.start_time.date())
   for name,(window,withstate,target,algorithm) in VARIANTS.items():
    cutoff=str((m-window).start_time.date());mask=DATA.date.ge(cutoff)&DATA.date.lt(first)&DATA[f'label_end{h}'].notna()&DATA[f'label_end{h}'].lt(first)&DATA[f'net{h}'].notna()
    train=DATA.loc[mask];assert train[f'label_end{h}'].max()<test.date.min()
    fs=FEATURES+RANK+(STATE if withstate else [])
    x=train[fs].to_numpy(float);t=test[fs].to_numpy(float);x[~np.isfinite(x)]=np.nan;t[~np.isfinite(t)]=np.nan
    med=np.nanmedian(x,axis=0);assert np.isfinite(med).all();x=np.where(np.isnan(x),med,x);t=np.where(np.isnan(t),med,t)
    bounds=np.quantile(x,[.01,.99],axis=0);x=np.clip(x,*bounds);t=np.clip(t,*bounds)
    w=1/train.groupby('date').sample_id.transform('count').to_numpy(float);w/=w.mean()
    y=labels(train[f'net{h}']) if target=='absolute' else train[f'relative{h}'].to_numpy(int)
    if algorithm=='lr':
     scaler=StandardScaler().fit(x,sample_weight=w);x=scaler.transform(x);t=scaler.transform(t);model=LogisticRegression(C=.2,max_iter=400,solver='lbfgs',random_state=42)
    else:
     scaler=None;model=RandomForestClassifier(n_estimators=80,max_depth=7,min_samples_leaf=200,max_features=.7,bootstrap=True,max_samples=.1,random_state=42,n_jobs=1)
    model.fit(x,y,sample_weight=w);prob=model.predict_proba(t);classes=list(model.classes_);assert classes==[-1,0,1]
    bundle={'median':med,'bounds':bounds,'scaler':scaler,'model':model,'features':fs};mp=out/f'{name}_h{h}.joblib';joblib.dump(bundle,mp,compress=3)
    sample=test.sample(n=min(100,len(test)),random_state=42);indices=test.index.get_indexer(sample.index);np.testing.assert_allclose(prob[indices],replay(joblib.load(mp),sample),rtol=0,atol=1e-13)
    z=test[['sample_id','date','stk_code','position60',f'net{h}',f'label_end{h}']].copy();z['score']=prob[:,2]-prob[:,0];z['p_down']=prob[:,0];z['p_up']=prob[:,2];z=z.rename(columns={f'net{h}':'net',f'label_end{h}':'label_end'});sp=out/f'{name}_h{h}_scores.parquet';z.to_parquet(sp,index=False)
    entries.append({'model':name,'horizon':h,'train_start':train.date.min(),'train_end':train.date.max(),'label_end':train[f'label_end{h}'].max(),'train_rows':len(train),'train_days':train.date.nunique(),'test_rows':len(test),'model_sha256':sha(mp),'scores_sha256':sha(sp)})
    print(month,h,name,len(train),round(time.monotonic()-start,1),flush=True)
 (out/'complete.json').write_text(json.dumps({'month':month,'entries':entries,'sample_sha256':sha(OUT/'samples.parquet'),'design_sha256':sha(DOC/'DESIGN.md')},indent=2));return month

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['build','train']);p.add_argument('--workers',type=int,default=2);p.add_argument('--months',nargs='*');a=p.parse_args()
 assert sklearn.__version__=='1.7.1'
 if a.action=='build':build();return
 manifest=json.loads((OUT/'build.json').read_text());assert manifest['sample_sha256']==sha(OUT/'samples.parquet') and manifest['design_sha256']==sha(DOC/'DESIGN.md')
 months=a.months or [str(m) for m in pd.period_range('2025-01','2026-09',freq='M')]
 with ProcessPoolExecutor(max_workers=a.workers,initializer=init) as pool:
  for f in as_completed([pool.submit(run,m) for m in months]):print('complete',f.result(),flush=True)
 if len(months)==21:(OUT/'complete.json').write_text(json.dumps({'months':months,'design_sha256':manifest['design_sha256'],'sample_sha256':manifest['sample_sha256']},indent=2))
if __name__=='__main__':main()
