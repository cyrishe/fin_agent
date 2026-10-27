"""Compare saved daily feature and outcome correlations to independent scipy calculations."""
from pathlib import Path
import json
import numpy as np,pandas as pd
from scipy.stats import spearmanr
P=Path('outputs/stock_automl/low_base_ml_co26_20261027');R=Path('outputs/stock_automl/low_base_rf_co27_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def main():
 s=pd.read_parquet(P/'samples.parquet',columns=['date','net2','net3','return5','excess5','return1','body','position60','position120','range5','volatility20'])
 pairs=pd.read_csv(R/'factor_pair_daily.csv.gz');ic=pd.read_csv(R/'factor_ic_daily.csv.gz');dates=[]
 for year in ['2024','2025','2026']:
  v=sorted(s.loc[s.date.str.startswith(year),'date'].unique());dates += [v[len(v)//3],v[2*len(v)//3]]
 checks=0
 for day in dates:
  g=s[s.date.eq(day)]
  for f1,f2 in [('return5','excess5'),('return1','body'),('position60','position120')]:
   z=g[[f1,f2]].dropna();a=spearmanr(z[f1],z[f2]).statistic if len(z)>=30 else np.nan;b=pairs[(pairs.date==day)&(pairs.factor1==f1)&(pairs.factor2==f2)].spearman.iloc[0]
   assert np.isclose(a,b,atol=1e-12,rtol=1e-12,equal_nan=True),(day,f1,f2,a,b);checks+=1
  for h in [2,3]:
   for f in ['return5','position120','range5','volatility20']:
    z=g[[f,f'net{h}']].dropna();a=spearmanr(z[f],z[f'net{h}'].round(10)).statistic if len(z)>=30 else np.nan;b=ic[(ic.date==day)&(ic.horizon==h)&(ic.factor==f)].net_spearman.iloc[0]
    assert np.isclose(a,b,atol=1e-12,rtol=1e-12,equal_nan=True),(day,h,f,a,b);checks+=1
 result=dict(days=dates,independent_scipy_checks=checks,source='complete same-day available candidate set',pairwise_missing_120_day_verified=True)
 (D/'correlation_verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
