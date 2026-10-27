"""Reconstruct full training windows and test populations from CO26 data."""
from pathlib import Path
import json
import numpy as np,pandas as pd
from scripts.train_low_base_co26 import splits,labels
R=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def main():
 s=pd.read_parquet(R/'samples.parquet');rows=[]
 for phase in ['validation','test']:
  period=json.loads((R/f'{phase}_complete.json').read_text())['months']
  for month in period:
   for h in [2,3]:
    a,b=splits(s,month,h); y=labels(a[f'net{h}']); known=b[f'net{h}'].dropna();k=labels(known)
    n=len(y);nk=len(k);counts=a.groupby('date').size();weights=1/a.groupby('date').sample_id.transform('count').to_numpy(float);weights/=weights.sum()
    rows.append(dict(phase=phase,horizon=h,prediction_month=month,training_first=a.date.min(),training_last=a.date.max(),last_training_label_matures=a[f'label_end{h}'].max(),training_days=a.date.nunique(),training_stock_days=n,training_stocks=a.stk_code.nunique(),training_daily_min=int(counts.min()),training_daily_median=float(counts.median()),training_daily_max=int(counts.max()),train_down=int((y==-1).sum()),train_mid=int((y==0).sum()),train_up=int((y==1).sum()),train_down_pct=100*(y==-1).mean(),train_mid_pct=100*(y==0).mean(),train_up_pct=100*(y==1).mean(),train_down_day_weight_pct=100*weights[y==-1].sum(),train_mid_day_weight_pct=100*weights[y==0].sum(),train_up_day_weight_pct=100*weights[y==1].sum(),prediction_first=b.date.min(),prediction_last=b.date.max(),prediction_days=b.date.nunique(),prediction_candidates=len(b),prediction_stocks=b.stk_code.nunique(),prediction_label_known=nk,prediction_label_unknown=int(b[f'net{h}'].isna().sum()),prediction_down_pct=100*(k==-1).mean(),prediction_mid_pct=100*(k==0).mean(),prediction_up_pct=100*(k==1).mean()))
 out=pd.DataFrame(rows);out.to_csv(D/'rolling_windows.csv',index=False)
 summary=out.groupby(['phase','horizon']).agg(months=('prediction_month','count'),train_days_min=('training_days','min'),train_days_median=('training_days','median'),train_days_max=('training_days','max'),train_min=('training_stock_days','min'),train_median=('training_stock_days','median'),train_max=('training_stock_days','max'),train_down_min=('train_down_pct','min'),train_down_max=('train_down_pct','max'),train_up_min=('train_up_pct','min'),train_up_max=('train_up_pct','max'),test_count_min=('prediction_candidates','min'),test_count_median=('prediction_candidates','median'),test_count_max=('prediction_candidates','max'),test_dates=('prediction_days','sum')).reset_index();summary.to_csv(D/'rolling_window_summary.csv',index=False)
 print(summary.to_string(index=False));print(out[(out.prediction_month.isin(['2025-01','2025-12','2026-01','2026-09']))].to_string(index=False))
if __name__=='__main__':main()
