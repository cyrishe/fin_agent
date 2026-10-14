import numpy as np
import pandas as pd

from scripts.analyze_limit_rebound_single_split_r13 import condition, max_drawdown, metrics, thresholds


def test_additive_drawdown_includes_initial_zero_and_recovers():
    assert np.isclose(max_drawdown([-.1,.2,-.05,-.2]),.25)
    assert np.isclose(max_drawdown([-.1,.2]),.1)
    assert max_drawdown([])==0


def test_zero_trade_months_count_without_inventing_per_trade_return():
    f=pd.DataFrame({'d0_date':['2025-01-10','2025-01-11'],'symbol':['a','b'],
                    'month':['2025-01','2025-01'],'r':[.1,-.04]})
    m=metrics(f,'r',['2025-01','2025-02'])
    assert np.isclose(m['monthly_total_mean'],.03)
    assert np.isclose(m['mean'],.03)
    assert np.isclose(m['mdd'],.04)
    assert m['monthly_win_rate']==.5


def test_split_boundaries_exclude_missing_and_zero_cut_is_preserved():
    f=pd.DataFrame({'d1_gap':[-.02,0.,.02,np.nan]})
    assert condition(f,'d1_gap',0.,'>').tolist()==[False,False,True,False]
    assert condition(f,'d1_gap',0.,'<=').tolist()==[True,True,False,False]
    assert 0. in thresholds(f,'d1_gap')
