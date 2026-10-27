"""Sampled OOB fit diagnostics and saved-forest split importance."""
from pathlib import Path
import json
import numpy as np,pandas as pd,joblib
from sklearn.metrics import accuracy_score,log_loss,roc_auc_score
from threadpoolctl import threadpool_limits
from scripts.train_low_base_co26 import splits,labels
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');P=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def metrics(y,p):
 return dict(n=len(y),accuracy=float(accuracy_score(y,np.array([-1,0,1])[p.argmax(axis=1)])),logloss=float(log_loss(y,p,labels=[-1,0,1])),up_auc=float(roc_auc_score(y==1,p[:,2])) if len(np.unique(y==1))==2 else None,down_auc=float(roc_auc_score(y==-1,p[:,0])) if len(np.unique(y==-1))==2 else None)

def main():
 sample=pd.read_parquet(P/'samples.parquet');months=json.loads((R/'complete.json').read_text())['months'];rows=[];importance=[]
 with threadpool_limits(limits=1):
  for m in months:
   phase='validation' if m<'2026-01' else 'test';folder=R/phase/m
   for h in [2,3]:
    train,test=splits(sample,m,h);bundle=joblib.load(folder/f'rf_h{h}.joblib');model=bundle['model'];rng=np.random.default_rng(int(m.replace('-',''))*10+h)
    chosen=np.sort(rng.choice(len(train),size=min(1000,len(train)),replace=False));X=train.iloc[chosen][bundle['features']].to_numpy(float);X[~np.isfinite(X)]=np.nan;X=np.where(np.isnan(X),bundle['median'],X);X=np.clip(X,*bundle['bounds']);yy=labels(train.iloc[chosen][f'net{h}'])
    totals=np.zeros((len(X),3));counts=np.zeros(len(X),int)
    for tree,bag in zip(model.estimators_,model.estimators_samples_):
     oob=~np.isin(chosen,bag);totals[oob]+=tree.predict_proba(X[oob]);counts[oob]+=1
    assert counts.min()>0;pred=totals/counts[:,None]
    a=metrics(yy,pred);a.update(phase=phase,month=m,horizon=h,kind='train_oob_sample',mean_trees=float(counts.mean()),min_trees=int(counts.min())) ;rows.append(a)
    scored=pd.read_parquet(folder/f'rf_h{h}_scores.parquet');scored=scored[scored.net.notna()]
    if phase=='validation':scored=scored[scored.label_end.lt('2026-01-01')]
    scored=scored.sample(n=min(1000,len(scored)),random_state=int(m.replace('-',''))+h);pp=scored[['p_down','p_mid','p_up']].to_numpy();b=metrics(labels(scored.net),pp);b.update(phase=phase,month=m,horizon=h,kind='forward_sample',mean_trees=80.,min_trees=80);rows.append(b)
    importance.extend(dict(phase=phase,month=m,horizon=h,factor=name,split_importance=float(value)) for name,value in zip(bundle['features'],model.feature_importances_))
    print(m,h,flush=True)
 diag=pd.DataFrame(rows);diag.to_csv(D/'fit_diagnostics.csv',index=False);imp=pd.DataFrame(importance);imp.to_csv(D/'rf_feature_importance.csv',index=False)
 sm=diag.groupby(['phase','horizon','kind']).agg(folds=('month','count'),sample_rows=('n','sum'),accuracy=('accuracy','mean'),logloss=('logloss','mean'),up_auc=('up_auc','mean'),down_auc=('down_auc','mean'),oob_trees=('mean_trees','mean')).reset_index();sm.to_csv(D/'fit_diagnostics_summary.csv',index=False)
 ims=imp.groupby(['phase','horizon','factor']).split_importance.agg(folds='count',mean='mean',median='median').reset_index();ims.sort_values(['phase','horizon','mean'],ascending=[True,True,False]).to_csv(D/'rf_feature_importance_summary.csv',index=False);print(sm.to_string(index=False))
if __name__=='__main__':main()
