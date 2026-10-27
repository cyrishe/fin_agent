"""CO26 pre-model 2024-only population and label distribution diagnostics."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.research_gentle_rise_gr1 import sha
R=Path('outputs/stock_automl/low_base_co25_20261027');O=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_ml_co26')

def main():
 d=pd.read_parquet(R/'daily_inputs.parquet');d.trade_date=pd.to_datetime(d.trade_date)
 p={k:d.pivot(index='trade_date',columns='stk_code',values=k).sort_index() for k in ['close','high','low','open','preclose','volume','amount']};del d
 c,pc,h,l=p['close'],p['preclose'],p['high'],p['low'];good=np.isfinite(c)&c.gt(0)
 for x in p.values():good &= np.isfinite(x)&x.gt(0)
 good &= h.ge(c)&h.ge(p['open'])&l.le(c)&l.le(p['open'])&h.ge(l)
 chain=np.exp(np.log((c/pc).where(good)).fillna(0).cumsum()).where(good);ah=h/c*chain;al=l/c*chain
 panel=pd.read_parquet(R/'panel.parquet');z=panel[panel.date.str.startswith('2024')].copy();z['date']=pd.to_datetime(z.date)
 keys=pd.MultiIndex.from_frame(z[['date','stk_code']]);raw={}
 for w in [20,40,60,120]:
  n=good.rolling(w,min_periods=w).sum().eq(w);lo=al.rolling(w,min_periods=w).min();hi=ah.rolling(w,min_periods=w).max();v=((chain-lo)/(hi-lo).where(hi.gt(lo))).where(n)
  raw[w]=v.stack(future_stack=True).reindex(keys).to_numpy();z[f'position{w}']=raw[w]
 windows=[];labels=[]
 for name in ['eligible','low','stable','compact','position20','position40','position60','position120']:
  if name=='eligible': mask=np.ones(len(z),bool);valid=mask
  elif name.startswith('position'):
   val=z[name];valid=val.notna().to_numpy();mask=(val.le(.35)&z.return20.le(.08)).to_numpy()
  else:mask=z[name].to_numpy(bool);valid=np.ones(len(z),bool)
  subset=z[mask];counts=subset.groupby('date').size().reindex(sorted(z.date.unique()),fill_value=0)
  windows.append(dict(candidate=name,rows=len(subset),unique_stocks=subset.stk_code.nunique(),signal_days=len(counts),mean_per_day=counts.mean(),min_per_day=int(counts.min()),max_per_day=int(counts.max()),available_rows=int(valid.sum())))
  for h in [2,3]:
   v=subset[f'net{h}'];known=v.dropna();r=dict(candidate=name,horizon=h,total=len(v),known=len(known),unknown=int(v.isna().sum()),mean=float(known.mean()),median=float(known.median()))
   for t in [1,2,3]:r[f'up_gt{t}']=int(known.gt(t/100).sum());r[f'down_lt{t}']=int(known.lt(-t/100).sum());r[f'middle{t}']=int(known.between(-t/100,t/100).sum())
   labels.append(r)
 pd.DataFrame(windows).to_csv(D/'discovery_windows.csv',index=False);pd.DataFrame(labels).to_csv(D/'discovery_labels.csv',index=False)
 meta=dict(discovery='2024',panel_sha256=sha(R/'panel.parquet'),daily_sha256=sha(R/'daily_inputs.parquet'),plan_sha256=sha(D/'DISCOVERY_PLAN.md'),rows=len(z),label='D+1 open to D+2/D+3 close net, .1% each side',rows_and_days='per-stock-day; adjacent observations correlated')
 (O/'discovery_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(pd.DataFrame(windows).to_string(index=False));print(pd.DataFrame(labels).to_string(index=False))
if __name__=='__main__':main()
