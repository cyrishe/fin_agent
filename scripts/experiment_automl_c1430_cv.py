"""C1430-C: predeclared training-only, purged expanding-window selection.

No random folds, and no holdout-dependent model/feature search. Numeric inputs,
fitted objects and predictions are persisted together for exact replay.
"""
from pathlib import Path
import argparse
import hashlib
import json
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import average_precision_score, roc_auc_score
from scripts.c1430_cv_utils import QuantileClipper
from scripts.extract_automl_c1430_cv_paths import OUT, PATH_FEATURES
from scripts.experiment_automl_limit_rebound_1430_long import SETS, COMPACT_INTRADAY
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

KEY=['symbol','d0_date','d1_date','first_limit_date']
D0=['board_streak_days','d0_open_gap','d0_range','d0_turnover_pct',
    'd0_amount_100m','d0_volume_ratio_pre5','pre5_return',
    'market_limit_count','market_up_fraction']
DROP=['d0_close_position','d0_amount_ratio_pre5','d0_turnover_ratio_first','market_median_return']
FEATURES={'d0_9':D0,'core15':D0+list(COMPACT_INTRADAY),
          'path21':D0+list(COMPACT_INTRADAY)+PATH_FEATURES,
          'clean31':[c for c in SETS['full35'] if c not in DROP],
          'full35':list(SETS['full35'])}

def configs():
    result=[dict(name='baseline35',features='full35',family='hgb',depth=3,leaves=15,
                 iterations=120,minleaf=100,l2=5),
            dict(name='strong35',features='full35',family='hgb',depth=2,leaves=8,
                 iterations=80,minleaf=200,l2=10)]
    for f in ['d0_9','core15','path21','clean31']:
        for c in [.01,.1,1.]:
            result.append(dict(name=f'{f}_logit_{c:g}',features=f,family='logit',C=c))
        for tag,depth,it,leaf in [('stump',1,80,200),('depth2',2,80,200),('small',2,40,300)]:
            result.append(dict(name=f'{f}_{tag}',features=f,family='hgb',depth=depth,
                               leaves=2**depth,iterations=it,minleaf=leaf,l2=20))
    return result

def estimator(c):
    if c['family']=='logit':
        return make_pipeline(QuantileClipper(),SimpleImputer(strategy='median',keep_empty_features=True),
                             StandardScaler(),LogisticRegression(C=c['C'],max_iter=2000,
                                                               random_state=20261014))
    return HistGradientBoostingClassifier(max_iter=c['iterations'],learning_rate=.05,
               max_depth=c['depth'],max_leaf_nodes=c['leaves'],min_samples_leaf=c['minleaf'],
               l2_regularization=c['l2'],early_stopping=False,random_state=20261013)

def fit(c, frame):
    m=estimator(c)
    weights=balanced_weights(frame.label_next3)
    kwargs={'logisticregression__sample_weight':weights} if c['family']=='logit' else {'sample_weight':weights}
    m.fit(frame[FEATURES[c['features']]],frame.label_next3,**kwargs)
    return m

def score(m,c,frame): return m.predict_proba(frame[FEATURES[c['features']]])[:,1]

def metrics(frame,s):
    y=frame.label_next3.to_numpy(int)
    z=frame[KEY+['label_next3']].copy(); z['score']=s
    z=z.sort_values(['d1_date','score','symbol'],ascending=[True,False,True])
    out=dict(rows=len(frame),days=frame.d1_date.nunique(),positive=int(y.sum()),
             prevalence=float(y.mean()),ap=float(average_precision_score(y,s)),
             auc=float(roc_auc_score(y,s)))
    for k in [1,3]:
        p=z.groupby('d1_date').head(k)
        out[f'top{k}_hits']=int(p.label_next3.sum());out[f'top{k}_n']=len(p)
        out[f'top{k}_rate']=float(p.label_next3.mean())
    out['ap_lift']=out['ap']/out['prevalence']
    return out

def purge(a,b):
    keys=set(zip(b.symbol,b.first_limit_date))
    return a[[key not in keys for key in zip(a.symbol,a.first_limit_date)]].copy()

def splits(p):
    vi=int(p.loc[p.d1_date.eq('2025-09-01'),'d1_i'].iloc[0])
    ti=int(p.loc[p.d1_date.eq('2026-01-05'),'d1_i'].iloc[0])
    v=p[p.d1_i.ge(vi)&p.d1_i.add(3).lt(ti)].copy()
    t=p[p.d1_i.ge(ti)].copy()
    return {'train':purge(p[p.d1_i.add(3).lt(vi)],v),'valid':v,
            'refit':purge(p[p.d1_i.add(3).lt(ti)],t),'test':t}

