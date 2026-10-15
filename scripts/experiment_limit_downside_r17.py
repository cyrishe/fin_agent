"""Independent D0 sealed-board downside classifier, with chronological model selection.

No prior strategy scores, D1 activation facts, or future features enter training.
Positive: D3 close / D0 close - 1 < -5%; negative: >= -2%.
Middle outcomes are scored and evaluated, but never used as binary training labels.
"""
from __future__ import annotations
import argparse, json, os, platform, time, warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import joblib, numpy as np, pandas as pd, sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, accuracy_score
from scripts.experiment_limit_rebound_short_mixed_r12 import FEATURES, EXTRA, dump, sha, xmatrix
from scripts.experiment_limit_rebound_search_r16 import GROUPS, model_for
import lightgbm, xgboost

warnings.filterwarnings('ignore', message='X does not have valid feature names, but LGBMClassifier was fitted with feature names')
OUT=Path('outputs/stock_automl/limit_downside_r17_20261015')
SOURCE=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
X=list(FEATURES)+list(EXTRA)
MONTHS=[str(m) for m in pd.period_range('2025-09','2026-10',freq='M')]
CONFIGS=[{**g,'parameters':{**g['parameters'],**({'n_estimators':80} if g['algorithm']=='rf' else {})}} for g in GROUPS]+[{'id':f'logistic_c{c}_{s}','algorithm':'logistic','parameters':{'C':c},
                     'scheme':s,'fraction':.8,'seeds':[171,191]}
                    for c in (.1,1.) for s in ('natural','balanced_weight')]
BASE='logistic_c0.1_balanced_weight'


def window(month):
    m=pd.Period(month,freq='M')
    return {'train':[str(m-8),str(m-3)],'valid':[str(m-2),str(m-1)],'test':month}


def labels(f):
    """Price thresholds avoid binary floating-point errors at exactly -5% / -2%."""
    complete=f[['close','d1_close','d2_close','d3_close','d1_preclose','d2_preclose','d3_preclose']].gt(0).all(axis=1)
    discontinuity=pd.Series(False,index=f.index)
    for d in (1,2,3):
        discontinuity |= f[f'd{d}_preclose'].sub(f['close' if d==1 else f'd{d-1}_close']).abs().gt(.011)
    known=complete&~discontinuity
    y=pd.Series(np.nan,index=f.index)
    y.loc[known&f.d3_close.lt(f.close*.95-1e-9)]=1
    y.loc[known&f.d3_close.ge(f.close*.98-1e-9)]=0
    return known,discontinuity,y


def prepare():
    assert sklearn.__version__=='1.7.1'
    OUT.mkdir(parents=True,exist_ok=True)
    p=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
    cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
    exact=p.close.sub(p.d0_cap).abs().le(.005)&p.up.eq(True)&p.d0_st.eq(0)
    p.loc[~exact].to_pickle(OUT/'excluded_d0_cap_mismatch.pkl')
    f=p.loc[exact].copy().sort_values(['d0_date','symbol']).reset_index(drop=True)
    f['risk_known'],f['risk_price_gap'],f['risk_y']=labels(f)
    f['risk_return']=f.d3_close.div(f.close).sub(1).where(f.risk_known)
    f['risk_group']=np.select([~f.risk_known,f.risk_y.eq(1),f.risk_y.eq(0)],['未标注','大跌','非大跌'],default='中间')
    f['worst_close_return']=f[['d1_close','d2_close','d3_close']].min(axis=1).div(f.close).sub(1).where(f.risk_known)
    f['d3_date']=[cal[i+3] if i+3<len(cal) else None for i in f.date_i]
    f.to_pickle(OUT/'panel.pkl');dump(OUT/'calendar.json',cal)
    spec={'experiment':'R17_independent_three_day_downside','source_sha256':sha(SOURCE/'panel_replay_through_20261014.pkl'),
          'features':X,'configs':CONFIGS,'test_months':MONTHS,'rolling':'6 months train / next 2 validation / next 1 test; no refit on validation',
          'target':{'positive':'D3 close < 0.95 * D0 close','negative':'D3 close >= 0.98 * D0 close','middle':'[-5%,-2%) excluded only from binary fitting and binary classification metrics'},
          'universe':'historical non-ST, supported price-limit boards, exact rounded D0 cap; every sealed stock-day incl all streak lengths, no D1 activation requirement',
          'quality':'complete positive D1-D3 closes/precloses; each next preclose must match preceding close within 0.011; otherwise outcomes unavailable, predictions retained',
          'cv':'inside six-month train: first3 -> month4, first4 -> month5, first5 -> month6; D3 labels purged at each boundary; ongoing board events removed at preceding cutoff',
          'selection':'maximize .5*(CV AUC+validation AUC)-.25*abs(CV AUC-validation AUC)-.25*std(monthly AUC across CV and validation); challenger must at least match regularized logistic baseline on CV and validation AUC',
          'threshold':'fixed CV all-outcome score quantiles 70/80/90%; main flag at 80th percentile (nominal highest-risk 20%); threshold never tuned on test or prior-strategy lists',
          'scores':'binary pole-discrimination scores, not calibrated all-population crash probabilities',
          'sampling':'80% without replacement per month/label; natural/sqrt-balanced/full-balanced/1:1 undersample; average RNG171+191 for each configuration',
          'independence':'no R15/R16 scores or selection membership in fitting, feature construction or model/threshold selection',
          'test_history':'previously researched dates; chronological out-of-sample replay, not newly unseen market history',
          'pretest_runtime_revision':'RF 240 trees reduced to 80 per seed before any R17 held-out result was generated; partial 240-tree pilot preserved separately and not used for model selection',
          'versions':{'python':platform.python_version(),'sklearn':sklearn.__version__,'lightgbm':lightgbm.__version__,'xgboost':xgboost.__version__,'numpy':np.__version__,'pandas':pd.__version__}}
    old=OUT/'specification.json'
    if old.exists():assert json.loads(old.read_text())==spec
    dump(old,spec)
    dump(OUT/'data_audit.json',{'source_n':len(p),'exact_seal_n':len(f),'excluded_d0_cap_mismatch':int((~exact).sum()),
         'known':int(f.risk_known.sum()),'price_gaps':int(f.risk_price_gap.sum()),'groups':{k:int(v) for k,v in f.risk_group.value_counts().items()},
         'source_start':f.d0_date.min(),'source_end':f.d0_date.max(),'outcome_end':cal[-1],'panel_sha256':sha(OUT/'panel.pkl')})
    print(json.loads((OUT/'data_audit.json').read_text()),flush=True)


