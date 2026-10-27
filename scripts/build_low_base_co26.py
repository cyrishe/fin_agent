"""Build D-close features for broad low-base pool, independently of labels."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.research_gentle_rise_gr1 import sha
R=Path('outputs/stock_automl/low_base_co25_20261027');O=Path('outputs/stock_automl/low_base_ml_co26_20261027')
CORE=['position60','return5','return20','ma5_slope3','range5','tr_contraction','volume_contraction','close_location','excess5','log_amount20']
EXTRA=['return1','return3','return10','position20','position120','volatility20','close_ma20','volume1_prior20','open_gap','body','upper_shadow','lower_shadow']

def main():
 s=pd.read_parquet(R/'panel.parquet',filters=[('low','=',True)]);s.date=pd.to_datetime(s.date)
 d=pd.read_parquet(R/'daily_inputs.parquet');d.trade_date=pd.to_datetime(d.trade_date)
 p={k:d.pivot(index='trade_date',columns='stk_code',values=k).sort_index() for k in ['close','high','low','open','preclose','volume','amount']};del d
 c,pc,h,l,o,v=p['close'],p['preclose'],p['high'],p['low'],p['open'],p['volume'];good=np.isfinite(c)&c.gt(0)
 for x in p.values():good &= np.isfinite(x)&x.gt(0)
 good &= h.ge(c)&h.ge(o)&l.le(c)&l.le(o)&h.ge(l)
 ratio=(c/pc).where(good);chain=np.exp(np.log(ratio).fillna(0).cumsum()).where(good);ah=h/c*chain;al=l/c*chain
 keys=pd.MultiIndex.from_frame(s[['date','stk_code']])
 def put(name,df):
  s[name]=df.stack(future_stack=True).reindex(keys).to_numpy();print(name,int(s[name].notna().sum()),flush=True)
 put('return1',ratio-1);put('return3',chain/chain.shift(3)-1);put('return10',chain/chain.shift(10)-1)
 for w in [20,120]:
  lo=al.rolling(w,min_periods=w).min();hi=ah.rolling(w,min_periods=w).max()
  put(f'position{w}',((chain-lo)/(hi-lo).where(hi.gt(lo))).where(good.rolling(w,min_periods=w).sum().eq(w)))
 put('volatility20',(ratio-1).rolling(20,min_periods=20).std());put('close_ma20',chain/chain.rolling(20,min_periods=20).mean()-1)
 put('volume1_prior20',v/v.shift(1).rolling(20,min_periods=20).mean());put('open_gap',o/pc-1);put('body',(c-o)/pc)
 put('upper_shadow',(h-np.maximum(c,o))/pc);put('lower_shadow',(np.minimum(c,o)-l)/pc)
 assert not s.duplicated(['date','stk_code']).any() and s.low.all()
 s['date']=s.date.dt.strftime('%Y-%m-%d');s.to_parquet(O/'samples.parquet',index=False)
 meta={'source_panel_sha256':sha(R/'panel.parquet'),'source_daily_sha256':sha(R/'daily_inputs.parquet'),'code_sha256':sha(Path(__file__)),'sample_sha256':sha(O/'samples.parquet'),'rows':len(s),'features':CORE+EXTRA,'missing':s[CORE+EXTRA].isna().sum().to_dict(),'latest_feature_input':'D close, no D+1 outcomes','candidate':'CO25 L: position60<=.35 and return20<=.08'}
 (O/'build_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=int));print('COMPLETE',len(s),flush=True)
if __name__=='__main__':main()
