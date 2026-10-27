"""Same-day Top3 vs known-outcome pool predictions and realized class rates."""
from pathlib import Path
import json
import numpy as np,pandas as pd
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def main():
 months=json.loads((R/'complete.json').read_text())['months'];rows=[]
 for month in months:
  if month<'2026-01':continue
  for h in [2,3]:
   d=pd.read_parquet(R/'test'/month/f'rf_h{h}_scores.parquet')
   for day,g in d.groupby('date',sort=True):
    top=g.sort_values(['score','stk_code'],ascending=[False,True]).head(3)
    if top.net.isna().any():continue
    pool=g[g.net.notna()]; row={'date':day,'month':month,'horizon':h,'top_n':len(top),'pool_n':len(pool)}
    for name,z in [('top',top),('pool',pool)]:
     n=z.net.round(10);row.update({f'{name}_p_up':z.p_up.mean(),f'{name}_p_down':z.p_down.mean(),f'{name}_actual_up':n.gt(.02).mean(),f'{name}_actual_down':n.lt(-.02).mean(),f'{name}_net':n.mean()})
    rows.append(row)
 df=pd.DataFrame(rows);df.to_csv(R/'score_quality_daily.csv.gz',index=False);summary=df.groupby('horizon').mean(numeric_only=True).reset_index();summary['days']=df.groupby('horizon').size().to_numpy();summary.to_csv(D/'score_quality_summary.csv',index=False);print(summary.to_string(index=False))
if __name__=='__main__':main()
