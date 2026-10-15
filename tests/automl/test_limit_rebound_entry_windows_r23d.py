import pandas as pd
from scripts.analyze_limit_rebound_entry_windows_r23d import facts


def bars():
    # End labels09:31..09:40, all prints below cap before test mutations.
    return pd.DataFrame(dict(time=pd.date_range('2026-01-06 09:31',periods=10,freq='min'),
        open=[9.]*10,high=[9.5]*10,low=[8.5]*10,close=[9.]*10,volume=[1000.]*10))


def test_mixed_entry_does_not_count_as_full_minute():
    b=bars();b.loc[5,'high']=10.
    f=facts(b,10.,'09:36',100.)
    assert f['whole_prefix']==0 and f['whole_minutes']==4 and f['below_minutes']==5
    assert f['first_touch']=='09:35–09:36' and f['pattern']=='混｜下｜下｜下｜下'
    assert f['delayed_time']=='09:37' and f['entry_share_ratio']==.1


def test_late_entry_clips_remaining_window_and_never_counts_future():
    b=bars();b.loc[5:8,['open','high','low','close']]=10.
    f=facts(b,10.,'09:40',100.)
    assert f['window_remaining']==f['whole_prefix']==f['whole_minutes']==f['below_minutes']==1
    assert f['delayed_index'] is None and f['first_touch']=='至09:40未触'


def test_no_volume_is_not_a_whole_or_buyable_minute():
    b=bars();b.loc[6,'volume']=0.
    f=facts(b,10.,'09:36',100.)
    assert f['whole_prefix']==1 and f['whole_minutes']==4 and f['pattern']=='下｜无｜下｜下｜下'
    assert f['delayed_time']=='09:38'
