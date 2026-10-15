import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import replay,sharpe


CAL=['2026-01-05','2026-01-06','2026-01-07','2026-01-08','2026-01-09']


def row(day,close1,close2):
    return dict(symbol='same-stock',d1_date=day,entry=10.,close=10.,d1_preclose=10.,
        d1_close=close1,d2_preclose=close1,d2_close=close2)


def test_rotation_uses_only_morning_cash_and_reinvests_profits():
    f=pd.DataFrame([row(CAL[0],10,12),row(CAL[1],10,8),row(CAL[2],10,10),row(CAL[4],11,np.nan)])
    s,m,n,l,e=replay(f,CAL,initial=100.)
    # Day 2 uses the remaining 50, never the 60 returned at that day's close.
    assert l.cost.tolist()==[50.,50.,60.,50.]
    assert l.carried.tolist()==[0,1,1,0]
    # Repeated identical stock remains separate lots; day 3 has 60 prior-sale cash.
    assert n.nav.tolist()==[100.,110.,100.,100.,105.]
    assert n.cash.tolist()==[50.,60.,40.,100.,50.]
    assert s['pending']==1 and s['closed']==3 and s['max_positions']==2
    assert l.exit_date.iloc[-1] is None and s['unrealized']==5.


def test_losses_reduce_cash_ticket_no_fixed_minimum():
    f=pd.DataFrame([row(CAL[0],8,8),row(CAL[1],9,9),row(CAL[2],10,10)])
    s,m,n,l,e=replay(f,CAL,initial=100.)
    assert l.cost.tolist()==[50.,50.,40.]
    assert s['cash_skips']==0 and s['final_nav']==85.


def test_sharpe_includes_idle_days_and_initial_anchor():
    f=pd.DataFrame([row(CAL[0],11,12)])
    s,m,n,l,e=replay(f,CAL,initial=100.)
    expected=np.array([.05,110/105-1,0,0,0])
    np.testing.assert_allclose(n.return_,expected,atol=1e-14)
    assert np.isclose(s['sharpe'],np.sqrt(252)*expected.mean()/expected.std(ddof=1))
    assert sharpe([0,0,0]) is None


def test_cross_month_marked_profit_and_not_exit_month_only():
    cal=['2026-01-30','2026-02-02']
    s,m,n,l,e=replay(pd.DataFrame([row(cal[0],11,12)]),cal,initial=100.)
    assert [x['profit'] for x in m]==[5.,5.]
    assert np.isclose(np.prod([1+x['return_'] for x in m]),1.1)


def test_fees_both_sides_and_cash_budget_includes_buy_fee():
    f=pd.DataFrame([row(CAL[0],10,10),row(CAL[1],10,10),row(CAL[2],10,np.nan)])
    s,m,n,l,e=replay(f,CAL[:3],initial=100.,fee_rate=.001)
    q=50/1.001
    np.testing.assert_allclose(l.cost,[50.,50.,q*.999],rtol=1e-14)
    np.testing.assert_allclose(l.buy_fee,l.buy_amount*.001,rtol=1e-12)
    np.testing.assert_allclose(l.sell_fee.dropna(),l.sell_amount.dropna()*.001)
    assert l.sell_fee.iloc[-1] is None or pd.isna(l.sell_fee.iloc[-1])
    assert n.cash.min()>=0 and s['pending']==1
    # Flat prices: total loss exactly equals actually paid fees, no future sell fee.
    assert np.isclose(s['total_profit'],-s['total_fees'],atol=1e-12)
