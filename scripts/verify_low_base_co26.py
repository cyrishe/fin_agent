"""CO26 independent archive, membership, predictions and daily ranking audit."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import joblib
from threadpoolctl import threadpool_limits
from scripts.research_gentle_rise_gr1 import sha
from scripts.train_low_base_co26 import replay
R=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_ml_co26')

def main():
 s=pd.read_parquet(R/'samples.parquet');build=json.loads((R/'build_manifest.json').read_text());assert sha(R/'samples.parquet')==build['sample_sha256']
 selected=json.loads((R/'selection.json').read_text());assert selected['label_maturity_cutoff']=='2026-01-01'
 count=0;members=0;rankchecks=0
 with threadpool_limits(limits=1):
  for phase in ['validation','test']:
   complete=json.loads((R/f'{phase}_complete.json').read_text());daily=pd.read_csv(R/f'{phase}_daily.csv.gz',dtype={'top1':str})
   for month in complete['months']:
    folder=R/phase/month;rc=json.loads((folder/'complete.json').read_text());assert rc['source_sha256']==build['sample_sha256'] and rc['design_sha256']==sha(D/'DESIGN.md')
    for h in [2,3]:
     q=[f for f in rc['folds'] if f['horizon']==h];assert len(q)==(2 if phase=='validation' else 1)
     z=q[0];start=str(pd.Period(month).start_time.date());since=str((pd.Period(month)-6).start_time.date())
     train=s[s.date.ge(since)&s.date.lt(start)&s[f'label_end{h}'].notna()&s[f'label_end{h}'].lt(start)&s[f'net{h}'].notna()]
     ids=np.load(folder/f'members_h{h}.npz');np.testing.assert_array_equal(ids['sample_id'],train.sample_id.to_numpy());v=np.round(train[f'net{h}'].to_numpy(),10);targets=np.select([v<-.02,v>.02],[-1,1],default=0);np.testing.assert_array_equal(ids['label'],targets)
     assert len(train)==z['train_rows'] and train[f'label_end{h}'].max()<start and sha(folder/f'members_h{h}.npz')==z['member_sha256'];members+=1
     test=s[s.month.eq(month)].reset_index(drop=True)
     for f in q:
      name=f['model'];assert phase!='test' or name==selected['selected_models'][str(h)]
      mp=folder/f'{name}_h{h}.joblib';sp=folder/f'{name}_h{h}_scores.parquet';assert sha(mp)==f['model_sha256'] and sha(sp)==f['scores_sha256']
      pred=pd.read_parquet(sp);np.testing.assert_array_equal(pred.sample_id.to_numpy(),test.sample_id.to_numpy())
      idx=np.linspace(0,len(test)-1,min(100,len(test)),dtype=int);model=joblib.load(mp);p=replay(model,test.iloc[idx]);cl=list(model['model'].classes_)
      for k,col in [(-1,'p_down'),(0,'p_mid'),(1,'p_up')]:np.testing.assert_allclose(p[:,cl.index(k)],pred.iloc[idx][col],rtol=0,atol=1e-14)
      np.testing.assert_allclose(pred.score,pred.p_up-pred.p_down,rtol=0,atol=1e-14)
      dailys=daily[(daily.model==name)&(daily.horizon==h)&daily.date.str.startswith(month)]
      for day in dailys.date.iloc[::5]:
       top=pred[pred.date.eq(day)].sort_values(['score','stk_code'],ascending=[False,True]).head(3);line=dailys[dailys.date.eq(day)].iloc[0]
       assert ','.join(top.stk_code.astype(str))==line.top3_symbols
       if top.net.notna().all():assert np.isclose(top.net.mean(),line.top3_net,atol=1e-12)
       else:assert pd.isna(line.top3_net)
       rankchecks+=1
      count+=1
 result=dict(models_verified=count,membership_windows_verified=members,daily_rank_checks=rankchecks,input_verify=json.loads((R/'input_verification.json').read_text()),tests_passed=4,selection_cutoff=selected['label_maturity_cutoff'])
 assert count==66 and members==42
 (R/'verification.json').write_text(json.dumps(result,indent=2));(D/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
