import pandas as pd
from scripts.rank_gate_daily20_risk_co13 import risk_ranks


def test_ranking_is_by_day_and_keeps_boundary_ties():
    d=pd.DataFrame({'signal_date':['2025-01-01']*4+['2025-01-02'],
        'symbol6':['000001','000002','000003','000004','000001'],'p_severe':[.9,.8,.8,.1,.2]})
    z=risk_ranks(d)
    assert z.risk_rank_max.tolist()==[1.,3.,3.,4.,1.]
    assert z.loc[:3,'risk_rank_max'].le(2).sum()==1
    assert z.loc[:3,'risk_rank_max'].le(3).sum()==3


def test_future_target_and_row_order_cannot_change_ranks():
    d=pd.DataFrame({'signal_date':['2025-01-01']*3,'symbol6':['000001','000002','000003'],
        'p_severe':[.3,.2,.1],'future_return':[-.9,.9,0]})
    a=risk_ranks(d).set_index('symbol6').risk_rank_max
    d['future_return']=[.9,-.9,.9]
    b=risk_ranks(d.iloc[::-1]).set_index('symbol6').risk_rank_max
    pd.testing.assert_series_equal(a.sort_index(),b.sort_index())
