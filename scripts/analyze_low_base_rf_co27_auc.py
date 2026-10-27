"""Out-of-time per-day forest AUC on the actual known-outcome candidate pool."""
from pathlib import Path
import json
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def main():
 months=json.loads((R/'complete.json').read_text())['months'];rows=[]
 for month in months:
  phase='validation' if month<'2026-01' else 'test'
  for h in [2,3]:
   f=pd.read_parquet(R/phase/month/f'rf_h{h}_scores.parquet');f=f[f.net.notna()]
   if phase=='validation':f=f[f.label_end.lt('2026-01-01')]
   for day,g in f.groupby('date',sort=True):
    y=g.net.round(10);up=y.gt(.02);down=y.lt(-.02)
    rows.append(dict(phase=phase,month=month,date=day,horizon=h,known=len(g),up_n=int(up.sum()),down_n=int(down.sum()),up_auc=float(roc_auc_score(up,g.p_up)) if up.nunique()==2 else np.nan,down_auc=float(roc_auc_score(down,g.p_down)) if down.nunique()==2 else np.nan))
 df=pd.DataFrame(rows);df.to_csv(R/'auc_daily.csv.gz',index=False)
 monthly=df.groupby(['phase','month','horizon']).agg(days=('date','count'),up_auc_days=('up_auc','count'),down_auc_days=('down_auc','count'),up_auc=('up_auc','mean'),down_auc=('down_auc','mean')).reset_index();monthly.to_csv(D/'daily_auc_monthly.csv',index=False)
 total=df.groupby(['phase','horizon']).agg(days=('date','count'),up_auc_days=('up_auc','count'),down_auc_days=('down_auc','count'),up_auc=('up_auc','mean'),down_auc=('down_auc','mean')).reset_index();total.to_csv(D/'daily_auc_summary.csv',index=False);print(total.to_string(index=False))
if __name__=='__main__':main()
