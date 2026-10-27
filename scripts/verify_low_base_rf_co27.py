"""Independent RF model, membership, score and chronology checks."""
from pathlib import Path
import argparse,json
import joblib,numpy as np,pandas as pd
from threadpoolctl import threadpool_limits
from scripts.train_low_base_co26 import splits,labels,replay
from scripts.research_gentle_rise_gr1 import sha
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');P=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--partial',action='store_true');args=ap.parse_args();s=pd.read_parquet(P/'samples.parquet');source=sha(P/'samples.parquet');folds=sorted(R.glob('*/*/complete.json'));checks=0
 with threadpool_limits(limits=1):
  for path in folds:
   receipt=json.loads(path.read_text());assert receipt['source_sha256']==source and receipt['design_sha256']==sha(D/'DESIGN.md');month=receipt['month'];phase=receipt['phase'];out=path.parent
   for info in receipt['folds']:
    h=info['horizon'];tr,te=splits(s,month,h);members=P/phase/month/f'members_h{h}.npz';ids=np.load(members);np.testing.assert_array_equal(ids['sample_id'],tr.sample_id);np.testing.assert_array_equal(ids['label'],labels(tr[f'net{h}']));assert sha(members)==info['member_sha256'];assert tr[f'label_end{h}'].max()<te.date.min()
    mp=out/f'rf_h{h}.joblib';sp=out/f'rf_h{h}_scores.parquet';assert sha(mp)==info['model_sha256'] and sha(sp)==info['scores_sha256'];bundle=joblib.load(mp);params=bundle['model'].get_params();assert params['n_estimators']==80 and params['max_samples']==.1 and params['max_depth']==7 and params['min_samples_leaf']==200 and params['random_state']==42
    pred=pd.read_parquet(sp);te=te.reset_index(drop=True);np.testing.assert_array_equal(pred.sample_id,te.sample_id);idx=np.linspace(0,len(te)-1,min(100,len(te)),dtype=int);probs=replay(bundle,te.iloc[idx]);cl=list(bundle['model'].classes_)
    for c,k in [(-1,'p_down'),(0,'p_mid'),(1,'p_up')]:np.testing.assert_allclose(probs[:,cl.index(c)],pred.iloc[idx][k],rtol=0,atol=1e-14)
    np.testing.assert_allclose(pred.score,pred.p_up-pred.p_down,rtol=0,atol=1e-14);checks+=1
 result={'months':len(folds),'models':checks,'members_match_co26':True,'temporal_maturity_verified':True,'sampled_prediction_readback':True,'bootstrap_fraction':.1}
 if not args.partial:assert len(folds)==21 and checks==42
 dest=R/'partial_verification.json' if args.partial else D/'verification.json';dest.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
