import numpy as np
import pandas as pd
import pytest
from src.experiments.kline_patterns.engine import evaluate,prepare,detect,add_pivots
from src.experiments.kline_patterns.catalog import CATALOG
PAT={p['id']:p for p in CATALOG}
def bars(n=210):
 rng=np.random.default_rng(171);c=100+np.cumsum(rng.normal(.04,1,n));o=c+rng.normal(0,.5,n)
 return pd.DataFrame({'open':o,'high':np.maximum(o,c)+1,'low':np.minimum(o,c)-1,'close':c,'volume':rng.uniform(100,1000,n)},index=pd.bdate_range('2020-01-01',periods=n))
def test_bull_engulfing_known_positive_near_miss_and_context():
 f=pd.DataFrame({'c':[15,14,13,12,11,10,9,10,12], 'o':[15,14,13,12,11,10,9,12,9.8], 'body':[0,0,0,0,0,0,0,2,2.2], 'range':3.,'body_avg':1.,'valid':1.},dtype=float)
 assert detect(PAT['engulf_bull'],f,8)
 f.loc[8,'c']=11.9
 assert not detect(PAT['engulf_bull'],f,8) # does not engulf prior open
 f.loc[8,'c']=12;f.loc[1,'c']=8
 assert not detect(PAT['engulf_bull'],f,8) # no preceding decline
@pytest.mark.parametrize('expr',['c[1]>0','__import__("os")','c.__class__','open("x")'])
def test_formula_rejects_future_and_code(expr):
 with pytest.raises(ValueError):evaluate(expr,pd.DataFrame({'c':[1,2]}))
def test_missing_is_unknown_not_zero():
 f=pd.DataFrame({'c':[1,np.nan,2]})
 assert pd.isna(evaluate('c[-1] > 0',f,2))
 assert pd.isna(evaluate('min(c[0],c[-1]) > 0',f,2))
 assert pd.isna(evaluate('c[0] / 0 > 1',f,2))
def test_cross_is_event_not_state():
 f=pd.DataFrame({'rsi':[29,31,32],'valid':1.})
 assert list(detect(PAT['rsi_reentry_bull'],f).fillna(False))==[False,True,False]
def test_all_rules_scalar_vector_agree_and_no_future_leak():
 b=bars();f=prepare(b)
 for t in (150,180,209):
  prefix=prepare(b.iloc[:t+1]);pd.testing.assert_series_equal(prefix.iloc[-1],f.iloc[t])
  for p in CATALOG:
   vec=detect(p,f).iloc[t];one=detect(p,f,t);pre=detect(p,prefix,t)
   assert (pd.isna(vec) and pd.isna(one) and pd.isna(pre)) or (vec==one==pre),p['id']
def test_price_scale_does_not_change_patterns():
 b=bars();scaled=b.copy();scaled[['open','high','low','close']]*=7
 f=prepare(b);g=prepare(scaled)
 for p in CATALOG:pd.testing.assert_series_equal(detect(p,f),detect(p,g),check_names=False)
def test_invalid_ohlc_is_gap_not_false_candle():
 b=bars();b.iloc[160,b.columns.get_loc('high')]=1;f=prepare(b)
 assert f.valid.iloc[160]==0 and pd.isna(f.c.iloc[160])
 assert not detect(PAT['doji'],f,160)
def test_confirmed_pivot_time_is_two_bars_later():
 f=pd.DataFrame({'l':[8,7,6,7,8,7,6,5,6,7], 'h':np.arange(10)+20.,'dif':np.arange(10,dtype=float),'rsi':np.arange(10)+40.,'atr':1.})
 out=add_pivots(f)
 assert pd.isna(out.lo_p2.iloc[7]) and pd.isna(out.lo_p2.iloc[8])
 assert out.lo_p2.iloc[9]==5 and out.lo_i2.iloc[9]==7 and out.lo_event.iloc[9]==1
 assert detect(PAT['macd_div_bull'],out.assign(valid=1.),9)
def test_dates_must_be_ordered():
 with pytest.raises(ValueError):prepare(bars().iloc[::-1])
