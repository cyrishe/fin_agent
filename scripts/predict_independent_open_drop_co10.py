"""Score a prepared 14:40 market feature panel without any B1 dependency."""
import argparse
import json
from pathlib import Path
import joblib
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits
from scripts.experiment_independent_open_drop_co10 import FEATURES,score,sha


def predict(model_dir:Path,input_file:Path,output_file:Path,mode='main'):
    if sklearn.__version__!='1.7.1':raise ValueError('Use the pinned scikit-learn 1.7.1 runtime')
    fold=json.loads((model_dir/'fold.json').read_text())
    if sha(model_dir/'model.joblib')!=fold['model_sha256']:raise ValueError('Model hash mismatch')
    d=pd.read_csv(input_file,dtype={'symbol6':str},float_precision='round_trip')
    required=['signal_date','symbol6','feature_cutoff',*FEATURES]
    absent=set(required)-set(d)
    if absent:raise ValueError('Missing columns: '+','.join(sorted(absent)))
    if d.duplicated(['signal_date','symbol6']).any():raise ValueError('Duplicate stock-day')
    cutoff=pd.to_datetime(d.feature_cutoff,errors='raise')
    latest=pd.to_datetime(d.signal_date+' 14:40:00')
    if not (cutoff.le(latest)&cutoff.dt.strftime('%Y-%m-%d').eq(d.signal_date)).all():
        raise ValueError('Features must declare a same-day cutoff no later than 14:40')
    if not d.signal_date.str.startswith(fold['month']).all():
        raise ValueError('Use the frozen model for this prediction month')
    if d.signal_date.min()<min(fold['test_dates']):raise ValueError('Model validation labels not yet mature')
    model=joblib.load(model_dir/'model.joblib');threshold=fold['threshold_'+mode]
    result=d[['signal_date','symbol6','feature_cutoff']].copy()
    result['risk_score']=score(model,d);result['threshold']=threshold
    result['avoid_for_open_risk']=result.risk_score.ge(threshold)
    result['model_arm']=fold['arm'];result['model_month']=fold['month']
    if output_file.exists():raise ValueError('Refusing to overwrite a saved decision file')
    result.to_csv(output_file,index=False,float_format='%.17g')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--mode',choices=['main','aggressive'],default='main');a=p.parse_args()
    with threadpool_limits(limits=1):r=predict(a.model_dir,a.input,a.output,a.mode)
    print(f'Scored {len(r)} stock-days; risk flags: {int(r.avoid_for_open_risk.sum())}. Scores are not calibrated probabilities.')