def block(p,cal,start,end):
    cutoff=str((pd.Period(end,freq='M')+1).start_time.date())
    end_i=int(np.searchsorted(cal,cutoff))-1
    active=pd.MultiIndex.from_frame(p.loc[p.date_i.eq(end_i),['symbol','first_limit_date']])
    active_mask=pd.MultiIndex.from_frame(p[['symbol','first_limit_date']]).isin(active)
    return p[p.d0_date.ge(start+'-01')&p.d0_date.lt(cutoff)&p.date_i.add(3).le(end_i)&~active_mask].copy()


def frames(p,cal,month):
    w=window(month);tr=block(p,cal,*w['train']);va=block(p,cal,*w['valid'])
    folds=[];start=pd.Period(w['train'][0],freq='M')
    for n in (3,4,5):
        ft=block(tr,cal,str(start),str(start+n-1));fv=block(tr,cal,str(start+n),str(start+n))
        assert not set(zip(ft.symbol,ft.first_limit_date))&set(zip(fv.symbol,fv.first_limit_date))
        folds.append((ft,fv))
    assert not set(zip(tr.symbol,tr.first_limit_date))&set(zip(va.symbol,va.first_limit_date))
    return tr,va,folds


def sample(f,scheme,seed):
    f=f[f.risk_y.notna()];rng=np.random.default_rng(seed);chunks=[]
    for _,g in f.groupby([f.d0_date.str[:7],f.risk_y],sort=True):
        n=max(1,int(len(g)*.8));chunks.append(g.iloc[np.sort(rng.choice(len(g),n,replace=False))])
    s=pd.concat(chunks).sort_values(['d0_date','symbol'])
    if scheme=='undersample':
        n=int(s.risk_y.value_counts().min())
        s=pd.concat([g.iloc[np.sort(rng.choice(len(g),n,replace=False))] for _,g in s.groupby('risk_y')]).sort_values(['d0_date','symbol'])
    counts=s.risk_y.value_counts();weights=np.ones(len(s))
    if scheme in ('balanced_weight','sqrt_weight'):
        weights=np.array([len(s)/(2*counts[v]) for v in s.risk_y])
        if scheme=='sqrt_weight':weights=np.sqrt(weights)
        weights/=weights.mean()
    return s,weights


def matrix(f):return xmatrix(f,X).to_numpy(dtype=np.float64)


def fit(config,f,path):
    if path.exists():return joblib.load(path)
    models=[];sampling=[]
    for seed in config['seeds']:
        s,weights=sample(f,config['scheme'],seed)
        if config['algorithm']=='logistic':
            m=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),
                            LogisticRegression(C=config['parameters']['C'],max_iter=2000,random_state=seed))
            kw={'logisticregression__sample_weight':weights}
        else:
            m=model_for(config,seed)
            kw={('randomforestclassifier__sample_weight' if config['algorithm']=='rf' else 'sample_weight'):weights}
        m.fit(matrix(s),s.risk_y.astype(int),**kw);models.append(m)
        sampling.append({'seed':seed,'eligible_poles':int(f.risk_y.notna().sum()),'used':len(s),
                         'counts':{str(int(k)):int(v) for k,v in s.risk_y.value_counts().items()},
                         'training_indices':s.index.tolist()})
    bundle={'models':models,'features':X,'config':config,'sampling':sampling,'target':'risk_y',
            'spec_sha256':sha(OUT/'specification.json'),'training_start':f.d0_date.min(),'training_end':f.d0_date.max()}
    joblib.dump(bundle,path,compress=3)
    before=predict(bundle,f.iloc[:20]);b=joblib.load(path)
    assert np.array_equal(before,predict(b,f.iloc[:20]))
    return b


