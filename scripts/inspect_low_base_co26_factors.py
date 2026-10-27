"""Descriptive standardized multinomial positive-vs-negative coefficients."""
from pathlib import Path
import json,joblib
import pandas as pd
R=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_ml_co26');rows=[]
for phase in ['validation','test']:
 months=json.loads((R/f'{phase}_complete.json').read_text())['months']
 for month in months:
  for h in [2,3]:
   p=R/phase/month/f'logistic_h{h}.joblib'
   if not p.exists():continue
   b=joblib.load(p);m=b['model'];cl=list(m.classes_);diff=m.coef_[cl.index(1)]-m.coef_[cl.index(-1)]
   rows.extend(dict(phase=phase,month=month,horizon=h,factor=f,coefficient=float(v)) for f,v in zip(b['features'],diff))
df=pd.DataFrame(rows);df.to_csv(D/'factor_coefficients.csv',index=False)
s=df.groupby(['phase','horizon','factor']).coefficient.agg(months='count',mean='mean',median='median',positive_share=lambda v:(v>0).mean()).reset_index();s['abs_median']=s['median'].abs();s.sort_values(['phase','horizon','abs_median'],ascending=[True,True,False]).to_csv(D/'factor_summary.csv',index=False)
print(s[s.phase.eq('test')].sort_values(['horizon','abs_median'],ascending=[True,False]).groupby('horizon').head(6).to_string(index=False))
