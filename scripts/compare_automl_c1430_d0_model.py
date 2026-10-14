"""C1430-D: transfer R12 algorithms/features onto the fixed C1430 samples.

Fixed 3x4 factorial comparison. No post-test hyperparameter search; all twelve
cells reported. Original R12 artifacts remain read-only.
"""
import json
import hashlib
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from scripts.experiment_automl_c1430_cv import KEY, FEATURES as CV_FEATURES, splits, folds, metrics
from scripts.experiment_automl_limit_rebound_d0_open_r5 import FEATURES as PRIOR
from scripts.experiment_limit_rebound_short_mixed_r12 import EXTRA, make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

OUT=Path('outputs/stock_automl/limit_rebound_1430_d0_compare_20261014')
CV=Path('outputs/stock_automl/limit_rebound_1430_cv_20261014')
R12=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
BASE=Path('outputs/stock_automl/limit_rebound_1430_long_20261014')
FEATURES={
    'd0_6':['board_streak_days','d0_return','pre5_return','pre5_mean_turnover_pct',
            'market_up_fraction','d0_volume_ratio_pre5'],
    'd0_23':list(PRIOR),
    'd0_35':list(PRIOR)+list(EXTRA),
    'late_35':CV_FEATURES['full35'],
}

def dump(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=lambda v:v.item())+'\n')

def fit(family,cols,frame):
    m=make_model(family);w=balanced_weights(frame.label_next3)
    kwargs={'sample_weight':w} if family=='hgb' else {list(m.named_steps)[-1]+'__sample_weight':w}
    m.fit(frame[cols],frame.label_next3,**kwargs)
    if family=='rf':m.steps[-1][1].n_jobs=1
    return m

def score(m,cols,frame):return m.predict_proba(frame[cols])[:,1]