def folds(train):
    days=sorted(train.d1_date.unique()); out=[]
    for n,start in enumerate(range(len(days)-100,len(days),25),1):
        v=train[train.d1_date.isin(days[start:start+25])].copy()
        t=purge(train[train.d1_i.add(3).lt(v.d1_i.min())],v)
        assert t.d1_i.add(3).max()<v.d1_i.min()
        assert not set(zip(t.symbol,t.first_limit_date))&set(zip(v.symbol,v.first_limit_date))
        out.append((n,t,v))
    return out

def describe(x):
    return dict(rows=len(x),days=x.d1_date.nunique(),start=x.d1_date.min(),end=x.d1_date.max(),
                positive=int(x.label_next3.sum()))

def dump(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=lambda v:v.item())+'\n')

def plan():
    OUT.mkdir(parents=True,exist_ok=True)
    p=pd.read_csv('outputs/stock_automl/limit_rebound_1430_long_20261014/panel.csv.gz',
                  dtype={'symbol':str,'stk_code':str},float_precision='round_trip')
    sets=splits(p); train=sets['train']
    x=train[FEATURES['full35']]
    audit=[dict(feature=c,missing=int(x[c].isna().sum()),infinite=int(np.isinf(x[c]).sum()),
                unique=int(x[c].nunique()),median=float(x[c].replace([np.inf,-np.inf],np.nan).median())) for c in x]
    pd.DataFrame(audit).to_csv(OUT/'feature_audit_train.csv',index=False)
    corr=x.replace([np.inf,-np.inf],np.nan).corr(method='spearman')
    pairs=[dict(a=a,b=b,spearman=float(corr.loc[a,b])) for i,a in enumerate(x) for b in list(x)[i+1:]
           if abs(corr.loc[a,b])>.9]
    dump('plan.json',dict(configs=configs(),feature_sets=FEATURES,removed_from_clean31=DROP,
        correlated_pairs=pairs,splits={k:describe(v) for k,v in sets.items()},
        folds=[dict(fold=n,train=describe(t),valid=describe(v)) for n,t,v in folds(train)],
        selection='Mean four-fold AP/prevalence. Retain maximum and one-SE simpler model. '
                  'Simplicity order: fewer columns, then logistic before trees, then lower capacity. '
                  'Top1/Top3 secondary diagnostics. Freeze selection before opening holdout results.',
        caveat='2025 validation and 2026 test already studied historically, not pristine blind tests.',
        path_definition=dict(zip(PATH_FEATURES,[
          '14:30 close /14:00 close -1','sqrt sum squared 5-minute log returns, 09:35..14:30',
          'min running close/open drawdown to 14:30','signed net move / sum absolute minute moves',
          '14:01..14:30 amount /13:31..14:00 amount','13:01..14:30 amount / prefix amount']))))
    print('Plan frozen; configs',len(configs()),flush=True)

def simplicity(c):
    return (len(FEATURES[c['features']]),0 if c['family']=='logit' else 1,
            c.get('C',c.get('depth',0)),c.get('iterations',0))

