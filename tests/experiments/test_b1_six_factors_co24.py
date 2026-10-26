import numpy as np
from scripts.research_b1_six_factors_co24 import six_values,CLOCK_MIN

def base():
 c=np.full(220,10.);v=np.ones(220);a=10*v
 return c,v,a,np.ones(60)

def test_breakout_fixed_denominator_and_breach():
 c,v,a,q=base();c[-21:]=10.1
 f,_=six_values(c,v,a,q,10,.2)
 assert f[0]==.7 and f[1]==0
 c[-1]=9.94;f,_=six_values(c,v,a,q,10,.2)
 np.testing.assert_allclose(f[1],.3)
 c[:]=10;c[-1]=10.1;f,_=six_values(c,v,a,q,10,.2)
 assert f[0]==1/30

def test_no_event_is_not_perfect_support():
 c,v,a,q=base();f,meta=six_values(c,v,a,q,11,.2)
 assert f[0]==0 and np.isnan(f[1]) and np.isnan(f[2]) and np.isnan(f[3])
 assert np.isnan(f[4]) and f[5]==1 and not meta['breakout_observed']

def test_pullback_volume_recovery_and_not_yet_recovered():
 c,v,a,q=base();c[-60:]=np.r_[np.linspace(10,10.5,21),np.linspace(10.49,10.2,19),np.linspace(10.21,10.41,20)]
 v[-60:]=1;v[-59:-39]=2.4;v[-39:-20]=1.2
 f,_=six_values(c,v,c*v,q,11,.2)
 np.testing.assert_allclose(f[2:4],[.5,.7])
 c[-1]=10.1;f,_=six_values(c,v,c*v,q,11,.2)
 assert f[3]==0

def test_clock_baseline_missing_does_not_erase_price_recovery():
 c,v,a,q=base();c[-60:]=np.r_[np.linspace(10,10.5,21),np.linspace(10.49,10.2,19),np.linspace(10.21,10.41,20)]
 q[1]=np.nan;f,_=six_values(c,v,c*v,q,11,.2)
 assert np.isnan(f[2]) and np.isfinite(f[3])

def test_last_five_positive_share_is_not_net_return_share():
 c,v,a,q=base();r=np.zeros(30);r[:25]=.021/25;r[-5:]=.009/5;c[-30:]=10*np.exp(np.cumsum(r))
 f,_=six_values(c,v,c*v,q,20,.2)
 np.testing.assert_allclose(f[4],.3)

def test_vwap_uses_each_minutes_prefix_and_zero_volume():
 c,v,a,q=base();c[-30:]=np.r_[np.full(6,9.),np.full(24,11.)]
 # Amount deliberately fixes each cumulative VWAP at 10, making a known count.
 f,_=six_values(c,v,a,q,20,.2);assert f[5]==.8
 f,_=six_values(c,v*0,a*0,q,20,.2);assert np.isnan(f[5])

def test_current_prefix_excludes_future_bars():
 assert CLOCK_MIN[-1]==14*60+40 and len(CLOCK_MIN)==220
 c,v,a,q=base();future=np.r_[c,np.full(20,1e6)]
 f,_=six_values(future[:220],v,a,q,11,.2)
 g,_=six_values(c,v,a,q,11,.2)
 np.testing.assert_allclose(f,g,equal_nan=True)

def test_incomplete_path_never_looks_like_a_good_zero():
 c,v,a,q=base();c[5]=np.nan;f,meta=six_values(c,v,a,q,11,.2)
 assert np.isnan(f).all() and not meta['path_complete']
