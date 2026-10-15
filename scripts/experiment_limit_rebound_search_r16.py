"""R16: predeclared algorithm/sampling search with causal monthly validation.

Each family has 3 parameter sets x 4 sampling/weighting schemes x 2 seeds.
Paired seeds are averaged before ranking; test results never select a model.
"""
from __future__ import annotations
import argparse, json, os, time, warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import joblib, numpy as np, pandas as pd, sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier
import lightgbm, xgboost
warnings.filterwarnings('ignore',message='X does not have valid feature names, but LGBMClassifier was fitted with feature names',category=UserWarning)
from scripts.experiment_limit_rebound_two_stage_r15 import (
    SOURCE, MONTHS, TEST_MONTHS, X1, X2, window, rank, classify, rank_quality,
)
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha, xmatrix

OUT=Path('outputs/stock_automl/limit_rebound_search_r16_20261015')
PREV=Path('outputs/stock_automl/limit_rebound_two_stage_r15_20261015')
SEEDS=(171,191)
SCHEMES=('natural','sqrt_weight','balanced_weight','undersample')
PARAMS={
 'rf':[
  {'n_estimators':240,'max_depth':4,'min_samples_leaf':100,'max_features':.8},
  {'n_estimators':240,'max_depth':6,'min_samples_leaf':50,'max_features':.8},
  {'n_estimators':240,'max_depth':8,'min_samples_leaf':30,'max_features':.8}],
 'lgb':[
  {'n_estimators':160,'learning_rate':.04,'num_leaves':7,'max_depth':3,'min_child_samples':80,'reg_lambda':5.},
  {'n_estimators':240,'learning_rate':.03,'num_leaves':15,'max_depth':4,'min_child_samples':60,'reg_lambda':10.},
  {'n_estimators':320,'learning_rate':.025,'num_leaves':31,'max_depth':5,'min_child_samples':50,'reg_lambda':15.}],
 'xgb':[
  {'n_estimators':160,'learning_rate':.04,'max_depth':2,'min_child_weight':20,'reg_lambda':5.},
  {'n_estimators':240,'learning_rate':.03,'max_depth':3,'min_child_weight':20,'reg_lambda':10.},
  {'n_estimators':320,'learning_rate':.025,'max_depth':4,'min_child_weight':30,'reg_lambda':15.}],
}
GROUPS=[{'id':f'{a}_p{p+1}_{s}','algorithm':a,'parameters':v,'scheme':s,'fraction':.8,'seeds':list(SEEDS)}
 for a,ps in PARAMS.items() for p,v in enumerate(ps) for s in SCHEMES]
REFERENCE={'id':'rf_reference','algorithm':'rf','parameters':{'n_estimators':240,'max_depth':7,'min_samples_leaf':50,'max_features':.8},'scheme':'balanced_weight','fraction':1.,'seeds':[20261013]}
ALL_GROUPS=[REFERENCE]+GROUPS

def specification():
 assert sklearn.__version__=='1.7.1'
 OUT.mkdir(parents=True,exist_ok=True)
 value={'name':'R16_72_variants_36_paired_groups_plus_reference','groups':ALL_GROUPS,
  'months':MONTHS,'test_months':TEST_MONTHS,'features':X1,'risk_features':X2,
  'sampling':'80% without replacement stratified by month and label; optional majority undersampling after the 80% draw; weights computed only from sampled train',
  'seeds':'171 and 191 are RNG seeds, not sklearn versions; average probabilities of both, never choose the better seed',
  'cv':'same R15 causal rolling OOF: each of six historical training months scored by a separate six-month fit ending two months before it; warm-up history allowed',
  'score':'.5*(OOF mean Top3/5+validation mean Top3/5)-.25*abs(OOF-validation)-.25*std(eight monthly qualities)-.25*mean(abs(two seed qualities in OOF and validation))',
  'gate':'challenger must improve or equal RF reference on both pooled OOF and validation and beat its stability score; otherwise retain reference',
  'testing':'all monthly choices frozen before held-out outcomes are evaluated; per-group test results descriptive only, no re-selection',
  'stage2':'unchanged R15 8-factor logistic or depth2 tree; 09:40 candidates; 09:41 open execution proxy; D2-D4 no seal and D4 loss>5% bad',
  'feature_pool':'same 35 features and historical non-ST D0/D1 pool as R15; no D0 final-seal-time filter',
  'no_refit_on_validation':True,'historically_researched_not_new_blind_data':True,
  'source_sha256':sha(SOURCE/'panel_replay_through_20261014.pkl'),
  'versions':{'sklearn':sklearn.__version__,'lightgbm':lightgbm.__version__,'xgboost':xgboost.__version__},
  'parameter_sources':['https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html','https://xgboost.readthedocs.io/en/stable/parameter.html']}
 p=OUT/'specification.json'
 if p.exists():assert json.loads(p.read_text())==value
 else:dump(p,value)
 return value

