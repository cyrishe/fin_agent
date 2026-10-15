import pandas as pd
from scripts.experiment_limit_rebound_four_minutes_r24 import first_features, entry_facts, chosen, delay_same_stock


def bars():
    return pd.DataFrame({'time':pd.date_range('2026-01-06 09:31',periods=10,freq='min'),
        'open':[10.]*10,'high':[10.1]*10,'low':[9.9]*10,'close':[10.]*10,'volume':[100.]*10})


def test_four_features_do_not_read_fifth_bar_and_entry_starts_after_fourth():
    b=bars(); before=first_features(b,10.,11.,4)
    b.loc[4,['open','high','close']]=[10.5,10.8,10.7]
    assert first_features(b,10.,11.,4)==before
    assert first_features(b,10.,11.,5)['high5']==.08
    assert entry_facts(b,11.,4)['entry_time']=='09:35'
    assert entry_facts(b,11.,4)['entry']==10.5
    assert entry_facts(b,11.,5)['entry_time']=='09:36'
    assert entry_facts(b,11.,5)['entry']==10.


def test_earlier_rank2_wins_without_looking_at_later_rank1():
    f=pd.DataFrame({'symbol':['a','b'],'d0_date':['2026-01-05']*2,'d0_rank':[1,2],
        'eligible':[True]*2,'trade':[True]*2,'entry_time':['09:36','09:35'],'body5':[0.,0.]})
    assert chosen(f,{'atoms':[]}).symbol.tolist()==['b']
    f.loc[1,'body5']=-.08
    assert chosen(f,{'atoms':[]}).empty  # no later rank1 replacement after new gate


def test_delay_uses_same_stock_and_its_own_activation_time():
    b=bars(); b.loc[5:8,['open','high','low','close']]=11.
    f=pd.DataFrame([dict(symbol='a',d0_date='2026-01-05',entry_time='09:35',entry=10.,
        entry_whole_below=True,d1_cap=11.,eligible=True,fill=True,d1_close=11.,
        d2_close=11.,d2_preclose=11.,d2_up=False,d3_close=11.,d3_preclose=11.,d3_up=False,
        d4_close=11.,d4_preclose=11.,observed_3=True)])
    q=delay_same_stock(f,{('a','2026-01-05'):b})
    assert q.entry_time.tolist()==['09:40']
    b.loc[9,['open','high','low','close']]=11.
    assert delay_same_stock(f,{('a','2026-01-05'):b}).empty
