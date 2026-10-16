import importlib
from datetime import datetime
import pandas as pd
import pytest

from scripts.backfill_b1_history import CLOCKS, raw_summary, training_window, price


def minute_frame():
    return pd.DataFrame([dict(symbol='002636.SZ',time='2026-10-15 '+clock,open=10.,high=11.,low=9.,close=10.,volume=100.,turnover=1000.) for clock in CLOCKS])


def test_signal_uses_only_complete_prefix_and_future_does_not_change_it():
    b=minute_frame();signal,morning=raw_summary(b,'2026-10-15')
    later=pd.DataFrame([dict(symbol='002636.SZ',time='2026-10-15 15:00:00',open=1,high=1,low=1,close=1,volume=999999,turnover=999999)])
    signal2,morning2=raw_summary(pd.concat([b,later]),'2026-10-15')
    pd.testing.assert_frame_equal(signal,signal2);pd.testing.assert_frame_equal(morning,morning2)
    assert signal.iloc[0].minute_volume_shares==22000
    assert signal.iloc[0].amount==220000
    assert morning.iloc[0].second_high==11 and morning.iloc[0].close_40==10


def test_signal_gap_does_not_remove_observed_morning_exit():
    b=minute_frame().iloc[:-1]
    signal,morning=raw_summary(b,'2026-10-15')
    assert signal.empty
    assert morning.iloc[0].second_high==11 and morning.iloc[0].close_40==10
    b=b[b.time!='2026-10-15 09:31:00']
    _,morning=raw_summary(b,'2026-10-15')
    assert pd.isna(morning.iloc[0].second_high) and morning.iloc[0].close_40==10


def test_missing_exit_stays_unknown_and_invalid_price_cannot_be_a_signal():
    b=minute_frame();b.loc[b.time.str.endswith('09:40:00'),'close']=0
    signal,morning=raw_summary(b,'2026-10-15')
    assert signal.empty and morning.empty


def test_duplicate_or_wrong_date_rejected():
    b=minute_frame()
    with pytest.raises(ValueError):raw_summary(pd.concat([b,b.iloc[:1]]),'2026-10-15')
    with pytest.raises(ValueError):raw_summary(b,'2026-10-14')


def test_training_uses_exact_past20_and_only_mature_labels():
    days=pd.bdate_range('2026-08-01',periods=25).strftime('%Y-%m-%d').tolist()
    c=pd.DataFrame([dict(signal_date=day,next_date=days[i+1],second_high=11) for i,day in enumerate(days[:-1])])
    train,window=training_window(c,days,days[-1])
    assert window==days[-21:-1] and set(train.signal_date)==set(window)
    c.loc[c.signal_date==window[0],'next_date']='2027-01-01'
    with pytest.raises(ValueError):training_window(c,days,days[-1])


def test_database_price_precision_and_unknown():
    assert str(price(12.34567))=='12.3457'
    assert price(float('nan')) is None and price(None) is None
