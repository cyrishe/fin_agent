import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_body8_r21 import body_keep,account,select_policy
from scripts.experiment_limit_rebound_shape_top3_r20 import select


def row(day='2025-01-30',symbol='x',prices=(12,8,10,14)):
    r={'symbol':symbol,'d0_date':'2025-01-29','d1_date':day,'entry':10.,'close':10.,
       'd1_preclose':10.,'d2_up':False,'d3_up':False}
    for k,p in enumerate(prices,1):
        r[f'd{k}_close']=float(p)
        if k>1:r[f'd{k}_preclose']=float(prices[k-2])
    return r


def test_strict_body_cut_never_replaces_original_choice():
    assert body_keep(pd.Series([-.081,-.08,-.079,np.nan])).tolist()==[False,False,True,False]
    f=pd.DataFrame([{'symbol':'a','d0_date':'2025-01-30','d0_rank':1,'entry_time':'09:36','trade':True,'body5':-.08},
                    {'symbol':'b','d0_date':'2025-01-30','d0_rank':2,'entry_time':'09:36','trade':True,'body5':0}])
    f['body_keep']=body_keep(f.body5);f['shape_pass']=True
    assert select_policy(f,'old').symbol.tolist()==['a']
    assert select_policy(f,'body8').empty
    f.loc[0,'body5']=-.079;f['body_keep']=body_keep(f.body5)
    assert select_policy(f,'body8').symbol.tolist()==['a']


def test_original_shape_fallback_is_preserved_but_new_gate_stops_day():
    f=pd.DataFrame([{'symbol':'a','d0_date':'2025-01-30','d0_rank':1,'entry_time':'09:38','trade':True,'shape_pass':True,'body_keep':True},
                    {'symbol':'b','d0_date':'2025-01-30','d0_rank':2,'entry_time':'09:36','trade':True,'shape_pass':True,'body_keep':False}])
    assert select_policy(f,'old').symbol.tolist()==['b']
    assert select_policy(f,'body8').empty
    f.loc[1,'body_keep']=True
    assert select_policy(f,'body8').symbol.tolist()==['b']


def test_cross_month_mark_to_market_and_pending_positions():
    calendar=['2025-01-30','2025-02-03','2025-02-04','2025-02-05']
    f=pd.DataFrame([row()])
    s,m,n,l,e=account(f,'D4',calendar,calendar[0],calendar[-1])
    assert np.allclose([x['nav'] for x in n],[1.05,.95,1,1.1])
    assert np.isclose(s['max_drawdown'],1-.95/1.05)
    assert np.isclose(m[0]['账户月收益'],.05)
    assert np.isclose(m[1]['账户月收益'],1.1/1.05-1)
    assert m[1]['信号笔数']==0 and m[1]['账户月收益']!=0
    pending=account(f,'D4',calendar,calendar[0],calendar[1])[0]
    assert pending['pending']==1 and pending['closed']==0 and pending['excluded']==0
    assert np.isclose(pending['total_return'],-.05)


def test_overlapping_lots_cash_and_close_proceeds():
    calendar=['2025-01-30','2025-02-03','2025-02-04','2025-02-05','2025-02-06']
    f=pd.DataFrame([row(d,str(i),(10,10,10,10)) for i,d in enumerate(calendar)])
    s,m,n,l,e=account(f,'D4',calendar,calendar[0],calendar[-1])
    assert s['max_positions']==4 and s['entries']==5 and s['pending']==3
    assert all(np.isclose(t['cost'],.25) for t in l)
    assert all(x['cash']>=0 and np.isclose(x['nav'],1) for x in n)


def test_historical_reference_break_is_excluded_without_replacement():
    calendar=['2025-01-30','2025-02-03','2025-02-04','2025-02-05']
    r=row();r['d3_preclose']=5.
    f=pd.DataFrame([r])
    assert account(f,'D2',calendar,calendar[0],calendar[-1])[0]['entries']==1
    s,_,_,_,excluded=account(f,'D4',calendar,calendar[0],calendar[-1])
    assert s['entries']==0 and s['excluded']==1 and s['total_return']==0
    assert excluded[0]['reason']=='持仓参考价不连续'
