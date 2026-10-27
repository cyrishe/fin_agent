"""Matched date RF, CO26 models and baseline with unknown results retained."""
from pathlib import Path
import json
import numpy as np,pandas as pd
from scripts.evaluate_low_base_co26 import daily,selection_eligible
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');PREV=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27')

def main():
 months=json.loads((R/'complete.json').read_text())['months'];parts=[]
 for month in months:
  phase='validation' if month<'2026-01' else 'test'
  for h in [2,3]:
   a=pd.read_parquet(R/phase/month/f'rf_h{h}_scores.parquet');z=daily(a,'rf',h)
   if phase=='validation':z=selection_eligible(z)
   z['phase']=phase;parts.append(z)
 rf=pd.concat(parts,ignore_index=True);rf.to_csv(R/'rf_daily.csv.gz',index=False)
 old=[]
 for phase in ['validation','test']:
  x=pd.read_csv(PREV/f'{phase}_daily.csv.gz',dtype={'top1':str});x['phase']=phase;old.append(x)
 allrows=pd.concat([rf,*old],ignore_index=True);paired=[]
 for (phase,h),g in allrows.groupby(['phase','horizon']):
  names=['rf','logistic','hgb'] if phase=='validation' else ['rf','logistic'];g=g[g.model.isin(names)]
  wide=g.pivot(index='date',columns='model',values='top3_net');common=wide.dropna().index;z=g[g.date.isin(common)].copy();assert len(z)==len(common)*len(names)
  for model,b in z.groupby('model'):
   row=dict(phase=phase,horizon=h,model=model,paired_days=len(b),top3_net=b.top3_net.mean(),pool_net=b.pool_net.mean(),top3_excess=(b.top3_net-b.pool_net).mean(),top3_up=b.top3_up.mean(),pool_up=b.pool_up.mean(),top3_down=b.top3_down.mean(),pool_down=b.pool_down.mean(),top1_known=int(b.top1_net.notna().sum()),top1_net=b.top1_net.mean(),top1_up=(b.top1_net.dropna().round(10)>.02).mean(),top1_down=(b.top1_net.dropna().round(10)<-.02).mean(),days_with_unknown=int(g[g.model.eq(model)].top3_net.isna().sum()),all_days=len(g[g.model.eq(model)]))
   paired.append(row)
 pair=pd.DataFrame(paired);pair.to_csv(D/'paired_overall.csv',index=False)
 allrows.to_csv(R/'all_daily.csv.gz',index=False)
 monthly=[];interval=[];rng=np.random.default_rng(20261027)
 for (phase,h),g in allrows.groupby(['phase','horizon']):
  g=g[g.model.isin(['rf','logistic'])];wide=g.pivot(index='date',columns='model',values='top3_net').dropna();z=g[g.date.isin(wide.index)]
  for month,monthrows in z.groupby('month'):
   for model,b in monthrows.groupby('model'):
    monthly.append(dict(phase=phase,horizon=h,month=month,model=model,paired_days=len(b),top3_net=b.top3_net.mean(),pool_net=b.pool_net.mean(),top3_up=b.top3_up.mean(),top3_down=b.top3_down.mean()))
  delta=wide.rf-wide.logistic;gg=delta.groupby(delta.index.str[:7]).agg(['sum','count']);idx=rng.integers(0,len(gg),size=(10000,len(gg)));boots=gg['sum'].to_numpy()[idx].sum(axis=1)/gg['count'].to_numpy()[idx].sum(axis=1)
  interval.append(dict(phase=phase,horizon=h,days=len(delta),months=len(gg),rf_minus_logistic=float(delta.mean()),ci_low=float(np.quantile(boots,.025)),ci_high=float(np.quantile(boots,.975)),months_rf_higher=int((gg['sum']>0).sum())))
 pd.DataFrame(monthly).to_csv(D/'paired_monthly.csv',index=False);pd.DataFrame(interval).to_csv(D/'paired_bootstrap.csv',index=False)
 print('PAIRED\n',pair.to_string(index=False));print('INTERVALS\n',pd.DataFrame(interval).to_string(index=False))
if __name__=='__main__':main()
