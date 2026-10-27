"""Summarize population, label distribution, fold sizes and month-paired uncertainty."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
R=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_ml_co26')

def main():
 s=pd.read_parquet(R/'samples.parquet');s['year']=s.date.str[:4];rows=[]
 for year,g in s.groupby('year'):
  counts=g.groupby('date').size()
  for h in [2,3]:
   v=g[f'net{h}'].dropna().round(10);n=len(v)
   rows.append(dict(year=year,horizon=h,stock_days=len(g),days=len(counts),stocks=g.stk_code.nunique(),daily_mean=counts.mean(),daily_min=int(counts.min()),daily_median=counts.median(),daily_max=int(counts.max()),known=n,unknown=int(g[f'net{h}'].isna().sum()),up2=int(v.gt(.02).sum()),middle=int(v.between(-.02,.02).sum()),down2=int(v.lt(-.02).sum()),up2_rate=v.gt(.02).mean(),middle_rate=v.between(-.02,.02).mean(),down2_rate=v.lt(-.02).mean(),mean_net=v.mean()))
 pd.DataFrame(rows).to_csv(D/'population_labels.csv',index=False)
 folds=[]
 for phase in ['validation','test']:
  c=json.loads((R/f'{phase}_complete.json').read_text())
  for month in c['months']:
   rc=json.loads((R/phase/month/'complete.json').read_text())
   for z in rc['folds']:folds.append(dict(phase=phase,month=month,**z))
 pd.DataFrame(folds).to_csv(D/'fold_samples.csv',index=False)
 daily=pd.read_csv(R/'test_daily.csv.gz');selected=json.loads((R/'selection.json').read_text())['selected_models'];rng=np.random.default_rng(20261027);interval=[]
 for h in [2,3]:
  z=daily[(daily.horizon==h)&(daily.model==selected[str(h)])].copy();z=z[z.top3_net.notna()&z.pool_net.notna()];z['delta']=z.top3_net-z.pool_net
  months=z.groupby('month').delta.agg(['sum','count']);idx=rng.integers(0,len(months),size=(10000,len(months)));vals=months['sum'].to_numpy()[idx].sum(axis=1)/months['count'].to_numpy()[idx].sum(axis=1)
  interval.append(dict(horizon=h,months=len(months),days=len(z),observed_delta=float(z.delta.mean()),bootstrap_low=float(np.quantile(vals,.025)),bootstrap_high=float(np.quantile(vals,.975)),positive_months=int((months['sum']>0).sum())))
 pd.DataFrame(interval).to_csv(D/'test_month_bootstrap.csv',index=False)
 print('POPULATION\n',pd.DataFrame(rows).to_string(index=False));print('MONTH BLOCKS\n',pd.DataFrame(interval).to_string(index=False));print('FOLDS\n',pd.DataFrame(folds).groupby(['phase','horizon']).train_rows.agg(['min','median','max']).to_string())
if __name__=='__main__':main()
