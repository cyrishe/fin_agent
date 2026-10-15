"""Read-only CO12 inference: probability, conditional severe loss and tail contribution."""
import argparse
import json
from pathlib import Path
import joblib
import sklearn
from scripts.experiment_independent_0940_netloss_co12 import predict_bundle,read,sha


def run(folder,source,output):
    if sklearn.__version__!='1.7.1':raise ValueError('CO12 requires scikit-learn 1.7.1')
    metadata=json.loads((folder/'fold.json').read_text())
    if sha(folder/'model.joblib')!=metadata['model_sha256']:raise ValueError('Model hash mismatch')
    model=joblib.load(folder/'model.joblib');d=read(source)
    result=predict_bundle(model,d)
    for col in ('signal_date','symbol6','feature_cutoff'):result[col]=d[col]
    result.to_csv(output,index=False,float_format='%.17g')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model-dir',required=True,type=Path)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();run(a.model_dir,a.input,a.output)
