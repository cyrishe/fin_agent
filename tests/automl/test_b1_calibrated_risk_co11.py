import numpy as np
import pandas as pd
from scripts.experiment_b1_calibrated_risk_co11 import calibrate, probability, exposures, sized_account
from scripts.report_b1_market_trade_gate_co8 import account


def test_calibration_uses_full_distribution_and_removes_weighted_score_prior():
    # A constant inflated score must calibrate to the actual unweighted 10% event rate.
    d=pd.DataFrame({'sample_id':range(1000),'signal_date':['2026-01-01']*1000,
                    'risk_score':[.8]*1000,'big_drop':[True]*100+[False]*900,'label_known':True})
    m=calibrate(d)
    assert abs(probability(m,[.8])[0]-.1)<1e-3


def test_negative_direction_falls_back_to_observed_base_rate():
    d=pd.DataFrame({'sample_id':range(200),'signal_date':['2026-01-01']*200,
                    'risk_score':[.1]*100+[.9]*100,'big_drop':[True]*100+[False]*100,'label_known':True})
    m=calibrate(d)
    assert m['fallback']=='negative_slope'
    assert np.allclose(probability(m,[.01,.99]),.5)


def test_policy_missing_score_and_boundaries_preserve_original_gate():
    d=pd.DataFrame({'p_conditional':[np.nan,.2,.3,.4,.9], 'p_extreme':[np.nan,.9,.1,.4,.9],
        'p_global':[np.nan,.2,.3,.4,.9], 'risk_score':[np.nan,.2,.3,.4,.9],
        'q90':[.8]*5,'baseline_trade':[True,True,True,True,False]})
    x=exposures(d)
    assert x['position_20_40'].tolist()==[1,.5,.5,0,0]
    assert x['conditional_p30'].tolist()==[1,1,0,0,0]
    assert x['consensus_p30'].tolist()==[1,1,1,0,0]


def test_variable_account_matches_reference_constant_exposure_including_minimum_fees():
    d=pd.DataFrame({'signal_date':['2026-01-01','2026-01-02'], 'gross_1442':[-.2,.12],
        'entry_1442':[10.,10.],'valuation_path_complete':[True,True], 'trade':[True,True]})
    marks={s:pd.DataFrame({'close':[10.,8.,11.]}) for s in d.signal_date}
    for x in (.01,.5,1.):
        a=account(d,marks,exposure=x);b=sized_account(d.assign(exposure=x),marks)
        assert np.allclose(a.nav_after,b.nav_after,rtol=0,atol=1e-9)
        for u,v in zip(a.intratrade_path,b.intratrade_path):assert np.allclose(u,v)