def sample_train(frame,scheme,seed):
 """Keep each month's positive and negative coverage without altering time splits."""
 rng=np.random.default_rng(seed);parts=[]
 for _,g in frame.groupby([frame.d0_date.str[:7],frame.label_3],sort=True):
  count=max(1,int(np.floor(.8*len(g))))
  parts.append(g.iloc[np.sort(rng.choice(len(g),count,replace=False))])
 sampled=pd.concat(parts).sort_values(['d0_date','symbol']).copy()
 if scheme=='undersample':
  count=int(sampled.label_3.value_counts().min());parts=[]
  for _,g in sampled.groupby('label_3',sort=True):
   parts.append(g.iloc[np.sort(rng.choice(len(g),count,replace=False))])
  sampled=pd.concat(parts).sort_values(['d0_date','symbol']).copy()
 counts=sampled.label_3.value_counts()
 weights=np.ones(len(sampled))
 if scheme in ('sqrt_weight','balanced_weight'):
  weights=np.array([len(sampled)/(2*counts[y]) for y in sampled.label_3])
  if scheme=='sqrt_weight':weights=np.sqrt(weights)
  weights/=weights.mean()
 return sampled,weights

def model_for(group,seed):
 a=group['algorithm'];p=group['parameters']
 if a=='rf':return make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestClassifier(**p,n_jobs=1,random_state=seed))
 if a=='lgb':return LGBMClassifier(**p,objective='binary',colsample_bytree=.8,subsample=1.,subsample_freq=0,
  verbosity=-1,deterministic=True,force_col_wise=True,n_jobs=1,random_state=seed)
 return XGBClassifier(**p,objective='binary:logistic',eval_metric='logloss',tree_method='hist',
  colsample_bytree=.8,subsample=1.,n_jobs=1,random_state=seed)

def matrix(f):return xmatrix(f,X1).to_numpy(dtype=np.float64)

def predict(bundle,frame):
 # Reference pipeline was fitted using column-labelled frames.
 if bundle.get('reference'):return bundle['models'][0].predict_proba(xmatrix(frame,X1))[:,1]
 x=matrix(frame)
 return np.mean([m.predict_proba(x)[:,1] for m in bundle['models']],axis=0)

def train_job(month,gid):
 group=next(g for g in ALL_GROUPS if g['id']==gid)
 root=OUT/month;root.mkdir(exist_ok=True);path=root/f'{gid}.joblib'
 if (root/f'{gid}_complete.json').exists():return f'{month} {gid} cached'
 t=time.monotonic();panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
 cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text());w=window(month)
 frames={'train':block(panel,cal,*w['train']), 'valid':block(panel,cal,*w['valid'],purge_events=False),
  'forecast':panel[panel.d0_date.str.startswith(month)].copy()}
 tr=frames['train'];fit=tr[tr.activated&tr.observed_3].sort_values(['d0_date','symbol'])
 if path.exists():bundle=joblib.load(path)
 elif gid=='rf_reference':
  old=joblib.load(PREV/month/'rf.joblib')
  assert set(map(tuple,fit[['symbol','d0_date']].to_numpy()))=={(r['symbol'],r['d0_date']) for r in old['training_keys']}
  bundle={'models':[old['model']],'reference':True,'features':X1,'group':group,'sampling':[{'training_keys':old['training_keys']}],
   'spec_sha256':sha(OUT/'specification.json')}
  joblib.dump(bundle,path,compress=3)
 else:
  models=[];sampling=[]
  for seed in group['seeds']:
   sampled,weight=sample_train(fit,group['scheme'],seed);model=model_for(group,seed)
   kw={'randomforestclassifier__sample_weight':weight} if group['algorithm']=='rf' else {'sample_weight':weight}
   model.fit(matrix(sampled),sampled.label_3.astype(int),**kw);models.append(model)
   sampling.append({'seed':seed,'available':len(fit),'sampled':len(sampled),
    'class_counts':{str(k):int(v) for k,v in sampled.label_3.value_counts().items()},
    'class_weights':{str(y):float(weight[sampled.label_3.to_numpy()==y][0]) for y in (0,1)},
    'training_keys':sampled[['symbol','d0_date','first_limit_date']].to_dict('records')})
  bundle={'models':models,'reference':False,'features':X1,'group':group,'sampling':sampling,'spec_sha256':sha(OUT/'specification.json')}
  before=predict(bundle,fit.iloc[:40]);joblib.dump(bundle,path,compress=3)
  bundle=joblib.load(path);assert np.array_equal(before,predict(bundle,fit.iloc[:40]))
 stats={}
 for part,frame in frames.items():
  x=xmatrix(frame,X1) if bundle.get('reference') else matrix(frame)
  probs=[m.predict_proba(x)[:,1] for m in bundle['models']]
  out=frame[['symbol','d0_date']].copy();out['score']=np.mean(probs,axis=0)
  for j,p in enumerate(probs):out[f'seed{j}']=p
  out.to_pickle(root/f'{gid}_{part}_scores.pkl')
  if part!='forecast':stats[part]=classify(rank(frame,out.score.to_numpy()))
 dump(root/f'{gid}_metrics.json',{'window':w,**stats,'model_sha256':sha(path)})
 dump(root/f'{gid}_complete.json',{'seconds':round(time.monotonic()-t,2),'model_sha256':sha(path)})
 return f'{month} {gid} {time.monotonic()-t:.1f}s'

