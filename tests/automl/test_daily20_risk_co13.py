import numpy as np
import pandas as pd
import pytest
from scripts.experiment_daily20_risk_co13 import previous20,matured,calibration_rows,predict_bundle


def fixture_rows():
    dates=pd.bdate_range('2025-01-01',periods=45).strftime('%Y-%m-%d').tolist()
    labels=pd.DataFrame({'sample_id':range(44),'signal_date':dates[:-1],'symbol6':'000001',
        'label_known':True,'label_available_time':[x+' 09:40:00' for x in dates[1:]],'severe_loss':False})
    raw=labels[['sample_id','signal_date','symbol6']].assign(raw_probability=.2,raw_severity=.04)
    return dates,labels,raw


def test_window_is_exactly_prior_twenty():
    dates,_,_=fixture_rows()
    assert previous20(dates,dates[40])==dates[20:40]
    with pytest.raises(ValueError):previous20(dates,dates[19])


def test_yesterday_matures_this_morning_but_future_is_excluded():
    dates,labels,_=fixture_rows();z=matured(labels,dates[40])
    assert z.signal_date.max()==dates[39]
    labels.loc[39,'label_available_time']=dates[40]+' 14:41:00'
    assert matured(labels,dates[40]).signal_date.max()==dates[38]


def test_calibration_unchanged_when_future_targets_change():
    dates,labels,raw=fixture_rows();raw=raw.iloc[20:40]
    a=calibration_rows(raw,labels,dates[40],dates)
    labels.loc[40:,'severe_loss']=True
    pd.testing.assert_frame_equal(a,calibration_rows(raw,labels,dates[40],dates))


def test_current_day_scores_cannot_enter_calibration():
    dates,labels,raw=fixture_rows()
    with pytest.raises(ValueError):calibration_rows(raw.iloc[21:41],labels,dates[40],dates)


def test_missing_calibration_date_rejected():
    dates,labels,raw=fixture_rows();labels.loc[39,'label_known']=False
    with pytest.raises(ValueError):calibration_rows(raw.iloc[20:40],labels,dates[40],dates)


def test_prediction_rejects_late_feature_before_model_use():
    from scripts.experiment_daily20_risk_co13 import FEATURES
    m={'features':FEATURES,'day':'2025-03-01'}
    d=pd.DataFrame({'signal_date':['2025-03-01'],'feature_cutoff':['2025-03-01 14:41:00']})
    with pytest.raises(ValueError,match='timestamp'):predict_bundle(m,d)
