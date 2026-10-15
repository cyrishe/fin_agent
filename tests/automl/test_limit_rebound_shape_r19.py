from types import SimpleNamespace
import numpy as np
import pandas as pd
from scripts.experiment_limit_rebound_shape_r19 import features,facts,DAY_CLOCK,rules,mask

def raw():
    b=pd.DataFrame({'time':['2025-01-03 '+t+':00' for t in DAY_CLOCK],
      'open':10.,'high':10.,'low':10.,'close':10.,'volume':100.})
    for i,(o,h,l,c) in enumerate([(10,10.2,9.9,10.1),(10.1,10.2,10,10),
       (10,10.5,10,10.4),(10.4,10.8,10.4,10.7),(10.7,10.9,10.6,10.8)]):
        b.loc[i,['open','high','low','close']]=[o,h,l,c]
    return b

def quote(b):
    return SimpleNamespace(d1_open=b.open.iloc[0],d1_high=b.high.max(),d1_low=b.low.min(),
       d1_close=b.close.iloc[-1],d1_volume=b.volume.sum(),d1_preclose=10.,d1_cap=11.)

def test_features_cannot_see_later_bars():
    b=raw();a=features(b,10,11)
    changed=b.copy();changed.loc[5:,['open','high','low','close']]=[8,15,5,12]
    changed.loc[5:,'volume']=1000000
    assert features(changed,10,11)==a

def test_non_touch_pattern_still_can_trade_and_no_retroactive_entry():
    b=raw();b.loc[5,['open','high','low','close']]=[11,11,10.7,11]
    b.loc[6,['open','high','low','close']]=[10.8,11,10.7,10.9]
    result=facts(b,quote(b))
    assert result['audit_ok'] and result['touch5']==0
    assert result['entry']==10.8 and result['entry_time']=='09:37'
    assert not result['entry_whole_below']

def test_first_five_sealed_does_not_force_skip_if_it_opens_later():
    b=raw();b.loc[:6,['open','high','low','close']]=11
    b.loc[7,['open','high','low','close']]=[10.9,10.95,10.8,10.9]
    result=facts(b,quote(b))
    assert result['touch5']==5 and result['below5']==0
    assert result['fill'] and result['entry_time']=='09:38'

def test_no_fill_and_data_missing_are_distinct():
    b=raw();b.loc[5:9,['open','high','low','close']]=11
    z=facts(b,quote(b));assert z['audit_ok'] and not z['fill'] and z['entry'] is None
    z=facts(b.iloc[:-1],quote(b));assert not z['audit_ok'] and 'fill' not in z

def test_fixed_rules_small_and_no_future_fields():
    for r in rules():
        assert len(r['atoms'])<=2
        assert all(a[0] in ['high5','low5','body5','pullback5','position5','below5','whole5','touch5','shape'] for a in r['atoms'])
