"""CO26 chronological six-month training, validation lock and next-month test."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse, hashlib, json, os
import numpy as np
import pandas as pd
import joblib, sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from scripts.build_low_base_co26 import CORE,EXTRA
from scripts.research_gentle_rise_gr1 import sha

ROOT=Path('outputs/stock_automl/low_base_ml_co26_20261027');DOC=Path('docs/stock_automl_runs/20261027_low_base_ml_co26');FEATURES=CORE+EXTRA
MODELS=['logistic','hgb'];DATA=None

def init_worker():
 global DATA
 DATA=pd.read_parquet(ROOT/'samples.parquet')

def labels(net):
 v=np.round(np.asarray(net,dtype=float),10);return np.where(v<-.02,-1,np.where(v>.02,1,0))

def splits(df,month,horizon):
 m=pd.Period(month); start=str(m.start_time.date());first=str((m-6).start_time.date())
 mask=df.date.ge(first)&df.date.lt(start)&df[f'label_end{horizon}'].notna()&df[f'label_end{horizon}'].lt(start)&df[f'net{horizon}'].notna()
 tr=df.loc[mask].copy();te=df[df.month.eq(month)].copy()
 if tr.empty or te.empty:raise ValueError(f'empty {month} horizon={horizon}')
 assert tr[f'label_end{horizon}'].max()<te.date.min() and tr.date.max()<te.date.min()
 return tr,te

def fit_one(train,test,horizon,name):
 X=train[FEATURES].to_numpy(dtype=float);T=test[FEATURES].to_numpy(dtype=float)
 X[~np.isfinite(X)]=np.nan;T[~np.isfinite(T)]=np.nan
 median=np.nanmedian(X,axis=0);assert np.isfinite(median).all()
 X=np.where(np.isnan(X),median,X);T=np.where(np.isnan(T),median,T)
 bounds=np.quantile(X,[.01,.99],axis=0);X=np.clip(X,*bounds);T=np.clip(T,*bounds)
 weights=1/train.groupby('date').sample_id.transform('count').to_numpy(dtype=float);weights/=weights.mean()
 y=labels(train[f'net{horizon}']);assert set(np.unique(y))=={-1,0,1}
 if name=='logistic':
  scaler=StandardScaler().fit(X,sample_weight=weights);X=scaler.transform(X);T=scaler.transform(T)
  model=LogisticRegression(C=.2,max_iter=400,solver='lbfgs',random_state=42)
 else:
  scaler=None;model=HistGradientBoostingClassifier(max_iter=60,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=200,l2_regularization=10,early_stopping=False,random_state=42)
 model.fit(X,y,sample_weight=weights)
 prob=model.predict_proba(T)
 return dict(median=median,bounds=bounds,scaler=scaler,model=model,features=FEATURES),prob

def replay(bundle,test):
 T=test[bundle['features']].to_numpy(dtype=float);T[~np.isfinite(T)]=np.nan
 T=np.where(np.isnan(T),bundle['median'],T);T=np.clip(T,*bundle['bounds'])
 if bundle['scaler'] is not None:T=bundle['scaler'].transform(T)
 return bundle['model'].predict_proba(T)

def run_month(arg):
 phase,month,chosen=arg;out=ROOT/phase/month;out.mkdir(parents=True,exist_ok=True)
 if (out/'complete.json').exists():return json.loads((out/'complete.json').read_text())
 info=[]
 with threadpool_limits(limits=1):
  for h in [2,3]:
   tr,te=splits(DATA,month,h); y=labels(tr[f'net{h}']);mem=out/f'members_h{h}.npz'
   np.savez_compressed(mem,sample_id=tr.sample_id.to_numpy(),label=y)
   counts={str(k):int((y==k).sum()) for k in [-1,0,1]}
   for name in (MODELS if phase=='validation' else [chosen[str(h)]]):
    bundle,prob=fit_one(tr,te,h,name);modelpath=out/f'{name}_h{h}.joblib';joblib.dump(bundle,modelpath,compress=3)
    np.testing.assert_allclose(prob,replay(joblib.load(modelpath),te),atol=1e-14,rtol=0)
    cl=list(bundle['model'].classes_);z=te[['sample_id','date','stk_code','position60',f'net{h}',f'label_end{h}']].copy()
    for c,key in [(-1,'p_down'),(0,'p_mid'),(1,'p_up')]:z[key]=prob[:,cl.index(c)]
    z['score']=z.p_up-z.p_down;z=z.rename(columns={f'net{h}':'net',f'label_end{h}':'label_end'})
    scorepath=out/f'{name}_h{h}_scores.parquet';z.to_parquet(scorepath,index=False)
    info.append(dict(horizon=h,model=name,train_start=tr.date.min(),train_end=tr.date.max(),label_end=tr[f'label_end{h}'].max(),train_rows=len(tr),train_days=tr.date.nunique(),class_counts=counts,test_candidates=len(te),test_days=te.date.nunique(),member_sha256=sha(mem),model_sha256=sha(modelpath),scores_sha256=sha(scorepath),readback_verified=True))
 result=dict(phase=phase,month=month,folds=info,source_sha256=sha(ROOT/'samples.parquet'),design_sha256=sha(DOC/'DESIGN.md'))
 (out/'complete.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

def main():
 ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['validation','test']);ap.add_argument('--workers',type=int,default=2);a=ap.parse_args()
 if sklearn.__version__!='1.7.1':raise ValueError('Require scikit-learn 1.7.1')
 build=json.loads((ROOT/'build_manifest.json').read_text());assert build['sample_sha256']==sha(ROOT/'samples.parquet')
 if a.phase=='validation':months=[str(p) for p in pd.period_range('2025-01','2025-12',freq='M')];chosen={}
 else:
  sel=json.loads((ROOT/'selection.json').read_text());assert sel['design_sha256']==sha(DOC/'DESIGN.md') and sel['samples_sha256']==build['sample_sha256']
  chosen=sel['selected_models'];months=[str(p) for p in pd.period_range('2026-01','2026-09',freq='M')]
 with ProcessPoolExecutor(max_workers=a.workers,initializer=init_worker) as pool:
  for i,f in enumerate(as_completed([pool.submit(run_month,(a.phase,m,chosen)) for m in months]),1):
   r=f.result();assert r['source_sha256']==build['sample_sha256'] and r['design_sha256']==sha(DOC/'DESIGN.md');print(a.phase,i,len(months),r['month'],flush=True)
 (ROOT/f'{a.phase}_complete.json').write_text(json.dumps(dict(months=months,samples_sha256=build['sample_sha256'],design_sha256=sha(DOC/'DESIGN.md')),indent=2))
if __name__=='__main__':main()
