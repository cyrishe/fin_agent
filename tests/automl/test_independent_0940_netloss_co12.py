import numpy as np
import pandas as pd
import pytest
from scripts.build_independent_0940_netloss_co12 import valid_bars
from scripts.experiment_independent_0940_netloss_co12 import (
    FEATURES,fit_heads,raw_predict,fit_calibration,predict_bundle,
)
from scripts.report_independent_0940_netloss_co12 import policy_exposures
from tests.automl.test_independent_open_drop_co10 import test_current_and_future_daily_prices_cannot_change_current_features


@pytest.fixture(scope='module')
def example():
    rng=np.random.default_rng(12);n=1000
    d=pd.DataFrame(rng.normal(size=(n,len(FEATURES))),columns=FEATURES)
    d['signal_return']=.05;d['sample_id']=range(n)
    d['signal_date']=np.repeat(['2026-01-02','2026-01-05'],n//2)
    d['feature_cutoff']=d.signal_date+' 14:40:00'
    d['label_known']=True;d['severe_loss']=np.arange(n)%3==0
    d['net_0940']=np.where(d.severe_loss,-.04,-.005)
    d['severity']=(-d.net_0940).where(d.severe_loss)
    m,used=fit_heads(d)
    d['raw_probability'],d['raw_severity']=raw_predict(m,d)
    m.update(calibration=fit_calibration(d),month='2026-01')
    return m,d,used


def test_full_distribution_includes_small_losses(example):
    m,d,used=example
    assert len(used)==1000
    assert (used.net_0940.eq(-.005)).sum()==666
    assert used.loc[~used.severe_loss,'severity_weight'].isna().all()
    assert abs(used.probability_weight.mean()-1)<1e-12
    assert list(m['features'])==FEATURES


def test_predictions_ignore_future_labels_and_B1_identity(example):
    m,d,_=example;a=predict_bundle(m,d)
    changed=d.copy();changed['net_0940']=999;changed['severity']=999;changed['severe_loss']=False
    changed['entry_1442']=1e9;changed['exit_0940']=-1;changed['B1']=True
    pd.testing.assert_frame_equal(a,predict_bundle(m,changed))


def test_calibration_ignores_unknown_labels(example):
    _,d,_=example
    a=d.copy();a.loc[:99,'label_known']=False
    first=fit_calibration(a)
    a.loc[:99,'severity']=100;a.loc[:99,'severe_loss']=~a.loc[:99,'severe_loss']
    assert first==fit_calibration(a)


def test_inference_rejects_late_features_and_wrong_month(example):
    m,d,_=example
    late=d.copy();late.loc[0,'feature_cutoff']='2026-01-02 14:41:00'
    with pytest.raises(ValueError,match='cutoff'):predict_bundle(m,late)
    wrong=d.copy();wrong['signal_date']='2026-02-02';wrong['feature_cutoff']='2026-02-02 14:40:00'
    with pytest.raises(ValueError,match='month'):predict_bundle(m,wrong)


def test_tail_risk_identity_and_nonnegative_bounded_severity(example):
    m,d,_=example;p=predict_bundle(m,d)
    assert p.p_severe.between(0,1).all()
    assert p.conditional_severity.between(.02,1).all()
    np.testing.assert_allclose(p.tail_expected_loss,p.p_severe*p.conditional_severity)


def test_exact_price_validity_rejects_invalid_bars():
    b=pd.DataFrame({'open':[10.,10.,10.],'high':[11.,9.,11.],'low':[9.,9.,9.],
                    'close':[10.,10.,np.nan],'volume':[0.,10.,10.]})
    assert valid_bars(b).tolist()==[True,False,False]


def test_policies_boundaries_missing_and_original_gate():
    d=pd.DataFrame({'p_severe':[.1,.2,.4,np.nan,.99],
        'tail_expected_loss':[.0025,.01,.02,np.nan,.9],
        'baseline_trade':[True,True,True,True,False], 'old_exposure':[1,0,1,1,0]})
    x=policy_exposures(d)
    assert list(x['new_net_p20'])==[1,0,0,1,0]
    assert list(x['new_net_size20_40'])==[1,.5,0,1,0]
    assert list(x['tail_budget_005'])==[1,.5,.25,1,0]
    assert list(x['old_open_p20'])==[1,0,1,1,0]
