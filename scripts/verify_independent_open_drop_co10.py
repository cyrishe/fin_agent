"""Verify the independent inference entrypoint against frozen out-of-time scores."""
import numpy as np
import pandas as pd
from scripts.experiment_independent_open_drop_co10 import OUT,REPORT,FEATURES,read,save_json
from scripts.predict_independent_open_drop_co10 import predict


def run():
    sample=None
    for chunk in pd.read_csv(OUT/'dataset.csv.gz',dtype={'symbol6':str},float_precision='round_trip',chunksize=50000):
        x=chunk[chunk.signal_date.eq('2025-08-01')]
        if len(x)>=100:sample=x.head(100).copy();break
    assert sample is not None
    folder=OUT/'inference_check';folder.mkdir(exist_ok=True)
    f=sample[['signal_date','symbol6',*FEATURES]].copy();f['feature_cutoff']=f.signal_date+' 14:40:00'
    f.to_csv(folder/'features.csv',index=False,float_format='%.17g')
    model_dir=OUT/'models'/'2025-08'/'nonfall_hist'
    result=predict(model_dir,folder/'features.csv',folder/'decisions.csv')
    expected=read(model_dir/'test.csv.gz')
    joined=result.merge(expected,on=['signal_date','symbol6'],validate='one_to_one',suffixes=('','_saved'))
    assert len(joined)==len(result)
    error=float(np.max(np.abs(joined.risk_score-joined.risk_score_saved)));assert error<1e-12
    f['feature_cutoff']=f.signal_date+' 14:41:00';f.to_csv(folder/'invalid_future_features.csv',index=False)
    rejected=False
    try:predict(model_dir,folder/'invalid_future_features.csv',folder/'should_not_exist.csv')
    except ValueError as e:rejected='cutoff' in str(e)
    assert rejected and not (folder/'should_not_exist.csv').exists()
    save_json(REPORT/'inference_check.json',dict(rows=len(result),max_score_difference=error,
        post_1440_declared_cutoff_rejected=rejected,no_B1_input=True))
    print('Independent inference verified:',len(result),'rows, error',error,flush=True)


if __name__=='__main__':run()
