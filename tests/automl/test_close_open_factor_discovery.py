from types import SimpleNamespace

import numpy as np
import pandas as pd

from scripts.build_automl_close_open_factor_panel import (
    CLOCKS, FACTORS, add_labels, extract_features, historical_features, morning_frame,
)


def history():
    days = pd.bdate_range('2024-11-01',periods=70).strftime('%Y-%m-%d')
    return pd.DataFrame({'trade_date':days,'stk_code':'000001','preclose':10.,
                         'open':10.,'high':10.6,'low':9.9,'close':10.2,
                         'volume':100000.,'turn_ratio':1.})


def minute_data():
    c = np.linspace(10,10.4,220)
    return pd.DataFrame({'symbol6':'000001','clock':CLOCKS,'open':c,'high':c+.01,
                         'low':c-.01,'close':c,'volume':100.,'turnover':c*100,'valid':True})


def test_daily_features_ignore_current_and_future_ohlc():
    raw = history()
    day = raw.trade_date.iloc[45]
    before = historical_features(raw)
    raw.loc[raw.trade_date.ge(day),['open','high','low','close','volume','turn_ratio']] *= 2
    after = historical_features(raw)
    cols = [c for c in before if c not in ('next_corporate_action',)]
    pd.testing.assert_frame_equal(before.loc[before.signal_date.eq(day),cols],
                                  after.loc[after.signal_date.eq(day),cols])


def test_minute_factors_ignore_after_decision_and_reject_missing_bar():
    item = SimpleNamespace(**historical_features(history()).iloc[-2].to_dict())
    bars = minute_data()
    a = extract_features(bars,item)
    later = bars.iloc[-1:].assign(clock='14:41:00',close=100.,high=100.,volume=1e12)
    b = extract_features(pd.concat([bars,later]),item)
    assert a == b
    assert set(FACTORS).issubset(a)
    assert extract_features(bars.iloc[1:],item) is None


def test_labels_keep_middle_unknown_and_exact_boundaries():
    d = add_labels(pd.DataFrame({'signal_price':[100.]*5,
                                'second_high':[103.,102.99,99.,100.,np.nan],
                                'max_high':[103.,104.,99.99,100.,np.nan],
                                'close40':[102.,103.,99.,100.,np.nan]}))
    assert d.strong.iloc[:4].tolist() == [1.,0.,0.,0.]
    assert d.weak.iloc[:4].tolist() == [0.,0.,1.,0.]
    assert np.isnan(d.strong.iloc[-1]) and np.isnan(d.weak.iloc[-1])


def test_second_high_keeps_equal_prices_in_distinct_minutes():
    bars = minute_data().iloc[:10].copy()
    bars.loc[8:9,'high'] = 10.8
    m = morning_frame(bars,'2025-01-02')
    assert m.second_high.iloc[0] == 10.8 and m.max_high.iloc[0] == 10.8
    assert morning_frame(pd.concat([bars,bars.iloc[-1:]]),'2025-01-02').empty


def test_split_purges_boundary_future_labels():
    from scripts.analyze_automl_close_open_factors import sample_split
    d = pd.DataFrame({'signal_date':['2025-06-27','2025-06-30','2025-12-31','2026-01-05'],
                      'next_date':['2025-06-30','2025-07-01','2026-01-05','2026-01-06'],
                      'strong':1.,'weak':0.})
    assert sample_split(d).signal_date.tolist() == ['2025-06-27','2026-01-05']


def test_ties_are_not_broken_by_identifier_and_bh_controls_family():
    from scripts.analyze_automl_close_open_factors import tail_masks,bh
    hi,lo = tail_masks(np.ones(50))
    assert not hi.any() and not lo.any()
    result = bh(pd.Series([.001,.03,.7,np.nan]))
    assert np.allclose(result.iloc[:3],[.003,.045,.7])
    assert np.isnan(result.iloc[-1])


def test_discovery_direction_is_frozen_before_holdout():
    from scripts.analyze_automl_close_open_factors import freeze_discovery,oriented_summary
    dates = pd.bdate_range('2025-01-02',periods=120).strftime('%Y-%m-%d')
    train = pd.DataFrame([{**{f:float(i) for f in FACTORS},'signal_date':day}
                           for day in dates for i in range(25)])
    daily = pd.DataFrame({'date':dates,'phase':'discovery','factor':FACTORS[0],
                          'delta_quality':.1,'delta_strong':.05,'delta_weak':-.05,
                          'delta_close40':.01,'delta_clipped_close40':.01,
                          'auc_strong':.6,'auc_weak':.4,'auc_extremes':.6})
    screen,rules = freeze_discovery(daily,train)
    assert rules[FACTORS[0]]['direction'] == 1
    opposite = daily.assign(phase='validation',delta_quality=-.1,delta_strong=-.05,delta_weak=.05)
    summary,_ = oriented_summary(opposite,rules)
    assert summary.quality_lift_pp.iloc[0] < 0
