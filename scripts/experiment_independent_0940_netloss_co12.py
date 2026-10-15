"""Independent whole-market 09:40 severe-loss probability and conditional severity."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import time
import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from scripts.build_independent_0940_netloss_co12 import OUT, REPORT, CO10, CO8, KEY, FEATURES, read, sha, save_json
from scripts.experiment_b1_calibrated_risk_co11 import calibrate, probability, strong

CO11=Path('outputs/stock_automl/b1_calibrated_risk_co11')
REPORT11=Path('docs/stock_automl_runs/20261015_b1_calibrated_risk_co11')
PARAMS=dict(max_iter=60,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=100,
            l2_regularization=10,max_bins=63,early_stopping=False,random_state=1015)


def preprocess(m,d):
    x=d[FEATURES].replace([np.inf,-np.inf],np.nan).clip(m['lo'],m['hi'],axis=1).fillna(m['median'])
    return (x-m['mean'])/m['std']


def fit_heads(d):
    used=d[d.label_known].copy()
    assert used.severe_loss.sum()>=100 and (~used.severe_loss).sum()>=100
    x=used[FEATURES].replace([np.inf,-np.inf],np.nan)
    lo=x.quantile(.01);hi=x.quantile(.99);median=x.median().fillna(0.)
    x=x.clip(lo,hi,axis=1).fillna(median);mean=x.mean();std=x.std(ddof=0).replace(0,1).fillna(1.)
    x=(x-mean)/std
    weights=1/used.groupby('signal_date').sample_id.transform('size');weights=weights/weights.mean()
    classifier=HistGradientBoostingClassifier(**PARAMS)
    classifier.fit(x,used.severe_loss.astype(int),sample_weight=weights)
    tail=used.severe_loss
    tw=weights[tail];tw=tw/tw.mean()
    regressor=HistGradientBoostingRegressor(loss='squared_error',**PARAMS)
    regressor.fit(x[tail],used.loc[tail,'severity'],sample_weight=tw)
    used['probability_weight']=weights
    used['severity_weight']=np.nan;used.loc[tail,'severity_weight']=tw
    return dict(classifier=classifier,regressor=regressor,lo=lo,hi=hi,median=median,
                mean=mean,std=std,features=list(FEATURES)),used


def raw_predict(m,d):
    x=preprocess(m,d)
    return m['classifier'].predict_proba(x)[:,1],np.clip(m['regressor'].predict(x),.02,1.)


def severity_factor(d,weights):
    selected=d.label_known&d.severe_loss
    z=d[selected];w=weights.loc[z.index]
    if len(z)==0:raise ValueError('No matured severity calibration events')
    ratio=float(np.sum(w*z.severity)/np.sum(w*z.raw_severity))
    return dict(events=len(z),raw_ratio=ratio,factor=float(np.clip(ratio,.5,2.)),
                observed_mean=float(np.average(z.severity,weights=w)))


def fit_calibration(d):
    known=d[d.label_known].copy()
    weights=1/known.groupby('signal_date').sample_id.transform('size');weights=weights/weights.mean()
    full=calibrate(known.rename(columns={'severe_loss':'big_drop','raw_probability':'risk_score'}))
    subset=known[strong(known)]
    sub=calibrate(subset.rename(columns={'severe_loss':'big_drop','raw_probability':'risk_score'}))
    sg=severity_factor(known,weights)
    sw=1/subset.groupby('signal_date').sample_id.transform('size');sw=sw/sw.mean()
    ss=severity_factor(subset,sw) if subset.severe_loss.sum()>=30 else dict(sg,fallback='fewer_than_30_strong_events')
    return dict(global_probability=full,strong_probability=sub,global_severity=sg,strong_severity=ss)


def calibrated_predict(cal,d):
    p=probability(cal['global_probability'],d.raw_probability)
    p=np.where(strong(d),probability(cal['strong_probability'],d.raw_probability),p)
    mult=np.where(strong(d),cal['strong_severity']['factor'],cal['global_severity']['factor'])
    severity=np.clip(d.raw_severity.to_numpy()*mult,.02,1.)
    return pd.DataFrame(dict(p_severe=p,conditional_severity=severity,tail_expected_loss=p*severity),index=d.index)


def predict_bundle(m,d):
    if list(m['features'])!=list(FEATURES):raise ValueError('Feature schema mismatch')
    if 'feature_cutoff' not in d or 'signal_date' not in d:raise ValueError('Declared feature time required')
    cutoff=pd.to_datetime(d.feature_cutoff)
    if cutoff.isna().any() or not (cutoff<=pd.to_datetime(d.signal_date+' 14:40:00')).all():
        raise ValueError('Feature cutoff exceeds decision time')
    if not d.signal_date.str.startswith(m['month']).all():raise ValueError('Wrong deployment month')
    z=d.copy();z['raw_probability'],z['raw_severity']=raw_predict(m,z)
    return calibrated_predict(m['calibration'],z)


def input_files():
    return [Path('scripts/experiment_independent_0940_netloss_co12.py'),Path('scripts/build_independent_0940_netloss_co12.py'),
        Path('scripts/report_independent_0940_netloss_co12.py'),Path('tests/automl/test_independent_0940_netloss_co12.py'),
        Path('scripts/predict_independent_0940_netloss_co12.py'),
        REPORT/'DESIGN.md',OUT/'dataset.csv.gz',CO10/'folds.json',
        CO8/'dataset.csv.gz',CO8/'valuation_paths.csv.gz',REPORT11/'daily.csv.gz',REPORT11/'summary.csv',
        Path('scripts/experiment_b1_calibrated_risk_co11.py'),Path('scripts/experiment_b1_market_trade_gate_co8.py'),
        Path('scripts/report_b1_market_trade_gate_co8.py')]


def freeze():
    path=REPORT/'pre_run_freeze.json'
    if path.exists():raise RuntimeError('Already frozen')
    save_json(path,dict(created_utc=datetime.now(timezone.utc).isoformat(),files={str(p):sha(p) for p in input_files()},
        git_base=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        prior_research_seen=True,primary='tail_budget_005',B1_used_in_independent_training=False))


def check_freeze():
    assert sklearn.__version__=='1.7.1',sklearn.__version__
    for p,h in json.loads((REPORT/'pre_run_freeze.json').read_text())['files'].items():assert sha(Path(p))==h,p


def phase_metrics(z,phase,month):
    results=[];bins=[]
    for scope,d in [('market',z),('strong4to9',z[strong(z)])]:
        d=d[d.label_known];tail=d[d.severe_loss]
        for method,col in [('raw','raw_probability'),('calibrated','p_severe')]:
            results.append(dict(month=month,phase=phase,scope=scope,method=method,rows=len(d),events=int(d.severe_loss.sum()),
                probability_sum=float(d[col].sum()),brier_sum=float(((d[col]-d.severe_loss.astype(float))**2).sum()),
                reference_brier_sum=float(((d.probability_reference-d.severe_loss.astype(float))**2).sum()),
                tail_loss_actual_sum=float((-d.net_0940).where(d.severe_loss,0).sum()),
                tail_loss_pred_sum=float(d.tail_expected_loss.sum()),
                severity_actual_sum=float(tail.severity.sum()),severity_pred_sum=float(tail.conditional_severity.sum()),
                reference_severity_abs_error_sum=float((tail.severity_reference-tail.severity).abs().sum()),
                severity_abs_error_sum=float((tail.conditional_severity-tail.severity).abs().sum())))
        for column,edges in [('p_severe',[0,.05,.1,.2,.3,.4,.5,.75,1.000001]),
                             ('tail_expected_loss',[0,.0025,.005,.01,.02,.05,1.000001])]:
            for bin_,g in d.groupby(pd.cut(d[column],edges,right=False),observed=True):
                bins.append(dict(month=month,phase=phase,scope=scope,variable=column,bin=str(bin_),rows=len(g),
                    events=int(g.severe_loss.sum()),p_sum=float(g.p_severe.sum()),tail_pred_sum=float(g.tail_expected_loss.sum()),
                    tail_actual_sum=float((-g.net_0940).where(g.severe_loss,0).sum())))
    return results,bins


def fit():
    check_freeze();start=time.monotonic();d=read(OUT/'dataset.csv.gz')
    folds=[f for f in json.loads((CO10/'folds.json').read_text()) if f['arm']=='nonfall_hist']
    allmetrics=[];allbins=[];receipts=[];maxerr=0.
    for fold in folds:
        month=fold['month'];dest=OUT/'models'/month;dest.mkdir(parents=True,exist_ok=True)
        if (dest/'fold.json').exists():
            print('cached',month,flush=True);continue
        started=time.monotonic();tr=fold['train_dates'];ca=fold['validation_dates'][:20];ck=fold['validation_dates'][20:];te=fold['test_dates']
        train=d[d.signal_date.isin(tr)];cal=d[d.signal_date.isin(ca)].copy();check=d[d.signal_date.isin(ck)].copy();test=d[d.signal_date.isin(te)].copy()
        assert set(tr).isdisjoint(ca) and set(ca).isdisjoint(ck) and set(ck).isdisjoint(te)
        assert max(tr)<min(ca)<max(ca)<min(ck)<max(ck)<min(te)
        assert train[train.label_known].label_available_time.max()<min(ca)+' 14:40:00'
        assert cal[cal.label_known].label_available_time.max()<min(ck)+' 14:40:00'
        assert check[check.label_known].label_available_time.max()<min(te)+' 14:40:00'
        m,used=fit_heads(train);cal['raw_probability'],cal['raw_severity']=raw_predict(m,cal)
        calibration=fit_calibration(cal);m.update(calibration=calibration,month=month)
        joblib.dump(m,dest/'model.joblib',compress=3);save_json(dest/'calibration.json',calibration)
        used[['sample_id','severe_loss','probability_weight','severity_weight']].to_csv(dest/'training_membership.csv.gz',index=False,float_format='%.17g')
        for phase,z in [('calibration',cal),('check',check),('test',test)]:
            if phase!='calibration':z['raw_probability'],z['raw_severity']=raw_predict(m,z)
            for col,values in calibrated_predict(calibration,z).items():z[col]=values
            z['probability_reference']=np.where(strong(z),calibration['strong_probability']['prior'],calibration['global_probability']['prior'])
            z['severity_reference']=np.where(strong(z),calibration['strong_severity']['observed_mean'],calibration['global_severity']['observed_mean'])
            output=['sample_id',*KEY,'raw_probability','raw_severity','p_severe','conditional_severity','tail_expected_loss',
                    'probability_reference','severity_reference']
            z[output].to_csv(dest/(phase+'.csv.gz'),index=False,float_format='%.17g')
            if phase!='calibration':
                ms,bs=phase_metrics(z,phase,month);allmetrics+=ms;allbins+=bs
        back=joblib.load(dest/'model.joblib');saved=read(dest/'test.csv.gz');p=predict_bundle(back,test)
        error=float(np.max(np.abs(p.to_numpy()-saved[p.columns].to_numpy())));assert error<1e-12;maxerr=max(maxerr,error)
        receipt=dict(month=month,train_dates=tr,calibration_dates=ca,check_dates=ck,test_dates=te,
            train_label_max=train[train.label_known].label_available_time.max(),calibration_label_max=cal[cal.label_known].label_available_time.max(),
            check_label_max=check[check.label_known].label_available_time.max(),training_rows=len(used),training_events=int(used.severe_loss.sum()),
            model_sha256=sha(dest/'model.joblib'),train_membership_sha256=sha(dest/'training_membership.csv.gz'),
            reload_max_error=error,seconds=time.monotonic()-started)
        # Save per-fold diagnostics so an interrupted run can resume without changing completed folds.
        pd.DataFrame([r for r in allmetrics if r['month']==month]).to_csv(dest/'metrics.csv',index=False)
        pd.DataFrame([r for r in allbins if r['month']==month]).to_csv(dest/'bins.csv',index=False)
        save_json(dest/'fold.json',receipt);receipts.append(receipt)
        print('fit',month,'train',len(used),'events',used.severe_loss.sum(),'seconds',round(receipt['seconds'],2),flush=True)
    dirs=sorted((OUT/'models').glob('*'))
    pd.concat([read(p/'metrics.csv') for p in dirs],ignore_index=True).to_csv(REPORT/'market_metrics_monthly.csv',index=False)
    pd.concat([read(p/'bins.csv') for p in dirs],ignore_index=True).to_csv(REPORT/'market_risk_bins_monthly.csv',index=False)
    receipts=[json.loads((p/'fold.json').read_text()) for p in dirs]
    save_json(OUT/'folds.json',receipts)
    save_json(REPORT/'run_manifest.json',dict(sklearn=sklearn.__version__,numpy=np.__version__,scipy=scipy.__version__,pandas=pd.__version__,
        joblib=joblib.__version__,python=platform.python_version(),folds=len(receipts),heads=2*len(receipts),features=FEATURES,
        total_seconds=time.monotonic()-start,source_in_working_tree=True,max_reload_error=max(f['reload_max_error'] for f in receipts)))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['freeze','fit']);globals()[p.parse_args().phase]()
