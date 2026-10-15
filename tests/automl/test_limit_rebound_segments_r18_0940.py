from types import SimpleNamespace
import numpy as np,pandas as pd
from scripts.enrich_limit_rebound_segments_r18_0940 import facts,return_columns,DAY_CLOCK


def sample():
    b=pd.DataFrame({'time':['2025-09-02 '+t+':00' for t in DAY_CLOCK],
      'open':10.,'high':10.1,'low':9.9,'close':10.,'volume':1.})
    r=SimpleNamespace(d1_open=10.,d1_close=10.,d1_volume=240.,d1_cap=11.,d1_st=0)
    return b,r


def test_entry_is_window_high_and_ignores_later_high():
    b,r=sample();b.loc[50,'high']=10.9
    x=facts(b,r)
    assert x['minute_ok'] and x['can_buy0940']==1 and x['buy0940']==10.1
    assert x['buy_at_cap']==0


def test_later_opening_before0940_and_cap_priced_entry():
    b,r=sample();b.loc[:9,['open','high','low','close']]=11.;r.d1_open=11.
    assert facts(b,r)['can_buy0940']==0
    b.loc[5,'low']=10.8
    x=facts(b,r)
    assert x['can_buy0940']==1 and x['buy0940']==11. and x['buy_at_cap']==1


def test_incomplete_or_mismatched_minutes_are_unknown_not_negative():
    b,r=sample();x=facts(b.drop(index=4),r)
    assert not x['minute_ok'] and x['can_buy0940'] is None
    r.d1_volume=999
    assert facts(b,r)['reason']=='全天量不符'


def test_each_return_has_its_own_complete_continuous_horizon():
    f=pd.DataFrame({'can_buy0940':[1,1,0],'buy0940':[10.,10.,np.nan],
       'd1_close':[10.,10.,10.],'d2_preclose':[10.,9.,10.],'d2_close':[11.,11.,11.],
       'd3_preclose':[11.,11.,11.],'d3_close':[12.,12.,12.],
       'd4_preclose':[np.nan,12.,12.],'d4_close':[np.nan,13.,13.]})
    a=return_columns(f)
    assert np.isclose(a.loc[0,'return0940_d2'],.1) and np.isclose(a.loc[0,'return0940_d3'],.2)
    assert np.isnan(a.loc[0,'return0940_d4'])
    assert a.loc[1,['return0940_d2','return0940_d3','return0940_d4']].isna().all()
    assert a.loc[2,['return0940_d2','return0940_d3','return0940_d4']].isna().all()
