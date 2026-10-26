"""Independent scalar reconstruction of sampled CO24 feature inputs and daily anchors."""
import math,json
import numpy as np
import pandas as pd
from scripts.research_b1_six_factors_co24 import OUT,REPORT,OLD,SIX,read,save

def main():
 panel=read(OUT/'panel.csv.gz').set_index('sample_id')
 files=sorted((OUT/'feature_inputs').glob('20*.npz'));chosen=[files[i] for i in np.linspace(0,len(files)-1,12,dtype=int)]
 checked=0;anchor_keys=[]
 for path in chosen:
  z=np.load(path);ids=z['sample_id'];take=np.linspace(0,len(ids)-1,min(10,len(ids)),dtype=int)
  for i in take:
   c=z['close'][i].tolist();v=z['volume'][i].tolist();a=z['amount'][i].tolist();q=z['prior_volume60'][i].tolist();R=float(z['R'][i]);atr=float(z['atr'][i]);f=[np.nan]*6
   if all(math.isfinite(x) and x>0 for x in c):
    f[0]=sum(x>R for x in c[-30:])/30 if math.isfinite(R) else np.nan
    breakout=[j for j,x in enumerate(c) if x>R]
    if breakout and math.isfinite(atr) and atr>0:f[1]=max(R-min(c[breakout[0]:]),0)/atr
    tail=c[-60:];h=tail.index(max(tail))
    if 0<h<59:
     start=tail.index(min(tail[:h]));l=h+1+tail[h+1:].index(min(tail[h+1:]))
     if tail[start]<tail[h] and tail[l]<tail[h]:
      f[3]=(tail[-1]-tail[l])/(tail[h]-tail[l])
      up=list(range(start+1,h+1));down=list(range(h+1,l+1))
      if all(math.isfinite(q[j]) and q[j]>0 and math.isfinite(v[j+160]) and v[j+160]>=0 for j in up+down):
       uv=sum(v[j+160]/q[j] for j in up)/len(up);dv=sum(v[j+160]/q[j] for j in down)/len(down)
       if uv>0:f[2]=dv/uv
    increments=[max(math.log(c[j])-math.log(c[j-1]),0) for j in range(190,220)]
    if sum(increments)>0:f[4]=sum(increments[-5:])/sum(increments)
    if all(math.isfinite(x) and x>=0 for x in v+a) and sum(v[:191])>0:
     f[5]=sum(c[j]>=sum(a[:j+1])/sum(v[:j+1]) for j in range(190,220))/30
   row=panel.loc[ids[i]];np.testing.assert_allclose(f,row[SIX].astype(float),atol=1e-11,rtol=1e-10,equal_nan=True)
   anchor_keys.append((row.signal_date,row.symbol6,float(row.R),float(row.atr)));checked+=1
 # Backward conversion to the current day's price scale is independent of the
 # builder's forward cumulative-chain construction.
 columns=['trade_date','stk_code','preclose','high','low','close']
 raw=pd.concat([pd.read_csv(p,usecols=columns,dtype={'stk_code':str,'trade_date':str},float_precision='round_trip') for p in sorted((OLD/'limit_rebound_2y_r1/price_months').glob('price_*.csv.gz')) if '2024-09'<=p.name[6:13]<='2026-07'],ignore_index=True)
 raw['symbol6']=raw.stk_code.str[:6];groups={s:g.sort_values('trade_date') for s,g in raw[raw.symbol6.isin({x[1] for x in anchor_keys})].groupby('symbol6')}
 anchors=0
 for day,symbol,R,atr in anchor_keys:
  if not np.isfinite(R):continue
  d=groups[symbol];current=d[d.trade_date.eq(day)].iloc[0];past=d[d.trade_date.lt(day)].tail(20)
  scale=1.;next_preclose=float(current.preclose);highs=[];trs=[]
  for r in reversed(list(past.itertuples())):
   scale*=next_preclose/r.close;highs.append(r.high*scale)
   trs.append(max(r.high-r.low,abs(r.high-r.preclose),abs(r.low-r.preclose))*scale)
   next_preclose=r.preclose
  np.testing.assert_allclose([max(highs),sum(trs)/20],[R,atr],rtol=1e-10,atol=1e-10);anchors+=1
 save(REPORT/'feature_verification.json',dict(independent_feature_rows=checked,independent_history_anchors=anchors,sampled_dates=len(chosen),formulas_agree=True))
 print('VERIFIED',checked,'feature rows;',anchors,'history anchors',flush=True)
if __name__=='__main__':main()
