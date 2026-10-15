import numpy as np
import pandas as pd

from scripts.experiment_b1_market_trade_gate_co8 import (
    CLOCKS, historical, market_features, stock_features, fit_model, predict,
    chronological_split, choose_gate, settle, path_stats,
)
from scripts.report_b1_market_trade_gate_co8 import drawdown, account


def test_current_and_future_daily_close_volume_do_not_change_current_features():
    dates=pd.bdate_range('2025-01-01',periods=15).strftime('%Y-%m-%d')
    d=pd.DataFrame([dict(trade_date=day,symbol6=code,preclose=10.,close=10.+i*.02,
                         volume=10000.+i,turn_ratio=1.+i*.01)
                    for code in ['000001','600001'] for i,day in enumerate(dates)])
    h=historical(d);changed=d.copy();current=dates[10]
    changed.loc[changed.trade_date.ge(current),['close','volume','turn_ratio']]=[99.,999999.,88.]
    h2=historical(changed)
    cols=['prior_shares','prior_turn5','prior5_return','lag5_market_breadth','preclose']
    np.testing.assert_allclose(h.loc[h.trade_date.eq(current),cols],h2.loc[h2.trade_date.eq(current),cols])
    snap=pd.DataFrame({'symbol6':['000001','600001'],'close':[10.1,9.9]})
    assert market_features(snap,h[h.trade_date.eq(current)])==market_features(snap,h2[h2.trade_date.eq(current)])


def test_minutes_after_1440_cannot_change_gate_features():
    p=pd.DataFrame({'clock':CLOCKS,'open':10.,'high':10.2,'low':9.8,'close':10.1,'volume':100.,'valid':True})
    h=pd.Series(dict(prior_shares=1e6,prior_turn5=1.,prior5_return=.02,preclose=10.))
    a,ok=stock_features(p,h);assert ok
    future=pd.DataFrame({'clock':['14:41:00','15:00:00'],'open':999.,'high':9999.,'low':.01,'close':999.,'volume':1e12,'valid':True})
    b,ok2=stock_features(pd.concat([p,future]),h)
    assert ok2 and a==b


def test_missing_or_duplicate_decision_minute_is_detected():
    p=pd.DataFrame({'clock':CLOCKS,'open':10.,'high':10.2,'low':9.8,'close':10.1,'volume':100.,'valid':True})
    h=pd.Series(dict(prior_shares=1e6,prior_turn5=1.,prior5_return=.02,preclose=10.))
    assert stock_features(p.iloc[:-1],h)==({},False)
    assert stock_features(pd.concat([p,p.iloc[-1:]]),h)==({},False)


def test_split_keeps_labels_mature_and_future_outcomes_cannot_change_training():
    dates=pd.bdate_range('2025-01-01',periods=180)
    d=pd.DataFrame({'signal_date':dates.strftime('%Y-%m-%d'),'next_date':(dates+pd.offsets.BDay()).strftime('%Y-%m-%d'),
                    'selected':True,'net_main':.01,'corporate_action':False})
    d['decision_time']=d.signal_date+' 14:40:00';d['label_available_time']=d.next_date+' 09:40:00'
    train,val,test=chronological_split(d,'2025-08')
    assert train.label_available_time.max()<val.decision_time.min()
    assert val.label_available_time.max()<test.decision_time.min()
    d.loc[d.signal_date.ge(test.signal_date.min()),'net_main']=-.99
    train2,val2,_=chronological_split(d,'2025-08')
    pd.testing.assert_frame_equal(train,train2);pd.testing.assert_frame_equal(val,val2)


def test_validation_and_test_are_not_used_for_preprocessing_or_fitting():
    train=pd.DataFrame({'x':np.arange(30.),'net_main':np.linspace(-.01,.01,30)})
    m=fit_model(train,['x']);before=predict(m,pd.DataFrame({'x':[2.,3.],'future_label':[99.,-99.]}))
    after=predict(m,pd.DataFrame({'x':[2.,3.],'future_label':[-9999.,9999.]}))
    np.testing.assert_array_equal(before,after)
    assert m['hi']['x']<30 and m['lo']['x']>=0


def test_gate_uses_validation_returns_and_includes_cash_and_no_filter():
    v=pd.DataFrame({'selected':True,'amount_gate':1,'gross_1442':[.02]*15+[-.03]*15,
                    'score':[.01]*15+[-.01]*15})
    threshold,grid=choose_gate(v)
    assert threshold==-.005 and {r['threshold'] for r in grid}=={-999.,-.005,0.,.005,999.}
    assert int(v.score.ge(threshold).sum())==15
    assert choose_gate(v.iloc[:8])[0]==-999
    all_negative=v.assign(gross_1442=-.03)
    assert choose_gate(all_negative)[0]==999


def test_cash_fees_and_peak_relative_drawdown():
    assert settle(100000.,0.)<100000.
    assert settle(100000.,.1,exposure=0.)==100000.
    assert abs(drawdown([100,110,88])-20)<1e-10
    gain,dd=path_stats([np.nan,.1],[False,False]);assert gain==0 and dd==0
    gain,dd=path_stats([np.nan],[True]);assert np.isnan(gain) and np.isnan(dd)


def test_held_path_drawdown_is_not_hidden_by_exit_recovery():
    d=pd.DataFrame([{'signal_date':'2025-01-06','trade':True,'gross_1442':0.,
                     'entry_1442':100.,'valuation_path_complete':True}])
    marks={'2025-01-06':pd.DataFrame({'close':[100.,80.,100.]})}
    a=account(d,marks)
    assert drawdown(a.intratrade_path.iloc[0])>20
    assert drawdown([100000.,a.nav_after.iloc[0]])<1
