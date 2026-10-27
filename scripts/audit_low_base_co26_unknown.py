"""Audit missing Top3 outcomes; hypothetical stress is not an execution result."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
R=Path('outputs/stock_automl/low_base_ml_co26_20261027');SRC=Path('outputs/stock_automl/low_base_co25_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_ml_co26')

def main():
 sel=json.loads((R/'selection.json').read_text())['selected_models'];months=json.loads((R/'test_complete.json').read_text())['months'];parts=[]
 for h in [2,3]:
  for m in months:
   s=pd.read_parquet(R/'test'/m/f'{sel[str(h)]}_h{h}_scores.parquet').sort_values(['date','score','stk_code'],ascending=[True,False,True]);s=s.groupby('date',sort=False).head(3);s['horizon']=h;parts.append(s)
 picks=pd.concat(parts,ignore_index=True);missing=picks[picks.net.isna()].copy()
 raw=pd.read_parquet(SRC/'daily_inputs.parquet',filters=[('stk_code','in',missing.stk_code.unique().tolist())]);raw.trade_date=pd.to_datetime(raw.trade_date).dt.strftime('%Y-%m-%d');raw=raw.set_index(['stk_code','trade_date'])
 cal=pd.read_csv(SRC/'calendar.csv').date.tolist();ci={d:i for i,d in enumerate(cal)};detail=[]
 for r in missing.itertuples():
  i=ci[r.date];day1=cal[i+1];last=cal[i+r.horizon];entry=raw.loc[(r.stk_code,day1)] if (r.stk_code,day1) in raw.index else None;exit=raw.loc[(r.stk_code,last)] if (r.stk_code,last) in raw.index else None
  if entry is None or pd.isna(entry.open) or entry.open<=0:reason='entry_missing'
  elif entry.high==entry.low:reason='entry_one_price'
  elif exit is None or pd.isna(exit.close) or exit.close<=0:reason='exit_missing'
  elif exit.high==exit.low and exit.close/exit.preclose<.955:reason='suspected_exit_lock'
  else:reason='other_missing_path'
  detail.append(dict(date=r.date,stock=r.stk_code,horizon=r.horizon,reason=reason))
 det=pd.DataFrame(detail,columns=['date','stock','horizon','reason']);det.to_csv(D/'unknown_top3_detail.csv',index=False)
 daily_base=pd.read_csv(R/'test_daily.csv.gz');rows=[]
 for h,g in picks.groupby('horizon'):
  daily=g.groupby('date');out=dict(horizon=int(h),days=len(daily),slots=len(g),unknown_slots=int(g.net.isna().sum()),days_with_unknown=int(daily.net.apply(lambda x:x.isna().any()).sum()))
  for val in [-.05,.05]:out[f'assumed_{int(val*100):+d}pct_mean']=float(g.net.fillna(val).groupby(g.date).mean().mean())
  base=daily_base[(daily_base.horizon==h)&daily_base.model.eq(sel[str(h)])];assert set(base.date)==set(g.date)
  out['pool_net_on_same_days']=float(base.pool_net.mean())
  out['known_only_slot_mean']=float(g.net.mean());out['reasons']=det[det.horizon.eq(h)].reason.value_counts().to_dict();rows.append(out)
 (D/'unknown_top3.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(json.dumps(rows,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