def train(workers):
 specification();jobs=[(m,g['id']) for m in MONTHS for g in ALL_GROUPS if not (OUT/m/f'{g["id"]}_complete.json').exists()]
 with ProcessPoolExecutor(max_workers=workers) as pool:
  fs=[pool.submit(train_job,*job) for job in jobs]
  for i,f in enumerate(as_completed(fs),1):print(i,'/',len(jobs),f.result(),flush=True)

def scored(panel,month,gid,part):
 s=pd.read_pickle(OUT/month/f'{gid}_{part}_scores.pkl')
 return s.merge(panel,on=['symbol','d0_date'],validate='one_to_one')

def selection_stats(oof,valid):
 a,b=classify(rank(oof,oof.score)),classify(rank(valid,valid.score))
 qa,qb=rank_quality(a),rank_quality(b);monthly=[]
 for f in (oof,valid):
  for _,g in f.groupby(f.d0_date.str[:7]):monthly.append(rank_quality(classify(rank(g,g.score))))
 seed_gap=0.
 if 'seed1' in oof:
  seed_gap=float(np.mean([abs(rank_quality(classify(rank(f,f.seed0)))-rank_quality(classify(rank(f,f.seed1)))) for f in (oof,valid)]))
 spread=float(np.std(monthly));score=.5*(qa+qb)-.25*abs(qa-qb)-.25*spread-.25*seed_gap
 return {'train_oof':a,'valid':b,'oof_quality':qa,'valid_quality':qb,'monthly_std':spread,
  'worst_month':float(min(monthly)),'seed_gap':seed_gap,'selection_score':float(score)}

def choose(wait_for_training=False):
 panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl');cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
 alltop=[];table=[]
 for month in TEST_MONTHS:
  w=window(month);trainmonths=[str(p) for p in pd.period_range(*w['train'],freq='M')]
  required=[OUT/m/f'{g["id"]}_complete.json' for m in [month]+trainmonths for g in ALL_GROUPS]
  while not all(p.exists() for p in required):
   if not wait_for_training:raise RuntimeError(f'Training not complete for {month}')
   time.sleep(5)
  allowed=block(panel,cal,*w['train'])[['symbol','d0_date']];results=[]
  for group in ALL_GROUPS:
   gid=group['id'];oof=pd.concat([scored(panel,m,gid,'forecast') for m in trainmonths],ignore_index=True)
   oof=oof.merge(allowed,on=['symbol','d0_date'],validate='one_to_one');valid=scored(panel,month,gid,'valid')
   stats=selection_stats(oof,valid);infit=json.loads((OUT/month/f'{gid}_metrics.json').read_text())['train']
   results.append({'family':gid,'group':group,'train_infit':infit,**stats})
  base=results[0]
  eligible=[r for r in results if r['oof_quality']>=base['oof_quality'] and r['valid_quality']>=base['valid_quality']]
  winner=max(eligible,key=lambda r:r['selection_score']);gid=winner['family']
  frozen={'month':month,'window':w,'family':gid,'group':winner['group'],'candidates':results,
   'spec_sha256':sha(OUT/'specification.json'),'test_results_seen':False}
  p=OUT/month/'stage1_frozen.json'
  if p.exists():assert json.loads(p.read_text())==frozen
  else:dump(p,frozen)
  for part in ('train_oof','valid','forecast'):
   if part=='train_oof':
    f=pd.concat([scored(panel,m,gid,'forecast') for m in trainmonths],ignore_index=True).merge(allowed,on=['symbol','d0_date'],validate='one_to_one')
   else:f=scored(panel,month,gid,part)
   f=rank(f,f.score);top=f[f.d0_rank.le(5)].copy();top['evaluation_month']=month;top['part']=part;top['family']=gid
   top.to_pickle(OUT/month/f'chosen_{part}_top5.pkl');alltop.append(top)
  table.append({'month':month,**{k:v for k,v in winner.items() if k not in ('train_infit','train_oof','valid')}})
  print('FROZEN',month,gid,round(winner['oof_quality'],4),round(winner['valid_quality'],4),flush=True)
 pd.concat(alltop,ignore_index=True).to_pickle(OUT/'all_top5.pkl')
 demand=pd.concat(alltop)[['symbol','d1_date','d1_cap','d1_preclose','d1_open','d1_close','d1_volume']].dropna(subset=['d1_date']).drop_duplicates(['symbol','d1_date'])
 demand.to_pickle(OUT/'minute_demand.pkl');dump(OUT/'monthly_choices.json',table)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['spec','train','choose','smoke']);p.add_argument('--workers',type=int,default=4);p.add_argument('--wait-for-training',action='store_true');a=p.parse_args()
 if a.stage=='train':train(a.workers)
 elif a.stage=='smoke':
  specification()
  for gid in ('rf_reference','rf_p1_natural','lgb_p1_natural','xgb_p1_natural'):print(train_job('2025-01',gid),flush=True)
 elif a.stage=='choose':choose(a.wait_for_training)
 else:specification()
