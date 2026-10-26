import numpy as np
import pandas as pd
from scripts.research_low_base_co25 import calculate, fit_predict, predict, FEATURES


def bars(n=100):
    days=pd.bdate_range('2024-01-01',periods=n)
    c=np.r_[np.linspace(130,100,60),np.full(n-60,100.)]
    return {k:pd.DataFrame(x,index=days,columns=['000001.SZ'],copy=True) for k,x in {
        'close':c,'open':c,'high':c*1.01,'low':c*.99,
        'preclose':np.r_[c[0],c[:-1]],'volume':np.r_[np.full(80,1e6),np.full(n-80,.6e6)],
        'amount':np.full(n,1e8)}.items()}


def test_known_low_stable_compact_case():
    p=bars();f,m,_=calculate(p)
    # Recent range contracts relative to the earlier decline, but 2% daily
    # range is constant, so this fixture must NOT pass the contraction rule.
    assert m['low'].iloc[85,0] and m['stable'].iloc[85,0]
    assert not m['compact'].iloc[85,0]
    np.testing.assert_allclose(f['volume_contraction'].iloc[85,0],.6/.98)
    p['high'].iloc[81:]=100.5;p['low'].iloc[81:]=99.5
    f,m,_=calculate(p)
    assert m['compact'].iloc[85,0]
    assert f['first_compact'].iloc[:86].sum().iloc[0]>=1


def test_future_prices_do_not_change_signals():
    p=bars();f,m,_=calculate(p);other={k:v.copy() for k,v in p.items()}
    for k in ['open','close','high','low','preclose']:
        other[k].iloc[87:]*=1.37
    g,n,_=calculate(other)
    for k in FEATURES:np.testing.assert_allclose(f[k].iloc[:87],g[k].iloc[:87],equal_nan=True)
    for k in m:pd.testing.assert_frame_equal(m[k].iloc[:87],n[k].iloc[:87])


def test_split_scale_does_not_make_false_low_or_profit():
    p=bars();f,m,_=calculate(p)
    for k in ['open','close','high','low','preclose']:p[k].iloc[80:]/=2
    g,n,_=calculate(p)
    for k in FEATURES:np.testing.assert_allclose(f[k],g[k],equal_nan=True)
    np.testing.assert_allclose(f['net3'],g['net3'],equal_nan=True)


def test_entry_is_next_open_and_halt_unknown():
    p=bars();p['open'].iloc[86]=100.5
    f,_,_=calculate(p)
    assert abs(f['net2'].iloc[85,0]-(100/100.5*.999/1.001-1))<1e-12
    p['volume'].iloc[87]=0
    f,_,_=calculate(p)
    assert np.isnan(f['net2'].iloc[85,0])
    assert np.isnan(f['event3'].iloc[85,0])


def test_one_price_entry_not_assumed_executable():
    p=bars();p['high'].iloc[86]=100;p['low'].iloc[86]=100
    f,_,_=calculate(p)
    assert np.isnan(f['net2'].iloc[85,0])
    assert f['event2'].iloc[85,0]==0


def test_test_targets_cannot_affect_fit_or_prediction():
    rng=np.random.default_rng(42)
    tr=pd.DataFrame(rng.normal(size=(100,10)),columns=FEATURES)
    tr['sample_id']=range(100);tr['date']=np.repeat(['a','b','c','d'],25)
    tr['net2']=.01*tr.return5+rng.normal(0,.01,100)
    te=tr.iloc[:10].copy();a,pa,_=fit_predict(tr,te,2)
    te['net2']=999
    b,pb,_=fit_predict(tr,te,2)
    np.testing.assert_array_equal(pa,pb)
    np.testing.assert_array_equal(pa,predict(a,te))


def test_summary_does_not_count_roundoff_as_price_gain():
    from scripts.analyze_low_base_co25 import daily_stats
    x=pd.DataFrame({'date':['d']*4,'event2':[1e-16,-1e-16,.03-1e-16,np.nan]})
    d=daily_stats(x,'event2').iloc[0]
    assert d.known==3
    assert d.up==1/3 and d.strong==1/3
