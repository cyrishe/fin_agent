"""CO13: daily rolling-20 independent loss heads, calibrated on past prequential scores."""
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
from scripts.experiment_independent_0940_netloss_co12 import (
    OUT as CO12, REPORT as REPORT12, CO8, KEY, FEATURES, PARAMS,
    read, sha, save_json, fit_heads, raw_predict, fit_calibration, calibrated_predict, strong,
)

OUT=Path('outputs/stock_automl/daily20_risk_co13')
REPORT=Path('docs/stock_automl_runs/20261015_daily20_risk_co13')
SCORE_COLS=['sample_id',*KEY,'raw_probability','raw_severity']


def previous20(dates, day):
    i=dates.index(day)
    if i<20:raise ValueError('Need 20 prior trading dates')
    return dates[i-20:i]


def matured(d, day):
    return d[d.label_known & d.label_available_time.notna() &
             d.label_available_time.lt(day+' 14:40:00') & d.signal_date.lt(day)].copy()


def calibration_rows(raw, labels, day, dates):
    expected=previous20(dates,day)
    if sorted(raw.signal_date.unique())!=expected:raise ValueError('Calibration requires exactly 20 prior OOS dates')
    if raw.sample_id.duplicated().any():raise ValueError('Duplicate OOS sample')
    z=raw.merge(labels,on=['sample_id',*KEY],validate='one_to_one')
    z=matured(z,day)
    if sorted(z.signal_date.unique())!=expected:raise ValueError('Missing matured calibration date')
    return z


def predict_bundle(m,d):
    if list(m['features'])!=list(FEATURES):raise ValueError('Wrong features')
    if not d.signal_date.eq(m['day']).all():raise ValueError('Wrong prediction day')
    cutoff=pd.to_datetime(d.feature_cutoff)
    if cutoff.isna().any() or not cutoff.le(pd.to_datetime(d.signal_date+' 14:40:00')).all():
        raise ValueError('Feature timestamp exceeds decision time')
    z=d.copy();z['raw_probability'],z['raw_severity']=raw_predict(m,z)
    return calibrated_predict(m['calibration'],z)


def freeze():
    REPORT.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    p=REPORT/'pre_run_freeze.json'
    if p.exists():raise RuntimeError('Already frozen')
    files=[Path(__file__).relative_to(Path.cwd()) if Path(__file__).is_absolute() else Path(__file__),
        Path('scripts/report_daily20_risk_co13.py'),Path('tests/automl/test_daily20_risk_co13.py'),REPORT/'DESIGN.md',
        CO12/'dataset.csv.gz',CO8/'dataset.csv.gz',CO8/'valuation_paths.csv.gz',
        REPORT12/'daily.csv.gz',REPORT12/'monthly.csv',REPORT12/'summary.csv',REPORT12/'top1_classification.json']
    files+=list(Path('scripts').glob('*independent_0940_netloss_co12.py'))
    files+=[Path('scripts/experiment_b1_calibrated_risk_co11.py'),Path('scripts/experiment_b1_market_trade_gate_co8.py'),
            Path('scripts/report_b1_market_trade_gate_co8.py')]
    save_json(p,dict(created_utc=datetime.now(timezone.utc).isoformat(),files={str(f):sha(f) for f in files},
        git_base=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        train_days=20,calibration_OOS_days=20,refresh='daily',parameters=PARAMS,
        primary='tail_budget_005',prior_research_seen=True,thresholds_unchanged=True))


def check_freeze():
    assert sklearn.__version__=='1.7.1',sklearn.__version__
    for p,h in json.loads((REPORT/'pre_run_freeze.json').read_text())['files'].items():assert sha(Path(p))==h,p


