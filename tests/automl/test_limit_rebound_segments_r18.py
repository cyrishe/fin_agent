import numpy as np
import pandas as pd
from scripts.experiment_limit_rebound_segments_r18 import segment_one, FEATURES, ALIGNED, EXTRA


def frame():
    close=np.array([10.,10.2,10.3,11.33,12.46,13.71,12.])
    pre=np.r_[10.,close[:-1]]
    return pd.DataFrame({'trade_date':pd.date_range('2025-01-01',periods=7).strftime('%Y-%m-%d'),
      'symbol':'000001','date_i':np.arange(7),'up':[False,False,False,True,True,True,False],
      'preclose':pre,'open':pre,'high':close+.1,'low':pre-.1,'close':close,
      'volume':[100.,200.,300.,600.,60.,20.,700.],'amount':close*1000,
      'turn_ratio':np.arange(1.,8.),'ret':close/pre-1})


def test_pre_segment_is_anchored_to_first_board_and_future_invariant():
    g=frame();a,meta,_=segment_one(g,3,4)
    assert a['normal_days5']==3 and meta['normal_end']=='2025-01-03'
    assert np.isclose(a['d0_normal_volume_ratio'],60/200)
    assert np.isclose(a['board_normal_volume_ratio'],330/200)
    assert meta['raw_board_days']==2
    other=g.copy();other.loc[5:,['volume','close','up']]=[999999,999,False]
    b,_,_=segment_one(other,3,4)
    for c in a:np.testing.assert_allclose(a[c],b[c],equal_nan=True)
    truncated,_,_=segment_one(g.iloc[:5],3,4)
    for c in a:np.testing.assert_allclose(a[c],truncated[c],equal_nan=True)


def test_stops_at_previous_board_instead_of_skipping_it():
    g=frame();g.loc[0,'up']=True
    z,m,_=segment_one(g,3,4)
    assert z['normal_days5']==2 and m['normal_start']=='2025-01-02'
    assert z['d0_normal_volume_ratio']==60/250


def test_stops_at_market_gap_and_price_adjustment():
    g=frame();g.loc[:1,'date_i']-=1
    assert segment_one(g,3,4)[0]['normal_days5']==1
    g=frame();g.loc[3,'preclose']+=.1
    z,_,_=segment_one(g,3,4)
    assert z['normal_days5']==0 and np.isnan(z['d0_normal_volume_ratio'])


def test_run_summary_uses_current_prefix_only():
    g=frame();z,_,_=segment_one(g,3,3)
    assert z['board_turnover_sum']==4 and z['board_volume_slope']==0
    assert z['d0_board_volume_ratio']==1
    z,_,_=segment_one(g,3,4)
    assert z['board_turnover_sum']==9 and z['board_volume_slope']<0
    g.loc[4,'preclose']+=.1
    z,_,_=segment_one(g,3,4)
    assert z['board_price_ok']==0 and np.isnan(z['board_normal_volume_ratio'])


def test_feature_contract_has_no_future_or_duplicates():
    assert [len(FEATURES[x]) for x in FEATURES]==[35,35,51]
    for features in FEATURES.values():
        assert len(set(features))==len(features)
        assert not any(c.startswith(('d1_','d2_','d3_','d4_','label_')) for c in features)
        assert 'board_streak_days' in features
    assert set(ALIGNED+EXTRA)<=set(segment_one(frame(),3,4)[0])