def predict(bundle,f):
    values=xmatrix(f,bundle['features']).to_numpy(dtype=np.float64)
    return np.mean([m.predict_proba(values)[:,1] for m in bundle['models']],axis=0)


def metrics(f):
    poles=f[f.risk_y.notna()];y=poles.risk_y.astype(int)
    return {'n':len(f),'known':int(f.risk_known.sum()),'poles':len(poles),'bad':int(y.sum()),
            'auc':float(roc_auc_score(y,poles.risk_score)) if y.nunique()==2 else None,
            'ap':float(average_precision_score(y,poles.risk_score)) if y.nunique()==2 else None,
            'accuracy_at_half':float(accuracy_score(y,poles.risk_score.ge(.5))) if len(y) else None,
            'balanced_accuracy_at_half':float(balanced_accuracy_score(y,poles.risk_score.ge(.5))) if y.nunique()==2 else None}


def scored(f,b):
    s=f.copy();s['risk_score']=predict(b,f);return s


def job(month,gid):
    start=time.monotonic();root=OUT/month/gid;root.mkdir(parents=True,exist_ok=True)
    if (root/'metrics.json').exists():return month,gid,'cached'
    p=pd.read_pickle(OUT/'panel.pkl');cal=json.loads((OUT/'calendar.json').read_text());tr,va,folds=frames(p,cal,month)
    config=next(c for c in CONFIGS if c['id']==gid);oofs=[]
    for i,(ft,fv) in enumerate(folds):
        b=fit(config,ft,root/f'cv{i+1}.joblib');oofs.append(scored(fv,b))
    oof=pd.concat(oofs).sort_index();b=fit(config,tr,root/'full.joblib')
    vs=scored(va,b);ts=scored(tr,b)
    stats={}
    for part,f in [('train',ts),('cv',oof),('valid',vs)]:
        f[['risk_score']].to_pickle(root/f'{part}_scores.pkl');stats[part]=metrics(f)
    month_auc=[metrics(g)['auc'] for f in (oof,vs) for _,g in f.groupby(f.d0_date.str[:7])]
    a,b=stats['cv']['auc'],stats['valid']['auc']
    stats['score']=float(.5*(a+b)-.25*abs(a-b)-.25*np.std(month_auc))
    stats['month_auc_std']=float(np.std(month_auc));stats['config']=config
    # Fixed thresholds fitted only to forward-CV score distribution, including middle outcomes.
    stats['thresholds']={str(q):float(oof.risk_score.quantile(q)) for q in (.7,.8,.9)}
    dump(root/'metrics.json',stats)
    return month,gid,round(time.monotonic()-start,1)


def train(workers):
    assert sklearn.__version__=='1.7.1';assert (OUT/'specification.json').exists()
    tasks=[(m,c['id']) for m in MONTHS for c in CONFIGS]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(job,*t) for t in tasks]
        for n,f in enumerate(as_completed(futures),1):print('fit',n,len(tasks),f.result(),flush=True)


def choose():
    selections=[]
    for month in MONTHS:
        rows=[json.loads((OUT/month/c['id']/'metrics.json').read_text()) for c in CONFIGS]
        base=next(r for r in rows if r['config']['id']==BASE)
        eligible=[r for r in rows if r['cv']['auc']>=base['cv']['auc'] and r['valid']['auc']>=base['valid']['auc']]
        best=max(eligible,key=lambda r:r['score'])
        record={'month':month,'window':window(month),'chosen':best['config']['id'],'thresholds':best['thresholds'],
                'selection_score':best['score'],'candidates':rows,'spec_sha256':sha(OUT/'specification.json')}
        dump(OUT/month/'selection_frozen.json',record);selections.append(record)
    dump(OUT/'all_selections_frozen.json',selections)
    print('all selections frozen',len(selections),flush=True)


def test():
    frozen=json.loads((OUT/'all_selections_frozen.json').read_text());p=pd.read_pickle(OUT/'panel.pkl');rows=[];metrics_rows=[]
    for s in frozen:
        month=s['month'];root=OUT/month/s['chosen'];b=joblib.load(root/'full.joblib')
        f=scored(p[p.d0_date.str.startswith(month)],b);f['evaluation_month']=month;f['config_id']=s['chosen']
        for q,threshold in s['thresholds'].items():f['flag'+str(round((1-float(q))*100))]=f.risk_score.ge(threshold)
        f.to_pickle(OUT/month/'test_scored.pkl');rows.append(f);metrics_rows.append({'month':month,**metrics(f)})
    f=pd.concat(rows,ignore_index=True);f.to_pickle(OUT/'test_scored.pkl')
    dump(OUT/'test_metrics.json',{'all':metrics(f),'monthly':metrics_rows,'selections_sha256':sha(OUT/'all_selections_frozen.json')})
    print(json.loads((OUT/'test_metrics.json').read_text())['all'],flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','train','choose','test']);ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args()
    if args.action=='train':train(args.workers)
    else:globals()[args.action]()