def audit_r12():
    evidence={}
    for group,cid in [('chronological','rf_expanded_10m'),('mixed','logistic_expanded_mixed')]:
        evidence[group]={}
        for part in ['train','valid','test']:
            x=pd.read_pickle(R12/group/f'{cid}_{part}_top5.pkl')
            z=x[x.selected&x.observed_3].copy()
            later_observed=z[['d2_up','d3_up','d4_up']].notna().all(axis=1)
            evidence[group][part]=dict(total_selected=int(x.selected.sum()),picks=len(z),hits=int(z.label_3.sum()),
                    d1_hits=int(z.d1_up.sum()),start=z.d1_date.min(),end=z.d1_date.max(),
                    d1_only=int((z.d1_up.eq(True)&~z.d2_up.eq(True)&~z.d3_up.eq(True)).sum()),
                    later_d2_d4_observed=int(later_observed.sum()),
                    later_d2_d4=int(((z.d2_up.eq(True)|z.d3_up.eq(True)|z.d4_up.eq(True))&later_observed).sum()))
    minute=pd.concat([pd.read_csv(BASE/f'minute_{y}.csv.gz',dtype={'symbol':str}) for y in ['2025','2026']])
    top=pd.read_pickle(R12/'chronological/rf_expanded_10m_test_top5.pkl')
    chosen=top[top.selected&top.d1_date.le('2026-08-07')].copy()
    chosen=chosen.merge(minute[['symbol','d1_date','d1_1430_price']],on=['symbol','d1_date'],how='left',validate='one_to_one')
    eligible=chosen.d1_1430_price.lt(chosen.d1_cap-.005)
    chosen['not_at_cap_1430']=eligible
    chosen.to_pickle(OUT/'r12_matched_dates_top1.pkl')
    evidence['chronological_to_aug7']=dict(picks=len(chosen),minute_missing=int(chosen.d1_1430_price.isna().sum()),
        hits=int(chosen.label_3.sum()),d1_hits=int(chosen.d1_up.sum()),
        at_cap_1430=int(chosen.d1_1430_price.ge(chosen.d1_cap-.005).sum()),
        remaining=int(eligible.sum()),remaining_original_hits=int(chosen.loc[eligible,'label_3'].sum()),
        remaining_future_d2_d4_hits=int((chosen.loc[eligible,'d2_up'].eq(True)|
                                      chosen.loc[eligible,'d3_up'].eq(True)|chosen.loc[eligible,'d4_up'].eq(True)).sum()))
    dump('r12_target_and_eligibility_audit.json',evidence)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert sklearn.__version__=='1.7.1'
    source=pd.read_pickle(CV/'numeric_snapshot.pkl')
    other=pd.read_pickle(R12/'panel.pkl')
    joined=source.merge(other[['symbol','d0_date',*EXTRA]],on=['symbol','d0_date'],validate='one_to_one')
    assert len(joined)==len(source)
    allcols=list(dict.fromkeys(c for cols in FEATURES.values() for c in cols))
    joined[allcols]=joined[allcols].replace([np.inf,-np.inf],np.nan)
    # R12 model transfer uses C1430's original base inputs, not silently replaced inputs.
    check=source[['symbol','d0_date','close']].merge(other[['symbol','d0_date','close']],on=['symbol','d0_date'])
    assert np.allclose(check.close_x,check.close_y,rtol=0,atol=1e-12)
    joined.to_pickle(OUT/'numeric_snapshot.pkl')
    sets=splits(joined);ff=folds(sets['train'])
    configs=[dict(name=f'{family}_{fs}',family=family,features=fs)
             for family in ['hgb','rf','logistic'] for fs in FEATURES]
    dump('plan.json',dict(experiment='C1430-D',configs=configs,features=FEATURES,
        evaluation='Fixed factorial comparison. Same C1430 data, target, four purged folds, holdouts, refit. '
                   'No winner selected by test and no further search. Historical holdouts already studied.',
        variables='Original D0 model has 23 or 35 daily features. Our 35 has same base23 +12 intraday.',
        source_sha={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [CV/'numeric_snapshot.pkl',R12/'panel.pkl',R12/'specification.json']},
        splits={k:dict(rows=len(v),days=v.d1_date.nunique(),start=v.d1_date.min(),end=v.d1_date.max()) for k,v in sets.items()}))
    audit_r12()
    allrows=[];summary={};replay=[]
    gate=pd.read_csv(BASE/'review_scored_full_1440.csv.gz',dtype={'symbol':str})[KEY+['at_cap_1440']]
    for c in configs:
        cols=FEATURES[c['features']];family=c['family'];name=c['name'];parts=[]
        for n,t,v in ff:
            model=fit(family,cols,t);s=score(model,cols,v)
            trainmet=metrics(t,score(model,cols,t));met=metrics(v,s)
            allrows.append(dict(model=name,fold=n,fit_ap=trainmet['ap'],fit_top1=trainmet['top1_rate'],**met))
            p=v[KEY+['label_next3']].copy();p['score']=s;p['fold']=n;parts.append(p)
        oof=pd.concat(parts);oof.to_pickle(OUT/f'{name}_oof.pkl')
        model=fit(family,cols,sets['train']);refit=fit(family,cols,sets['refit'])
        result=dict(family=family,feature_set=c['features'],features=len(cols),
                    oof=metrics(oof,oof.score.to_numpy()))
        for part,modelpart,fitted in [('train','train',model),('valid','train',model),('test','refit',refit)]:
            s=score(fitted,cols,sets[part]);p=sets[part][KEY+['label_next3']].copy();p['score']=s
            p.to_pickle(OUT/f'{name}_{part}_scores.pkl');result[part]=metrics(p,s)
            path=OUT/f'{name}_{modelpart}.joblib'
            if part!='valid':joblib.dump(dict(config=c,columns=cols,model=fitted),path,compress=3)
            restored=joblib.load(path)['model']
            error=float(np.max(np.abs(s-score(restored,cols,sets[part]))));assert error==0
            replay.append(dict(model=name,split=part,max_error=error))
            tradable=p.merge(gate,on=KEY,validate='one_to_one');assert len(tradable)==len(p)
            tradable=tradable[~tradable.at_cap_1440]
            result[part+'_1440']=metrics(tradable,tradable.score.to_numpy())
        if name=='hgb_late_35':
            for part in ['train','valid','test']:
                a=pd.read_pickle(OUT/f'{name}_{part}_scores.pkl');b=pd.read_pickle(CV/f'baseline35_{part}_scores.pkl')
                assert np.array_equal(a.score.to_numpy(),b.score.to_numpy())
        summary[name]=result
        print(name,'fit/OOF/valid/test Top1',*[round(result[k]['top1_rate'],4) for k in ['train','oof','valid','test']],flush=True)
        dump('results.json',summary)
    cv=pd.DataFrame(allrows);cv.to_csv(OUT/'cv_folds.csv',index=False)
    rows=[]
    for name,v in summary.items():
        own=cv[cv.model.eq(name)]
        row=dict(model=name,features=v['features'],cv_mean_ap=float(own.ap.mean()),cv_mean_lift=float(own.ap_lift.mean()))
        for part in ['train','oof','valid','test']:
            row.update({f'{part}_{key}':v[part][key] for key in ['ap','auc','top1_hits','top1_n','top1_rate']})
        rows.append(row)
    pd.DataFrame(rows).to_csv(OUT/'comparison.csv',index=False)
    dump('model_replay.json',replay)
    dump('manifest.json',{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!='manifest.json'})

if __name__=='__main__':main()
