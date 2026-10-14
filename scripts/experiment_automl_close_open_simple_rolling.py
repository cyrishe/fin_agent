"""CO5: causally selected compact/all-factor simple models versus sklearn 1.7.1."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import warnings
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.exceptions import ConvergenceWarning
from threadpoolctl import threadpool_limits
from scripts.build_automl_close_open_factor_panel import DATA as SOURCE, FACTORS, BASE, BINARY, sha
from scripts.experiment_automl_second_high_four_class import models, four_class
from scripts.analyze_automl_cost_robustness import net_return

OUT = Path('outputs/stock_automl/close_open_simple_rolling_r1')
REPORT = Path('docs/stock_automl_runs/20261014_close_open_simple_rolling')
PANEL_SHA = '692b84cc7b36f64dacff4f5b45f0a07e1f3d162c42d7a37c685964a8b2020bcc'
KEY = ['signal_date','symbol6']
LOG = {'volume_ratio','turnover_so_far_pct','float_mv_100m_cny','relative_turnover5','relative_turnover20'}
PARAMS = {'ridge':[10000.,1000.,100.], 'logit':[.001,.01,.1]}


def utility(gross):
    net = net_return(pd.Series(np.asarray(gross)),50_000,.00025).to_numpy()
    return np.minimum(net,.05)-np.maximum(-net-.02,0)


def mature(frame, days, cutoff):
    return frame[frame.signal_date.isin(days)&frame.next_date.le(cutoff)&
                 frame[['strong','weak','close40_return']].notna().all(axis=1)].copy()


def split_days(calendar, first):
    past = [d for d in calendar if d < first][-100:]
    if len(past)!=100:
        raise ValueError('100 prior market dates required')
    return past[:80],past[80:]


def associations(frame):
    records=[]
    for day,g in frame.groupby('signal_date',sort=True):
        x = g[list(FACTORS)].astype(float)
        rank = (x.rank(method='average')-.5)/x.notna().sum()
        hi,lo = rank.ge(.8),rank.le(.2)
        for name in BINARY:
            hi[name],lo[name] = x[name].eq(1),x[name].eq(0)
        y = g.strong-g.weak
        delta = hi.mul(y,axis=0).sum()/hi.sum()-lo.mul(y,axis=0).sum()/lo.sum()
        delta[(hi.sum()<5)|(lo.sum()<5)] = np.nan
        records += [{'date':day,'factor':k,'delta':v} for k,v in delta.items()]
    return pd.DataFrame(records)


def compact_factors(train, daily, count=12):
    # The caller supplies only mature discovery rows. No future association is read.
    dates = set(train.signal_date)
    d = daily[daily.date.isin(dates)].copy()
    d['month'] = d.date.str[:7]
    stats = d.groupby('factor').delta.agg(['mean','count'])
    monthly = d.groupby(['factor','month']).delta.mean().unstack()
    consistency = monthly.mul(np.sign(stats['mean']),axis=0).gt(0).sum(axis=1)/monthly.notna().sum(axis=1)
    stats['consistency'] = consistency
    stats['score'] = stats['mean'].abs()*consistency
    stats = stats[stats['count'].ge(20)].sort_values(['score','mean'],ascending=[False,False],kind='stable')
    corr = train[list(FACTORS)].corr(method='spearman').abs()
    chosen=[]
    for f in stats.index:
        if not chosen or not corr.loc[f,chosen].ge(.85).any():
            chosen.append(f)
        if len(chosen)==count: break
    if len(chosen)<7:
        raise ValueError('Fewer than seven estimable training factors')
    stats['selected'] = stats.index.isin(chosen)
    return chosen,stats.reset_index(names='factor')


def raw_x(frame, factors):
    x = frame[factors].to_numpy(float).copy()
    for j,f in enumerate(factors):
        if f in LOG: x[:,j] = np.log1p(np.maximum(x[:,j],0))
    x[~np.isfinite(x)] = np.nan
    return x


def preprocess(frame,factors):
    x = raw_x(frame,factors)
    med = np.nanmedian(x,axis=0); med = np.nan_to_num(med)
    low,high = np.nanquantile(x,[.01,.99],axis=0)
    low = np.where(np.isfinite(low),low,med); high=np.where(np.isfinite(high),high,med)
    for j,f in enumerate(factors):
        if f in BINARY: low[j],high[j]=0,1
    x = np.clip(np.where(np.isnan(x),med,x),low,high)
    mean,scale = x.mean(axis=0),x.std(axis=0)
    scale[scale<1e-10]=1
    for j,f in enumerate(factors):
        if f in BINARY: mean[j],scale[j]=0,1
    return {'factors':factors,'median':med,'low':low,'high':high,'mean':mean,'scale':scale}


def transform(frame,p):
    x=raw_x(frame,p['factors'])
    return (np.clip(np.where(np.isnan(x),p['median'],x),p['low'],p['high'])-p['mean'])/p['scale']


def fit_simple(train,factors,kind,param):
    p=preprocess(train,factors)
    x=transform(train,p)
    w=1/train.groupby('signal_date').signal_date.transform('count').to_numpy()
    w *= len(w)/w.sum()
    if kind=='ridge':
        model=Ridge(alpha=param,solver='cholesky')
        y=utility(train.close40_return)
    else:
        model=LogisticRegression(C=param,solver='lbfgs',max_iter=1500,tol=1e-7)
        y=np.where(train.strong.eq(1),2,np.where(train.weak.eq(1),0,1))
    with warnings.catch_warnings():
        warnings.simplefilter('error',ConvergenceWarning)
        model.fit(x,y,sample_weight=w)
    return {'preprocess':p,'model':model,'kind':kind,'parameter':param}


def scores(fitted,frame):
    x=transform(frame,fitted['preprocess'])
    if fitted['kind']=='ridge':
        return pd.DataFrame({'score':fitted['model'].predict(x)},index=frame.index)
    prob=fitted['model'].predict_proba(x)
    out={int(c):prob[:,i] for i,c in enumerate(fitted['model'].classes_)}
    strong,weak=out.get(2,np.zeros(len(frame))),out.get(0,np.zeros(len(frame)))
    return pd.DataFrame({'score':strong-weak,'p_strong':strong,'p_weak':weak},index=frame.index)


def top1(frame,scored):
    # Ranking only receives identifiers and scores; labels are joined afterwards.
    r=frame[KEY].join(scored)
    r=r[np.isfinite(r.score)]
    return r.sort_values(['signal_date','score','symbol6'],ascending=[True,False,True],kind='stable').drop_duplicates('signal_date').index


def tune(train,val,factors,kind):
    history=[]
    for param in PARAMS[kind]:
        fitted=fit_simple(train,factors,kind,param)
        idx=top1(val,scores(fitted,val))
        u=utility(val.loc[idx,'close40_return'])
        score=float(np.delete(u,np.argmax(u)).mean()) if len(u)>1 else -np.inf
        history.append({'parameter':param,'score':score,'validation_trades':len(u)})
    # PARAMS lists stronger shrinkage first, stable tie-breaking.
    best=max(range(len(history)),key=lambda i:history[i]['score'])
    return history[best]['parameter'],history


def retained_rows(frame):
    keys=frame.signal_date+'|'+frame.symbol6
    return keys.map(lambda k:int(hashlib.sha256(k.encode()).hexdigest()[:8],16)%50!=0).to_numpy()


def record_score(frame,scored,model):
    out=frame[KEY].join(scored)
    out['model']=model
    return out


def picks_from_scores(frame,scored,model,days):
    idx=top1(frame,scored)
    cols=KEY+['next_date','signal_price','second_high','max_high','close40','strong','weak','close40_return','next_corporate_action']
    p=frame.loc[idx,cols].join(scored.loc[idx])
    p=pd.DataFrame({'signal_date':days}).merge(p,on='signal_date',how='left',validate='one_to_one')
    p['model']=model
    return p


def tree_scores(model,frame):
    proba=model.predict_proba(frame[list(BASE)])
    labels=np.asarray(model.classes_)
    predicted=labels[proba.argmax(axis=1)]
    probs={c:proba[:,i] for i,c in enumerate(labels)}
    p3=probs.get('ge3',np.zeros(len(frame)))
    p1=probs.get('1to3',np.zeros(len(frame)))
    score=np.where(predicted=='ge3',2+p3,np.where(predicted=='1to3',1+p3+p1,-np.inf))
    return pd.DataFrame({'score':score,'p_ge3':p3,'p_ge1':p3+p1,'predicted_class':predicted},index=frame.index)


def original_b1(panel):
    source=Path('docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top1_verified.csv')
    b=pd.read_csv(source,dtype={'symbol6':str},float_precision='round_trip')
    rows=[]
    for r in b.itertuples():
        morning=pd.read_csv(SOURCE/'days'/f'{r.next_date}_morning.csv.gz',dtype={'symbol6':str},float_precision='round_trip')
        m=morning[morning.symbol6.eq(r.symbol6)]
        maximum=float(m.max_high.iloc[0]) if len(m) else np.nan
        rows.append({'signal_date':r.signal_date,'symbol6':r.symbol6,'next_date':r.next_date,
                     'signal_price':r.signal_price,'second_high':r.second_high,'max_high':maximum,
                     'close40':r.close_40,'strong':float(r.second_high>=r.signal_price*1.03-1e-10),
                     'weak':float(maximum<r.signal_price-1e-10) if np.isfinite(maximum) else np.nan,
                     'close40_return':r.actual_0940_return,'model':'B1_frozen_original171',
                     'in_co4_pool':bool(((panel.signal_date==r.signal_date)&(panel.symbol6==r.symbol6)).any())})
    return pd.DataFrame(rows)


def run(months=None):
    if sklearn.__version__!='1.7.1': raise ValueError('Requires sklearn 1.7.1')
    source=SOURCE/'factor_panel.csv.gz'
    if sha(source)!=PANEL_SHA: raise ValueError('Frozen panel hash changed')
    OUT.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    panel=pd.read_csv(source,dtype={'symbol6':str},float_precision='round_trip')
    panel=panel.sort_values(KEY).reset_index(drop=True)
    if panel.duplicated(KEY).any() or not panel.entry_eligible.all(): raise ValueError('Invalid candidate pool')
    calendar=pd.read_csv(SOURCE/'availability.csv').date.tolist()
    allmonths=sorted(d for d in panel.signal_date.str[:7].unique() if d>='2025-07')
    selected=allmonths if months is None else months
    assocfile=OUT/'training_daily_associations.csv.gz'
    if assocfile.exists(): daily=pd.read_csv(assocfile)
    else:
        daily=associations(panel.dropna(subset=['strong','weak','close40_return']))
        daily.to_csv(assocfile,index=False)
    (OUT/'models').mkdir(exist_ok=True)
    for month in selected:
        folder=OUT/'folds'/month;folder.mkdir(parents=True,exist_ok=True)
        if (folder/'complete.json').exists(): print(month,'cached',flush=True);continue
        started=time.time()
        test=panel[panel.signal_date.str.startswith(month)]
        testdays=sorted(test.signal_date.unique());first=testdays[0]
        train_days,val_days=split_days(calendar,first)
        train=mature(panel,train_days,val_days[0]);val=mature(panel,val_days,first)
        full=mature(panel,train_days+val_days,first)
        factors,selection=compact_factors(train,daily)
        selection.to_csv(folder/'factor_selection.csv',index=False)
        groups={'seven7':list(BASE),'compact12':factors,'broad76':list(FACTORS)}
        records=[];predictions=[];picks=[];manifests=[]
        for group,cols in groups.items():
            for kind in PARAMS:
                model_id=f'{kind}_{group}'
                param,history=tune(train,val,cols,kind)
                records.extend({'model':model_id,**h,'chosen':h['parameter']==param} for h in history)
                for perturb in (False,True):
                    name=model_id+('_drop2pct' if perturb else '')
                    fitdata=full.loc[retained_rows(full)] if perturb else full
                    fitted=fit_simple(fitdata,cols,kind,param)
                    scored=scores(fitted,test)
                    predictions.append(record_score(test,scored,name))
                    picks.append(picks_from_scores(test,scored,name,testdays))
                    model_file=OUT/'models'/f'{month}_{name}.joblib'
                    joblib.dump(fitted,model_file,compress=3)
                    manifests.append({'model':name,'file':str(model_file),'sha256':sha(model_file),'factors':cols,
                                      'parameter':param,'fit_rows':len(fitdata),'fit_first':fitdata.signal_date.min(),
                                      'fit_last':fitdata.signal_date.max(),'max_label_date':fitdata.next_date.max()})
                print(month,model_id,'done',flush=True)
        # Same-configuration control: daily 20-market-day fit, original class-priority ranking.
        for day in testdays:
            pos=calendar.index(day)
            historical=calendar[pos-20:pos]
            tr=mature(panel,historical,day)
            te=test[test.signal_date.eq(day)]
            for perturb in (False,True):
                name='tree171_seven7_samepool'+('_drop2pct' if perturb else '')
                fitdata=tr.loc[retained_rows(tr)] if perturb else tr
                model=models()['small_boosted_tree']
                model.fit(fitdata[list(BASE)],four_class(fitdata.second_high/fitdata.signal_price-1).astype(str))
                scored=tree_scores(model,te)
                predictions.append(record_score(te,scored,name))
                picks.append(picks_from_scores(te,scored,name,[day]))
                model_file=OUT/'models'/f'{day}_{name}.joblib'
                joblib.dump(model,model_file,compress=3)
                manifests.append({'model':name,'file':str(model_file),'sha256':sha(model_file),'factors':list(BASE),
                                  'fit_rows':len(fitdata),'fit_first':fitdata.signal_date.min(),'fit_last':fitdata.signal_date.max(),
                                  'max_label_date':fitdata.next_date.max(),'decision_day':day})
        pd.concat(predictions,ignore_index=True).to_csv(folder/'predictions.csv.gz',index=False,float_format='%.17g')
        pd.concat(picks,ignore_index=True).to_csv(folder/'picks.csv',index=False,float_format='%.17g')
        pd.DataFrame(records).to_csv(folder/'validation.csv',index=False)
        meta={'month':month,'first_test':first,'discovery_dates':train_days,'validation_dates':val_days,
              'discovery_rows':len(train),'validation_rows':len(val),'refit_rows':len(full),
              'discovery_label_max':train.next_date.max(),'validation_label_max':val.next_date.max(),
              'test_rows':len(test),'test_days':testdays,'models':manifests,'seconds':time.time()-started}
        (folder/'complete.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        print(month,'COMPLETE',round(time.time()-started,1),flush=True)
    if selected==allmonths:
        picks=pd.concat([pd.read_csv(OUT/'folds'/m/'picks.csv',dtype={'symbol6':str},float_precision='round_trip') for m in allmonths],ignore_index=True)
        picks=pd.concat([picks,original_b1(panel)],ignore_index=True)
        picks.to_csv(OUT/'picks_before_amount_gate.csv',index=False,float_format='%.17g')
        manifest={'panel':str(source),'panel_sha256':PANEL_SHA,'sklearn':sklearn.__version__,
                  'python':subprocess.check_output(['.venv-automl/bin/python','--version'],text=True).strip(),
                  'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                  'design_sha256':sha(REPORT/'DESIGN.md'),'code_sha256':sha(Path(__file__)),
                  'test_months':allmonths,'source_rows':len(panel),'predicted_months':len(allmonths)}
        (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print('ALL COMPLETE',len(picks),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--months',nargs='+')
    args=parser.parse_args()
    with threadpool_limits(limits=1): run(args.months)