def fit():
    check_freeze();started=time.monotonic()
    d=read(CO12/'dataset.csv.gz');dates=sorted(d.signal_date.unique());groups={day:g for day,g in d.groupby('signal_date')}
    old=read(REPORT12/'daily.csv.gz');testdates=sorted(old.signal_date.unique())
    start=dates.index(testdates[0])-20
    assert start>=20 and testdates==[day for day in dates if testdates[0]<=day<=testdates[-1]]
    run_dates=dates[start:dates.index(testdates[-1])+1]
    labels=d[['sample_id',*KEY,'signal_return','label_known','label_available_time','severe_loss','severity','net_0940']]
    raw_cache={};receipts=[]
    for i,day in enumerate(run_dates):
        dest=OUT/'models'/day;dest.mkdir(parents=True,exist_ok=True)
        if (dest/'fold.json').exists():
            rec=json.loads((dest/'fold.json').read_text())
            for name,h in rec['hashes'].items():assert sha(dest/name)==h,(day,name)
            raw_cache[day]=read(dest/'raw_oos.csv.gz');receipts.append(rec)
            if len(raw_cache)>20:del raw_cache[min(raw_cache)]
            continue
        t=time.monotonic();tr=previous20(dates,day)
        train=matured(pd.concat([groups[x] for x in tr]),day)
        assert sorted(train.signal_date.unique())==tr
        m,used=fit_heads(train);m.update(day=day)
        test=groups[day];z=test[SCORE_COLS[:3]].copy()
        z['raw_probability'],z['raw_severity']=raw_predict(m,test)
        z.to_csv(dest/'raw_oos.csv.gz',index=False,float_format='%.17g')
        used[['sample_id','severe_loss','probability_weight','severity_weight']].to_csv(dest/'training_membership.csv.gz',index=False,float_format='%.17g')
        rec=dict(day=day,train_dates=tr,train_label_max=used.label_available_time.max(),training_rows=len(used),
                 training_events=int(used.severe_loss.sum()),test_rows=len(test),formal_test=day in testdates)
        names=['raw_oos.csv.gz','training_membership.csv.gz','model.joblib']
        if day in testdates:
            ca=previous20(dates,day)
            raw=pd.concat([raw_cache[x] for x in ca],ignore_index=True)
            cal=calibration_rows(raw,labels[labels.signal_date.isin(ca)],day,dates)
            calibration=fit_calibration(cal);m['calibration']=calibration
            save_json(dest/'calibration.json',calibration)
            cal[['sample_id','raw_probability','raw_severity']].to_csv(dest/'calibration_membership.csv.gz',index=False,float_format='%.17g')
            for col,values in calibrated_predict(calibration,z.merge(test[['sample_id','signal_return']],on='sample_id',validate='one_to_one')).items():z[col]=values.to_numpy()
            z['probability_reference']=np.where(strong(test),calibration['strong_probability']['prior'],calibration['global_probability']['prior'])
            z['severity_reference']=np.where(strong(test),calibration['strong_severity']['observed_mean'],calibration['global_severity']['observed_mean'])
            z.to_csv(dest/'test.csv.gz',index=False,float_format='%.17g')
            rec.update(calibration_dates=ca,calibration_rows=len(cal),calibration_label_max=cal.label_available_time.max(),
                       calibration_source_hashes={x:sha(OUT/'models'/x/'raw_oos.csv.gz') for x in ca})
            names+=['calibration.json','calibration_membership.csv.gz','test.csv.gz']
        joblib.dump(m,dest/'model.joblib',compress=3)
        back=joblib.load(dest/'model.joblib');bp,bs=raw_predict(back,test)
        error=max(float(np.max(abs(bp-z.raw_probability))),float(np.max(abs(bs-z.raw_severity))))
        if day in testdates:
            pred=predict_bundle(back,test)
            error=max(error,float(np.max(abs(pred.to_numpy()-z[pred.columns].to_numpy()))))
        assert error<1e-12
        rec.update(reload_max_error=error,hashes={name:sha(dest/name) for name in names},seconds=time.monotonic()-t)
        save_json(dest/'fold.json',rec);receipts.append(rec)
        raw_cache[day]=z[SCORE_COLS]
        if len(raw_cache)>20:del raw_cache[min(raw_cache)]
        print('fit',i+1,'/',len(run_dates),day,'rows',len(used),'seconds',round(rec['seconds'],2),flush=True)
    save_json(OUT/'folds.json',receipts)
    save_json(REPORT/'run_manifest.json',dict(sklearn=sklearn.__version__,numpy=np.__version__,scipy=scipy.__version__,pandas=pd.__version__,
        joblib=joblib.__version__,python=platform.python_version(),features=FEATURES,folds=len(receipts),heads=2*len(receipts),
        formal_days=len(testdates),warmup_days=20,total_seconds=time.monotonic()-started,
        max_reload_error=max(r['reload_max_error'] for r in receipts)))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['freeze','fit']);globals()[p.parse_args().phase]()
