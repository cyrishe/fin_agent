"""Independent monthly checkpoints: rebuild calibration from historical OOS, feature-only scoring."""
import json
import time
import joblib
import numpy as np
import pandas as pd
from scripts.experiment_daily20_risk_co13 import (
    OUT,REPORT,CO12,REPORT12,KEY,FEATURES,read,save_json,check_freeze,
    calibration_rows,fit_calibration,predict_bundle,
)


def numeric_error(a,b):
    if isinstance(a,dict):
        assert a.keys()==b.keys()
        return max([numeric_error(a[k],b[k]) for k in a]+[0.])
    if isinstance(a,(float,int)) and a is not None:return abs(float(a)-float(b))
    assert a==b
    return 0.


def run():
    check_freeze();d=read(CO12/'dataset.csv.gz');dates=sorted(d.signal_date.unique())
    labels=d[['sample_id',*KEY,'signal_return','label_known','label_available_time','severe_loss','severity','net_0940']]
    folds=[f for f in json.loads((OUT/'folds.json').read_text()) if f['formal_test']]
    checkpoints={f['day'][:7]:f for f in folds};results=[]
    for month,f in checkpoints.items():
        day=f['day'];root=OUT/'models'/day
        raw=pd.concat([read(OUT/'models'/s/'raw_oos.csv.gz') for s in f['calibration_dates']],ignore_index=True)
        cal=calibration_rows(raw,labels,day,dates);rebuilt=fit_calibration(cal)
        err=numeric_error(rebuilt,json.loads((root/'calibration.json').read_text()));assert err<1e-10
        source=d[d.signal_date.eq(day)]
        x=source[['signal_date','symbol6','feature_cutoff',*FEATURES]].copy()
        m=joblib.load(root/'model.joblib');start=time.monotonic();p=predict_bundle(m,x);elapsed=time.monotonic()-start
        expected=read(root/'test.csv.gz');pred_err=float(abs(p.to_numpy()-expected[p.columns].to_numpy()).max());assert pred_err<1e-12
        late=x.head(1).copy();late['feature_cutoff']=day+' 14:41:00'
        try:predict_bundle(m,late)
        except ValueError:pass
        else:raise AssertionError('Late input accepted')
        results.append(dict(day=day,calibration_rebuild_error=err,feature_only_prediction_error=pred_err,
                            test_rows=len(x),prediction_seconds=elapsed,late_feature_rejected=True))
    # Exact pairing of all policies/months with old persisted metrics.
    current=read(REPORT/'monthly.csv');old=read(REPORT12/'monthly.csv')
    cols=['net_nav_pct','settled_max_drawdown_pct','trades','profits_missed','partial_winning_trades','losses_avoided']
    a=current[current.model.str.startswith('60d_')].copy();a['model']=a.model.str[4:]
    z=a.merge(old,on=['model','month'],suffixes=('_new','_old'),validate='one_to_one')
    for col in cols:np.testing.assert_allclose(z[col+'_new'],z[col+'_old'],atol=1e-9,rtol=0)
    save_json(REPORT/'verification.json',dict(monthly_checkpoints=results,old_monthly_metrics_exact=True,
        all_scores_reloaded_during_training=True,max_calibration_error=max(r['calibration_rebuild_error'] for r in results),
        max_feature_only_error=max(r['feature_only_prediction_error'] for r in results)))
    print('verified',len(results),'monthly checkpoints and',len(z),'old scheme-month rows')


if __name__=='__main__':run()