def run():
    p=pd.read_pickle(OUT/'panel.pkl')
    allcols=list(dict.fromkeys(c for fs in FEATURES.values() for c in fs))
    p[allcols]=p[allcols].replace([np.inf,-np.inf],np.nan)
    p.to_pickle(OUT/'numeric_snapshot.pkl')
    sets=splits(p); ff=folds(sets['train'])
    assert len(sets['train'])==8021 and len(sets['valid'])==3725 and len(sets['test'])==8869
    assert not p.d0_st.any() and not p.d1_st.any()
    rows=[]; oof={}
    for c in configs():
        predictions=[]
        for n,t,v in ff:
            m=fit(c,t); s=score(m,c,v); met=metrics(v,s)
            fitted_met=metrics(t,score(m,c,t))
            rows.append(dict(name=c['name'],fold=n,features=len(FEATURES[c['features']]),
                             fit_ap=fitted_met['ap'],fit_top1=fitted_met['top1_rate'],**met))
            z=v[KEY+['label_next3']].copy(); z['score']=s;z['fold']=n; predictions.append(z)
        oof[c['name']]=pd.concat(predictions)
        oof[c['name']].to_pickle(OUT/f"{c['name']}_oof.pkl")
        print(c['name'],'CV AP lift',round(np.mean([x['ap_lift'] for x in rows if x['name']==c['name']]),4),flush=True)
    cv=pd.DataFrame(rows);cv.to_csv(OUT/'cv_folds.csv',index=False)
    leaderboard=cv.groupby('name').agg(mean_ap=('ap','mean'),mean_lift=('ap_lift','mean'),
                    sd_lift=('ap_lift','std'),mean_auc=('auc','mean'),
                    top1_hits=('top1_hits','sum'),top1_n=('top1_n','sum'),
                    top3_hits=('top3_hits','sum'),top3_n=('top3_n','sum')).reset_index()
    leaderboard['se_lift']=leaderboard.sd_lift/2
    leaderboard=leaderboard.sort_values(['mean_lift','name'],ascending=[False,True])
    leaderboard.to_csv(OUT/'cv_leaderboard.csv',index=False)
    best=leaderboard.iloc[0];cut=float(best.mean_lift-best.se_lift)
    candidates=[c for c in configs() if c['name'] in set(leaderboard[leaderboard.mean_lift.ge(cut)].name)]
    simple=min(candidates,key=simplicity)
    chosen=list(dict.fromkeys(['baseline35',str(best['name']),simple['name']]))
    dump('selection_frozen.json',dict(best=str(best['name']),one_se=simple['name'],
        one_se_cut=cut,holdout_models=chosen,selection_inputs='cv_folds.csv; train period only',
        cv_se_note='Four dependent chronological folds: one-SE is a simplicity heuristic, not a confidence bound.'))
    results={}
    for name in chosen:
        c=next(c for c in configs() if c['name']==name)
        m=fit(c,sets['train']);refit=fit(c,sets['refit'])
        joblib.dump(dict(config=c,columns=FEATURES[c['features']],model=m),OUT/f'{name}_train.joblib')
        joblib.dump(dict(config=c,columns=FEATURES[c['features']],model=refit),OUT/f'{name}_refit.joblib')
        results[name]={'config':c,'features':FEATURES[c['features']],
                       'oof':metrics(oof[name],oof[name].score.to_numpy())}
        for part,fitted in [('train',m),('valid',m),('test',refit)]:
            z=sets[part][KEY+['label_next3']].copy();s=score(fitted,c,sets[part]);z['score']=s
            z.to_pickle(OUT/f'{name}_{part}_scores.pkl')
            results[name][part]=metrics(sets[part],s)
        print('HOLDOUT',name,json.dumps({k:results[name][k] for k in ['valid','test']}),flush=True)
    dump('results.json',dict(experiment='C1430-C',sklearn=sklearn.__version__,models=results,
         splits={k:describe(v) for k,v in sets.items()},selected=chosen))
    importance=[]
    groups={'D0形态':['board_streak_days','streak_return','d0_return','d0_open_gap','d0_range','d0_lower_shadow',
                   'first_open_gap','first_range','first_one_price','pre5_return'],
            'D0量能':[c for c in FEATURES['full35'] if ('volume' in c or 'amount' in c or 'turnover' in c) and not c.startswith('d1_')],
            '市场环境':[c for c in FEATURES['full35'] if c.startswith('market_')],
            '日内价格':[c for c in FEATURES['full35'] if c.startswith('d1_') and 'amount' not in c],
            '日内成交':[c for c in COMPACT_INTRADAY if 'amount' in c],
            '新增路径':PATH_FEATURES}
    for name in chosen:
        c=next(c for c in configs() if c['name']==name)
        for n,t,v in ff:
            m=fit(c,t);base=average_precision_score(v.label_next3,score(m,c,v))
            for group,cols in groups.items():
                cols=[a for a in cols if a in FEATURES[c['features']]]
                if not cols: continue
                for repeat in range(3):
                    shuffled=v.copy();rng=np.random.default_rng(1000*n+repeat)
                    # Market features share a value within day: permute whole day vectors.
                    if group=='市场环境':
                        d=sorted(v.d1_date.unique()); perm=dict(zip(d,rng.permutation(d)))
                        table=v.groupby('d1_date')[cols].first()
                        shuffled[cols]=table.reindex(v.d1_date.map(perm)).to_numpy()
                    else:
                        for _,idx in v.groupby('d1_date').groups.items():
                            shuffled.loc[idx,cols]=v.loc[rng.permutation(idx),cols].to_numpy()
                    ap=average_precision_score(v.label_next3,score(m,c,shuffled))
                    importance.append(dict(name=name,fold=n,group=group,repeat=repeat,ap_drop=base-ap))
    pd.DataFrame(importance).to_csv(OUT/'group_permutation_train_cv.csv',index=False)
    manifest={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in OUT.iterdir()
              if f.is_file() and f.name!='manifest.json'}
    dump('manifest.json',manifest)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',action='store_true');args=parser.parse_args()
    plan() if args.plan else run()
