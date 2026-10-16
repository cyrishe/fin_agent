"""Monthly forward models: 10 train months, 2 validation months, 1 test month.

Choose candidates with pooled expanding-window CV plus the independent validation.
All learned preprocessing and weights are fitted only on the corresponding train.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_limit_rebound_short_mixed_r12 import (
    FEATURES, EXTRA, make_model, xmatrix, top_five, dump, sha,
)
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

SOURCE=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
OUT=Path('outputs/stock_automl/limit_rebound_monthly_r14_20261014')
MONTHS=[str(m) for m in pd.period_range('2025-01','2026-10',freq='M')]
CANDIDATES=[{'id':f'{family}_{features}','family':family,'features':features}
            for family in ('rf','extra','hgb','logistic')
            for features in (('base','expanded') if family!='logistic' else ('expanded',))]


def specification():
    path=OUT/'specification.json'
    pool={'training_event_purge':True,'evaluation_event_purge':False,
          'evaluation_pool':'all D0 sealed candidates on dates with complete D4 known by cutoff; ranking before any per-stock anomaly exclusion',
          'forecast_results_seen':False}
    pool_path=OUT/'candidate_pool_protocol.json'
    if pool_path.exists(): assert json.loads(pool_path.read_text())==pool
    else: dump(pool_path,pool)
    spec={'experiment':'R14_monthly_10_2_1','months':MONTHS,'candidates':CANDIDATES,
          'cv':'within the 10 train months: first 4 -> next 2; first 6 -> next 2; first 8 -> last 2',
          'choice':'0.5 * pooled CV Top1 hits per D0 day + 0.5 * independent validation Top1 hits per D0 day; ties: min of two rates then mean AP',
          'training_metric':'resubstitution diagnostic only; never used for selection',
          'prediction_policy':'D0 Top5; D1 historical non-ST open below cap; choose highest D0 score among activated',
          'target':'D1-D3 any sealed close, observed labels only',
          'feature_cutoff':'D0 and earlier daily information only',
          'outcome_purge':'D4 must be strictly before next block starts; remove all events still sealed at preceding final trading day, known at cutoff',
          'past_features':'prior price observations may appear in next-block feature lookbacks; no future outcomes used; no shuffle',
          'monthly_test':'all D0 days of target month, including first trading day; do not drop month boundaries; incomplete endpoint labels/returns kept pending',
          'refit_on_validation':False,'baseline':'rf_expanded independently refit every month on the same 10 months',
          'sklearn':'1.7.1','seed':20261013,
          'panel_sha256':sha(SOURCE/'panel_replay_through_20261014.pkl'),
          'calendar_sha256':sha(SOURCE/'calendar_through_20261014.json')}
    if path.exists(): assert json.loads(path.read_text())==spec
    else: dump(path,spec)
    return spec


def month_start(m): return str(pd.Period(m,freq='M').start_time.date())
def next_month(m): return str(pd.Period(m,freq='M')+1)


def window(month):
    m=pd.Period(month,freq='M')
    return {'train':[str(m-12),str(m-3)],'valid':[str(m-2),str(m-1)],'test':month}


def block(panel,calendar,start,end,purge_events=True):
    """Use only complete outcomes known before the following month starts.

    Event purge uses the preceding last day, not future held-out event membership.
    """
    first=month_start(start); cutoff=month_start(next_month(end))
    end_i=np.searchsorted(calendar,cutoff)-1
    keep=panel.d0_date.ge(first)&panel.d0_date.lt(cutoff)&panel.date_i.add(4).le(end_i)
    running=panel[panel.date_i.eq(end_i)]
    active_keys=pd.MultiIndex.from_frame(running[['symbol','first_limit_date']])
    overlap=pd.MultiIndex.from_frame(panel[['symbol','first_limit_date']]).isin(active_keys)
    f=panel[keep&(~overlap if purge_events else True)].copy()
    if len(f):
        assert f.date_i.add(4).le(end_i).all()
        assert f.d0_date.lt(cutoff).all()
    return f


def metrics(f,scores):
    p=np.asarray(scores);keep=f.activated&f.observed_3
    y=f.loc[keep,'label_3'].astype(int).to_numpy(); pp=p[keep.to_numpy()]
    top=top_five(f,p);chosen=top[top.selected];known=chosen[chosen.observed_3]
    n_days=int(f.d0_date.nunique())
    results={'d0_rows':len(f),'classification_n':len(y),'base_rate':float(y.mean()),
       'ap':float(average_precision_score(y,pp)),'auc':float(roc_auc_score(y,pp)),
       'days':n_days,'picks':len(chosen),'known':len(known),'hits':int(known.label_3.sum()),
       'hit_rate':float(known.label_3.mean()) if len(known) else 0.,
       'hits_per_day':float(known.label_3.sum()/n_days),
       'd0_rank1_picks':int(chosen.d0_rank.eq(1).sum())}
    return results,top


def fit_one(candidate,train,score_frames,path):
    features=list(FEATURES)+(list(EXTRA) if candidate['features']=='expanded' else [])
    fitted=train[train.activated&train.observed_3]
    if path.exists():
        bundle=joblib.load(path);model=bundle['model']
        assert bundle['training_keys']==fitted[['symbol','d0_date']].to_dict('records')
        assert bundle['candidate']==candidate and bundle['spec_sha256']==sha(OUT/'specification.json')
    else:
        model=make_model(candidate['family']);weights=balanced_weights(fitted.label_3)
        kw={'sample_weight':weights} if candidate['family']=='hgb' else {model.steps[-1][0]+'__sample_weight':weights}
        model.fit(xmatrix(fitted,features),fitted.label_3,**kw)
        if candidate['family'] in ('rf','extra'): model.steps[-1][1].n_jobs=1
        before=model.predict_proba(xmatrix(fitted.iloc[:50],features))[:,1]
        joblib.dump({'model':model,'features':features,'candidate':candidate,
              'spec_sha256':sha(OUT/'specification.json'),'sklearn':sklearn.__version__,
              'training_keys':fitted[['symbol','d0_date']].to_dict('records')},path,compress=3)
        model=joblib.load(path)['model']
        assert np.array_equal(before,model.predict_proba(xmatrix(fitted.iloc[:50],features))[:,1])
    outputs={};frames={}
    for part,f in score_frames.items():
        score=model.predict_proba(xmatrix(f,features))[:,1]
        m,top=metrics(f,score);outputs[part]=m
        scored=f.copy();scored['score']=score;frames[part]=scored
    return outputs,frames


def selection_key(result):
    cv=result['cv'];v=result['valid']
    return ((cv['hits_per_day']+v['hits_per_day'])/2,
             min(cv['hits_per_day'],v['hits_per_day']),(cv['ap']+v['ap'])/2)


def train_month(month,panel,calendar):
    root=OUT/month;root.mkdir(parents=True,exist_ok=True)
    if (root/'selection_frozen.json').exists():
        frozen=json.loads((root/'selection_frozen.json').read_text())
        assert frozen['spec_sha256']==sha(OUT/'specification.json')
        assert frozen['pool_sha256']==sha(OUT/'candidate_pool_protocol.json')
        return
    dates=window(month);start=pd.Period(dates['train'][0],freq='M')
    train=block(panel,calendar,*dates['train']);valid=block(panel,calendar,*dates['valid'],purge_events=False)
    assert not set(zip(train.symbol,train.first_limit_date))&set(zip(valid.symbol,valid.first_limit_date))
    all_results=[]
    for candidate in CANDIDATES:
        cid=candidate['id'];target=root/f'{cid}_metrics.json'
        if target.exists(): all_results.append(json.loads(target.read_text()));continue
        cvs=[];fold_metrics=[]
        for fold,size in enumerate((4,6,8),1):
            tr=block(panel,calendar,str(start),str(start+size-1))
            va=block(panel,calendar,str(start+size),str(start+size+1),purge_events=False)
            assert tr.date_i.max()+4<va.date_i.min()
            assert not set(zip(tr.symbol,tr.first_limit_date))&set(zip(va.symbol,va.first_limit_date))
            m,scored=fit_one(candidate,tr,{'cv':va},root/f'{cid}_cv{fold}.joblib')
            s=scored['cv'];s['cv_fold']=fold;cvs.append(s)
            fold_metrics.append({'fold':fold,'train_months':[str(start),str(start+size-1)],
                    'valid_months':[str(start+size),str(start+size+1)],'train_n':int((tr.activated&tr.observed_3).sum()),**m['cv']})
        cv=pd.concat(cvs,ignore_index=True);assert not cv.duplicated(['symbol','d0_date']).any()
        cv_metric,_=metrics(cv,cv.score)
        fit_metrics,frames=fit_one(candidate,train,{'train':train,'valid':valid},root/f'{cid}.joblib')
        result={'candidate':candidate,'months':dates,'cv':cv_metric,'cv_folds':fold_metrics,
                'pool_sha256':sha(OUT/'candidate_pool_protocol.json'),**fit_metrics}
        result['combined_score']=selection_key(result)[0]
        result['model_sha256']=sha(root/f'{cid}.joblib')
        score_cols=['symbol','d0_date','d1_date','score','activated','observed_3','label_3','first_limit_date']
        cv[score_cols+['cv_fold']].to_pickle(root/f'{cid}_cv_scored.pkl')
        for part,frame in frames.items():frame[score_cols].to_pickle(root/f'{cid}_{part}_scored.pkl')
        dump(target,result);all_results.append(result)
        print(month,cid,'cv/valid',round(cv_metric['hits_per_day'],4),round(result['valid']['hits_per_day'],4),flush=True)
    winner=max(all_results,key=selection_key)
    dump(root/'selection_frozen.json',{'month':month,'months':dates,'candidate':winner['candidate'],
       'selection':selection_key(winner),'spec_sha256':sha(OUT/'specification.json'),
       'pool_sha256':sha(OUT/'candidate_pool_protocol.json'),'candidates':all_results,
       'train_n':winner['train']['classification_n'],'valid_n':winner['valid']['classification_n']})
    print('FROZEN',month,winner['candidate']['id'],flush=True)


def train(parity,shards=2):
    assert sklearn.__version__=='1.7.1'
    specification();panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
    calendar=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
    for i,month in enumerate(MONTHS):
        if parity is None or i%shards==parity: train_month(month,panel,calendar)


def test():
    spec=specification()
    # Freeze every monthly model choice before reporting any held-out results.
    for month in MONTHS: assert (OUT/month/'selection_frozen.json').exists(),month
    panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
    chosen=[];reference=[];summaries=[]
    for month in MONTHS:
        root=OUT/month;f=panel[panel.d0_date.str.startswith(month)].copy()
        frozen=json.loads((root/'selection_frozen.json').read_text())
        assert frozen['pool_sha256']==sha(OUT/'candidate_pool_protocol.json')
        cid=frozen['candidate']['id']
        for policy,model_id in [('滚动选优',cid),('滚动固定森林','rf_expanded')]:
            bundle=joblib.load(root/f'{model_id}.joblib')
            scores=bundle['model'].predict_proba(xmatrix(f,bundle['features']))[:,1]
            m,top=metrics(f,scores);top['forecast_month']=month;top['model_id']=model_id;top['model_policy']=policy
            saved=f[['symbol','d0_date','d1_date','activated','label_3','observed_3']].copy();saved['score']=scores
            saved.to_pickle(root/f'{policy}_test_scores.pkl')
            top.to_pickle(root/f'{policy}_test_top5.pkl')
            (chosen if policy=='滚动选优' else reference).append(top)
            summaries.append({'month':month,'policy':policy,'model':model_id,**m})
        print('tested',month,cid,flush=True)
    pd.concat(chosen,ignore_index=True).to_pickle(OUT/'chosen_test_top5.pkl')
    pd.concat(reference,ignore_index=True).to_pickle(OUT/'reference_test_top5.pkl')
    dump(OUT/'test_metrics.json',summaries)
    dump(OUT/'model_manifest.json',{'models':[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size}
              for p in sorted(OUT.glob('*/*.joblib'))], 'spec_sha256':sha(OUT/'specification.json')})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['spec','train','test'])
    parser.add_argument('--parity',type=int,choices=[0,1,2,3]);parser.add_argument('--shards',type=int,choices=[2,4],default=2)
    args=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    if args.stage=='spec': specification()
    elif args.stage=='train': train(args.parity,args.shards)
    else: test()
