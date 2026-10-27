"""Independently audit point-in-time ranks, states and prediction artifact readback."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import joblib
from scipy.stats import rankdata
from scripts.train_low_base_co26 import FEATURES,replay
from scripts.research_gentle_rise_gr1 import sha
from scripts.research_low_base_adaptive_co28 import OUT,DOC,VARIANTS

s=pd.read_parquet(OUT/'samples.parquet');m=pd.read_csv('docs/stock_automl_runs/20261027_low_base_co25/daily_counts.csv').sort_values('date').reset_index(drop=True)
checks=0
for date in ['2024-05-08','2025-08-14','2026-03-11','2026-09-23']:
 g=s[s.date.eq(date)];assert len(g)>30
 for f in FEATURES:
  vals=g[f].to_numpy(float);valid=np.isfinite(vals);expected=np.full(len(vals),np.nan);expected[valid]=rankdata(vals[valid],method='average')/valid.sum();np.testing.assert_allclose(g['rank_'+f],expected,rtol=0,atol=1e-7,equal_nan=True);checks+=1
 i=m.index[m.date.eq(date)][0];z=g.iloc[0];np.testing.assert_allclose(z.market_ret5,np.prod(1+m.market_return.iloc[i-4:i+1])-1,rtol=0,atol=1e-12);np.testing.assert_allclose(z.market_ret20,np.prod(1+m.market_return.iloc[i-19:i+1])-1,rtol=0,atol=1e-12);np.testing.assert_allclose(z.market_vol20,m.market_return.iloc[i-19:i+1].std(),rtol=0,atol=1e-12);checks+=3
 for h in [2,3]:
  a=g[f'net{h}'].to_numpy(float);v=np.isfinite(a);q=rankdata(a[v],method='average')/v.sum();expected=np.full(len(a),np.nan);expected[v]=np.where(q<=.3,-1,np.where(q>.7,1,0));np.testing.assert_equal(g[f'relative{h}'].to_numpy(),expected);checks+=1
n=0
indexed=s.set_index('sample_id')
for month in json.loads((OUT/'complete.json').read_text())['months']:
 phase='validation' if month<'2026-01' else 'test';p=OUT/phase/month;receipt=json.loads((p/'complete.json').read_text());assert receipt['sample_sha256']==sha(OUT/'samples.parquet') and receipt['design_sha256']==sha(DOC/'DESIGN.md')
 for h0 in [2,3]:
  cutoff=str((pd.Period(month)-6).start_time.date())
  mask=s.date.ge(cutoff)&s.date.lt(month+'-01')&s[f'label_end{h0}'].notna()&s[f'label_end{h0}'].lt(month+'-01')&s[f'net{h0}'].notna()
  old=np.load(Path('outputs/stock_automl/low_base_ml_co26_20261027')/phase/month/f'members_h{h0}.npz')
  np.testing.assert_array_equal(s.loc[mask,'sample_id'].to_numpy(),old['sample_id'])
 for a in receipt['entries']:
  h=a['horizon'];name=a['model'];assert a['label_end']<month+'-01';mp=p/f'{name}_h{h}.joblib';sp=p/f'{name}_h{h}_scores.parquet';assert sha(mp)==a['model_sha256'] and sha(sp)==a['scores_sha256'];pred=pd.read_parquet(sp).sample(n=50,random_state=42);z=indexed.loc[pred.sample_id];q=replay(joblib.load(mp),z);np.testing.assert_allclose(q[:,2]-q[:,0],pred.score,rtol=0,atol=1e-13);n+=1
result={'dates_checked':4,'independent_feature_and_label_checks':checks,'models_readback':n,'sample_sha256':sha(OUT/'samples.parquet'),'design_sha256':sha(DOC/'DESIGN.md')};assert n==21*2*len(VARIANTS);(DOC/'verification.json').write_text(json.dumps(result,indent=2));print(result)
