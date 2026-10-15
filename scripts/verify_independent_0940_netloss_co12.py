"""Exercise the independent inference CLI with feature-only input and a late-input rejection."""
import json
import subprocess
import sys
import time
import pandas as pd
from scripts.experiment_independent_0940_netloss_co12 import OUT,REPORT,FEATURES,read,save_json


def run():
    fold=json.loads((OUT/'folds.json').read_text())[0];month=fold['month']
    parts=[]
    for chunk in pd.read_csv(OUT/'dataset.csv.gz',dtype={'symbol6':str},float_precision='round_trip',chunksize=100000):
        selected=chunk[chunk.signal_date.str.startswith(month)]
        if len(selected):
            parts.append(selected.head(100));break
    d=pd.concat(parts,ignore_index=True).head(100)
    dest=OUT/'inference_check';dest.mkdir(exist_ok=True)
    source=dest/'features_only.csv';out=dest/'predictions.csv'
    cols=['signal_date','symbol6','feature_cutoff',*FEATURES]
    d[cols].to_csv(source,index=False,float_format='%.17g')
    command=[sys.executable,'-m','scripts.predict_independent_0940_netloss_co12','--model-dir',str(OUT/'models'/month),
             '--input',str(source),'--output',str(out)]
    start=time.monotonic();subprocess.run(command,check=True,capture_output=True,text=True);elapsed=time.monotonic()-start
    actual=read(out);expected=read(OUT/'models'/month/'test.csv.gz').set_index(['signal_date','symbol6']).loc[pd.MultiIndex.from_frame(d[['signal_date','symbol6']])]
    outputs=['p_severe','conditional_severity','tail_expected_loss']
    err=float(abs(actual[outputs].to_numpy()-expected[outputs].to_numpy()).max());assert err<1e-12
    late=d[cols].copy();late.loc[0,'feature_cutoff']=late.loc[0,'signal_date']+' 14:41:00'
    bad=dest/'late_features_rejection_case.csv';badout=dest/'late_output.csv'
    late.to_csv(bad,index=False,float_format='%.17g')
    rejected=subprocess.run([*command[:-4],'--input',str(bad),'--output',str(badout)],capture_output=True,text=True)
    assert rejected.returncode!=0 and 'cutoff exceeds' in rejected.stderr and not badout.exists()
    result=dict(rows=len(d),maximum_error=err,feature_only_input=True,late_feature_cutoff_rejected=True,
                cli_wall_seconds=elapsed,includes_python_import_time=True,model_month=month)
    save_json(REPORT/'inference_check.json',result);print(json.dumps(result,indent=2))


if __name__=='__main__':run()
