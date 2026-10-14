"""CO6 actual net-loss classification, ordinal loss, fuzzy targets and risk veto."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import warnings
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss, confusion_matrix
from threadpoolctl import threadpool_limits
from scripts.experiment_automl_close_open_simple_rolling import (
    SOURCE, OUT as CO5, FACTORS, PANEL_SHA, sha, mature, split_days,
    preprocess, transform, picks_from_scores, net_return,
)

OUT=Path('outputs/stock_automl/close_open_critical_risk_r1')
REPORT=Path('docs/stock_automl_runs/20261014_close_open_critical_risk')
BOUNDS=np.array([-.02,0.,.01,.03])
CLASSES=('critical','negative','small','acceptable','strong')
C=.01
KINDS=('risk_binary','nominal','ordinal','ordinal_critical','ordinal_critical_fuzzy')


def net_values(frame):
    return net_return(frame.close40_return,50_000,.00025).to_numpy()


def net_classes(net):
    r=np.asarray(net,float)
    if not np.isfinite(r).all(): raise ValueError('Unknown outcomes must not become a class')
    return np.searchsorted(BOUNDS,r,side='right')


def ordinal_targets(net,fuzzy=False):
    r=np.asarray(net,float)
    if not np.isfinite(r).all(): raise ValueError('Unknown target')
    q=(r[:,None]>=BOUNDS).astype(float)
    if fuzzy:q[:,1:]=np.clip((r[:,None]-BOUNDS[1:]+.002)/.004,0,1)
    return q


def day_weights(frame):
    w=1/frame.groupby('signal_date').signal_date.transform('count').to_numpy()
    return w*len(w)/w.sum()


def expanded_targets(x,q,w):
    # A soft Bernoulli label equals two hard observations with fractional weight.
    xx=np.concatenate([x,x]);yy=np.r_[np.zeros(len(x),int),np.ones(len(x),int)]
    ww=np.r_[w*(1-q),w*q]
    keep=ww>0
    return xx[keep],yy[keep],ww[keep]


def fit_logit(x,y,w):
    if len(np.unique(y))<2: return {'constant':int(y[0])}
    model=LogisticRegression(C=C,solver='lbfgs',max_iter=1500,tol=1e-7)
    with warnings.catch_warnings():
        warnings.simplefilter('error',ConvergenceWarning)
        model.fit(x,y,sample_weight=w)
    return model


def probability(model,x,classes):
    p=np.zeros((len(x),len(classes)))
    if isinstance(model,dict):
        p[:,classes.index(model['constant'])]=1
    else:
        raw=model.predict_proba(x)
        for i,label in enumerate(model.classes_):p[:,classes.index(int(label))]=raw[:,i]
    return p


def fit(frame,kind):
    prep=preprocess(frame,list(FACTORS));x=transform(frame,prep)
    net=net_values(frame);w=day_weights(frame)
    if kind=='risk_binary':fitted=[fit_logit(x,(net<-.02).astype(int),w)]
    elif kind=='nominal':fitted=[fit_logit(x,net_classes(net),w)]
    else:
        target=ordinal_targets(net,kind.endswith('fuzzy'))
        if 'critical' in kind:
            w=w*np.where(net<-.02,3.,1.)
            w*=len(w)/w.sum()
        fitted=[fit_logit(*expanded_targets(x,target[:,j],w)) for j in range(4)]
    return {'kind':kind,'preprocess':prep,'estimators':fitted,'C':C}


def project_cumulative(raw):
    monotone=np.minimum.accumulate(np.clip(raw,0,1),axis=1)
    return np.column_stack([1-monotone[:,0],monotone[:,:-1]-monotone[:,1:],monotone[:,-1]])


def predict(fitted,frame):
    x=transform(frame,fitted['preprocess']);kind=fitted['kind']
    if kind=='risk_binary':return probability(fitted['estimators'][0],x,[0,1])[:,1]
    if kind=='nominal':return probability(fitted['estimators'][0],x,list(range(5)))
    raw=np.column_stack([probability(m,x,[0,1])[:,1] for m in fitted['estimators']])
    return project_cumulative(raw)


def cost_matrix():
    actual=np.arange(5)[:,None];predicted=np.arange(5)[None,:]
    distance=np.abs(predicted-actual)
    costs=np.where(predicted>actual,distance**2,distance).astype(float)
    costs[0,3:]*=2
    return costs


def decision_scores(p,rule,index):
    values=np.array([-.04 if rule=='nominal' else -.08,-.01,.005,.02,.04])
    utility=p@values
    risk_class=(p@cost_matrix()).argmin(axis=1)
    eligible=utility>0
    if rule=='cost_matrix':eligible&=risk_class>=3
    out=pd.DataFrame({f'mass_{c}':p[:,j] for j,c in enumerate(CLASSES)},index=index)
    out['predicted_net_class']=p.argmax(axis=1)
    out['risk_decision_class']=risk_class
    out['decision_utility']=utility
    out['score']=np.where(eligible,utility,-np.inf)
    return out


def diagnostic(frame,p,kind,month,scope):
    y=(net_values(frame)<-.02).astype(int)
    risk=p if kind=='risk_binary' else p[:,0]
    weights=day_weights(frame)
    result={'month':month,'scope':scope,'kind':kind,'rows':len(frame),
            'critical_rate':float(np.average(y,weights=weights)),
            'mean_risk_score':float(np.average(risk,weights=weights)),
            'critical_auc':float(roc_auc_score(y,risk,sample_weight=weights)),
            'critical_brier':float(brier_score_loss(y,risk,sample_weight=weights)),
            'critical_logloss':float(log_loss(y,np.c_[1-risk,risk],sample_weight=weights,labels=[0,1]))}
    if kind!='risk_binary':
        actual=net_classes(net_values(frame));pred=p.argmax(axis=1)
        result.update({'confusion':confusion_matrix(actual,pred,labels=list(range(5))).tolist(),
                       'critical_as_acceptable_or_strong':int(((actual==0)&(pred>=3)).sum()),
                       'critical_count':int((actual==0).sum()),
                       'mean_overoptimistic_cost':float(np.average(cost_matrix()[actual,pred],weights=weights))})
    return result


def run():
    OUT.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    if sha(SOURCE/'factor_panel.csv.gz')!=PANEL_SHA:raise ValueError('Changed panel')
    panel=pd.read_csv(SOURCE/'factor_panel.csv.gz',dtype={'symbol6':str},float_precision='round_trip').sort_values(['signal_date','symbol6']).reset_index(drop=True)
    calendar=pd.read_csv(SOURCE/'availability.csv').date.tolist()
    months=sorted(panel.loc[panel.signal_date.ge('2025-07-01'),'signal_date'].str[:7].unique())
    for month in months:
        folder=OUT/'folds'/month;folder.mkdir(parents=True,exist_ok=True)
        if (folder/'complete.json').exists():print(month,'cached',flush=True);continue
        test=panel[panel.signal_date.str.startswith(month)];days=sorted(test.signal_date.unique());first=days[0]
        tr_days,val_days=split_days(calendar,first)
        train=mature(panel,tr_days,val_days[0]);val=mature(panel,val_days,first)
        full=mature(panel,tr_days+val_days,first)
        models_meta=[];diags=[];scored_rows=[];picks=[]
        risk_test=None
        for kind in KINDS:
            discovered=fit(train,kind)
            pv=predict(discovered,val)
            diags.append(diagnostic(val,pv,kind,month,'validation'))
            if kind=='risk_binary':
                val[['signal_date','symbol6','close40_return']].assign(risk=pv).to_csv(folder/'validation_risk.csv.gz',index=False)
            fitted=fit(full,kind);pt=predict(fitted,test)
            for phase,obj,rows in [('discovery',discovered,train),('refit',fitted,full)]:
                path=folder/f'{kind}_{phase}.joblib';joblib.dump(obj,path,compress=3)
                models_meta.append({'kind':kind,'phase':phase,'file':str(path),'sha256':sha(path),'rows':len(rows),
                                    'first':rows.signal_date.min(),'last':rows.signal_date.max(),'label_max':rows.next_date.max(),
                                    'features':list(FACTORS),'C':C})
            known=test[['strong','weak','close40_return']].notna().all(axis=1)
            diags.append(diagnostic(test.loc[known],pt[known],kind,month,'test'))
            if kind=='risk_binary':
                risk_test=test[['signal_date','symbol6']].assign(p_critical=pt)
            else:
                variants=('nominal','cost_decision','cost_matrix') if kind=='nominal' else (kind,)
                for rule in variants:
                    name='net5_'+rule
                    scored=decision_scores(pt,rule,test.index)
                    scored_rows.append(test[['signal_date','symbol6']].join(scored).assign(model=name))
                    picks.append(picks_from_scores(test,scored,name,days))
            print(month,kind,'done',flush=True)
        risk_test.to_csv(folder/'risk_scores.csv.gz',index=False,float_format='%.17g')
        pd.concat(scored_rows,ignore_index=True).to_csv(folder/'predictions.csv.gz',index=False,float_format='%.17g')
        pd.concat(picks,ignore_index=True).to_csv(folder/'picks.csv',index=False,float_format='%.17g')
        (folder/'diagnostics.json').write_text(json.dumps(diags,indent=2)+'\n')
        for phase,rows in [('discovery',train),('validation',val),('refit',full)]:
            member=rows[['signal_date','symbol6','next_date']].copy();member.insert(0,'sample_id',rows.index)
            member['day_weight']=day_weights(rows)
            weighted=member.day_weight*np.where(net_values(rows)<-.02,3.,1.)
            member['critical_weight']=weighted*(len(rows)/weighted.sum())
            member.to_csv(folder/f'{phase}_membership.csv.gz',index=False,float_format='%.17g')
        meta={'month':month,'discovery_dates':tr_days,'validation_dates':val_days,'test_dates':days,
              'discovery_label_max':train.next_date.max(),'validation_label_max':val.next_date.max(),
              'models':models_meta,'test_rows':len(test)}
        (folder/'complete.json').write_text(json.dumps(meta,indent=2)+'\n')
        print(month,'COMPLETE',flush=True)
    pd.concat([pd.read_csv(OUT/'folds'/m/'picks.csv',dtype={'symbol6':str},float_precision='round_trip') for m in months],ignore_index=True).to_csv(OUT/'new_picks.csv',index=False)
    pd.concat([pd.read_csv(OUT/'folds'/m/'risk_scores.csv.gz',dtype={'symbol6':str},float_precision='round_trip') for m in months],ignore_index=True).to_csv(OUT/'risk_scores.csv.gz',index=False,float_format='%.17g')
    manifest={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'panel_sha256':PANEL_SHA,'script_sha256':sha(Path(__file__)),'design_sha256':sha(REPORT/'DESIGN.md'),
              'months':months,'classes':list(CLASSES),'cost_matrix':cost_matrix().tolist(),'C':C}
    (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('ALL COMPLETE',flush=True)

if __name__=='__main__':
    with threadpool_limits(limits=1):run()
