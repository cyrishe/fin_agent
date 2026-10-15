import numpy as np
import pandas as pd
from scripts.experiment_b1_open_drop_risk_co9 import (
    labels,history_features,training_subset,choose_threshold,fit_model,score,FEATURES)


def test_label_boundaries_and_middle():
    down,up=labels([-.020000000000000018,-.019,0,.019,.02,np.nan])
    assert down.tolist()==[True,False,False,False,False,False]
    assert up.tolist()==[False,False,False,False,True,False]


def test_future_daily_changes_do_not_change_factors():
    days=pd.bdate_range('2025-01-01',periods=13).strftime('%Y-%m-%d').tolist()
    d=pd.DataFrame(dict(trade_date=days,preclose=100.,close=np.arange(100.,113.)))
    a=history_features(d,days[10],days)
    d.loc[d.trade_date.ge(days[10]),['close','preclose']]=999999.
    assert history_features(d,days[10],days)==a


def test_training_extremes_only_validation_remains_full():
    d=pd.DataFrame(dict(selected=True,open_return=[-.03,0,.03],corporate_action=False,
                        big_drop=[True,False,False],big_rise=[False,False,True]))
    assert len(training_subset(d,'extreme_logistic'))==2
    assert len(training_subset(d,'all_logistic'))==3
    assert len(d)==3


def validation():
    return pd.DataFrame(dict(baseline_trade=True,open_return=[-.03]*5+[.01]*15,
        corporate_action=False,big_drop=[True]*5+[False]*15,risk_score=[.9]*5+[.1]*15))


def test_threshold_preserves_middle_and_targets_recall():
    v=validation();t,grid,reason=choose_threshold(v)
    assert int(v.risk_score.lt(t).sum())==15
    assert int((v.big_drop&v.risk_score.ge(t)).sum())==5
    assert reason=='selected'


def test_insufficient_validation_does_not_invent_gate():
    v=validation();v.loc[:3,'big_drop']=False
    t,_,reason=choose_threshold(v)
    assert t==2 and reason=='insufficient_validation_events'


def test_unattainable_recall_respects_retention():
    v=validation();v['risk_score']=.5
    t,_,reason=choose_threshold(v)
    assert t>.5 and reason=='recall_target_unmet'


def test_preprocessing_is_train_only_and_deterministic():
    rng=np.random.default_rng(1)
    t=pd.DataFrame(rng.normal(size=(30,len(FEATURES))),columns=FEATURES)
    t['big_drop']=[True]*10+[False]*20
    m=fit_model(t,'all_logistic');z=t.copy();p=score(m,z)
    z.loc[0,FEATURES]=1e9
    p2=score(m,z)
    np.testing.assert_array_equal(p[1:],p2[1:])
    np.testing.assert_array_equal(p,score(fit_model(t,'all_logistic'),t))
