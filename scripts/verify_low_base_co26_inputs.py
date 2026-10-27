"""Independently recompute a stratified sample from raw D-close data."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.build_low_base_co26 import EXTRA
R=Path('outputs/stock_automl/low_base_co25_20261027');O=Path('outputs/stock_automl/low_base_ml_co26_20261027')

def calculate(g,idx):
 t=g.iloc[:idx+1].copy();good=t[['open','high','low','close','preclose','volume','amount']].notna().all(axis=1)
 for n in ['open','high','low','close','preclose','volume','amount']:good &= t[n].gt(0)
 good &= t.high.ge(t.close)&t.high.ge(t.open)&t.low.le(t.close)&t.low.le(t.open)&t.high.ge(t.low)
 ratio=(t.close/t.preclose).where(good);chain=np.exp(np.log(ratio).fillna(0).cumsum()).where(good);hh=t.high/t.close*chain;ll=t.low/t.close*chain
 z=t.iloc[-1];last=chain.iloc[-1];r={}
 r['return1']=ratio.iloc[-1]-1
 for n in [3,10]:r[f'return{n}']=last/chain.iloc[-n-1]-1
 for n in [20,120]:
  high=hh.iloc[-n:];low=ll.iloc[-n:];r[f'position{n}']=(last-low.min())/(high.max()-low.min()) if len(high)==n and good.iloc[-n:].all() and high.max()>low.min() else np.nan
 r['volatility20']=(ratio.iloc[-20:]-1).std();r['close_ma20']=last/chain.iloc[-20:].mean()-1
 r['volume1_prior20']=z.volume/t.volume.iloc[-21:-1].mean();r['open_gap']=z.open/z.preclose-1;r['body']=(z.close-z.open)/z.preclose
 r['upper_shadow']=(z.high-max(z.close,z.open))/z.preclose;r['lower_shadow']=(min(z.close,z.open)-z.low)/z.preclose
 return r

def main():
 s=pd.read_parquet(O/'samples.parquet');picked=pd.concat([g.sample(n=min(40,len(g)),random_state=26027) for _,g in s.groupby(s.date.str[:4])]);codes=picked.stk_code.unique().tolist()
 raw=pd.read_parquet(R/'daily_inputs.parquet',filters=[('stk_code','in',codes)]);raw.trade_date=pd.to_datetime(raw.trade_date)
 dates=pd.DatetimeIndex(pd.to_datetime(pd.read_csv(R/'calendar.csv').date));groups={k:g.set_index('trade_date').reindex(dates) for k,g in raw.groupby('stk_code')}
 for row in picked.itertuples():
  g=groups[row.stk_code];idx=dates.get_loc(pd.Timestamp(row.date));expected=calculate(g,idx)
  for name in EXTRA:
   assert np.isclose(getattr(row,name),expected[name],atol=1e-10,rtol=1e-10,equal_nan=True),(row.date,row.stk_code,name,getattr(row,name),expected[name])
  future=g.copy();future.iloc[idx+1:,future.columns.get_loc('close')]=1e9
  perturb=calculate(future,idx)
  for name in EXTRA:assert np.isclose(expected[name],perturb[name],atol=1e-12,rtol=1e-12,equal_nan=True)
  for h in [2,3]:
   sub=g.iloc[idx+1:idx+h+1];val=getattr(row,f'net{h}')
   if pd.notna(val):
    adjusted=np.prod(sub.close/sub.preclose)/(sub.open.iloc[0]/sub.preclose.iloc[0])*(.999/1.001)-1
    assert np.isclose(val,adjusted,atol=1e-10,rtol=1e-10)
 result={'stratified_rows':len(picked),'feature_values':len(picked)*len(EXTRA),'features_match':True,'future_price_perturbation_unchanged':True,'known_net_returns_match':True,'test_count':4}
 (O/'input_verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
