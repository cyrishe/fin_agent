"""CO27 RF, exactly same CO26 folds and available training members."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse,json,time
import numpy as np,pandas as pd,joblib,sklearn
from sklearn.ensemble import RandomForestClassifier
from threadpoolctl import threadpool_limits
from scripts.train_low_base_co26 import FEATURES,labels,splits,replay
from scripts.research_gentle_rise_gr1 import sha
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');PREV=Path('outputs/stock_automl/low_base_ml_co26_20261027');DOC=Path('docs/stock_automl_runs/20261027_low_base_rf_co27');DATA=None

def init():
 global DATA
 DATA=pd.read_parquet(PREV/'samples.parquet')

def fit(train,test,h):
 X=train[FEATURES].to_numpy(float);T=test[FEATURES].to_numpy(float);X[~np.isfinite(X)]=np.nan;T[~np.isfinite(T)]=np.nan
 med=np.nanmedian(X,axis=0);assert np.isfinite(med).all();X=np.where(np.isnan(X),med,X);T=np.where(np.isnan(T),med,T)
 bounds=np.quantile(X,[.01,.99],axis=0);X=np.clip(X,*bounds);T=np.clip(T,*bounds)
 weight=1/train.groupby('date').sample_id.transform('count').to_numpy(float);weight/=weight.mean()
 model=RandomForestClassifier(n_estimators=80,max_depth=7,min_samples_leaf=200,max_features=.7,bootstrap=True,max_samples=.1,random_state=42,n_jobs=1)
 model.fit(X,labels(train[f'net{h}']),sample_weight=weight)
 b=dict(median=med,bounds=bounds,scaler=None,model=model,features=FEATURES);return b,model.predict_proba(T)

def run(month):
 start=time.monotonic();phase='validation' if month<'2026-01' else 'test';out=R/phase/month;out.mkdir(parents=True,exist_ok=True)
 if (out/'complete.json').exists():return json.loads((out/'complete.json').read_text())
 info=[]
 with threadpool_limits(limits=1):
  for h in [2,3]:
   tr,te=splits(DATA,month,h);previous=PREV/phase/month/f'members_h{h}.npz';old=np.load(previous);np.testing.assert_array_equal(tr.sample_id.to_numpy(),old['sample_id']);np.testing.assert_array_equal(labels(tr[f'net{h}']),old['label'])
   b,p=fit(tr,te,h);mp=out/f'rf_h{h}.joblib';joblib.dump(b,mp,compress=3);np.testing.assert_allclose(p,replay(joblib.load(mp),te),atol=1e-14,rtol=0)
   z=te[['sample_id','date','stk_code','position60',f'net{h}',f'label_end{h}']].copy();cl=list(b['model'].classes_)
   for c,k in [(-1,'p_down'),(0,'p_mid'),(1,'p_up')]:z[k]=p[:,cl.index(c)]
   z['score']=z.p_up-z.p_down;z=z.rename(columns={f'net{h}':'net',f'label_end{h}':'label_end'});sp=out/f'rf_h{h}_scores.parquet';z.to_parquet(sp,index=False)
   info.append(dict(horizon=h,train_start=tr.date.min(),train_end=tr.date.max(),label_end=tr[f'label_end{h}'].max(),train_rows=len(tr),train_days=tr.date.nunique(),class_counts={str(k):int((old['label']==k).sum()) for k in [-1,0,1]},test_candidates=len(te),test_days=te.date.nunique(),member_sha256=sha(previous),model_sha256=sha(mp),scores_sha256=sha(sp),readback_verified=True))
 result=dict(phase=phase,month=month,folds=info,source_sha256=sha(PREV/'samples.parquet'),design_sha256=sha(DOC/'DESIGN.md'),seconds=round(time.monotonic()-start,2))
 (out/'complete.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=3);p.add_argument('--months',nargs='*');a=p.parse_args()
 assert sklearn.__version__=='1.7.1';source=json.loads((PREV/'build_manifest.json').read_text());assert source['sample_sha256']==sha(PREV/'samples.parquet')
 months=a.months or [str(m) for m in pd.period_range('2025-01','2026-09',freq='M')]
 with ProcessPoolExecutor(max_workers=a.workers,initializer=init) as pool:
  for i,f in enumerate(as_completed([pool.submit(run,m) for m in months]),1):
   x=f.result();assert x['source_sha256']==source['sample_sha256'] and x['design_sha256']==sha(DOC/'DESIGN.md');print(i,len(months),x['month'],x['seconds'],flush=True)
 if len(months)==21:(R/'complete.json').write_text(json.dumps(dict(months=months,source_sha256=source['sample_sha256'],design_sha256=sha(DOC/'DESIGN.md')),indent=2))
if __name__=='__main__':main()
