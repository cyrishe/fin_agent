import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_windows_r22 import policy_picks,window_data,choose_rule
from scripts.experiment_limit_rebound_shape_r19 import rules


def test_new_body_gate_is_common_and_never_replaces_first_choice():
    f=pd.DataFrame([{'symbol':'a','d0_date':'2025-04-01','d0_rank':1,'entry_time':'09:36','trade':True,'eligible':True,'high5':.07,'low5':-.01,'body5':-.08},
                    {'symbol':'b','d0_date':'2025-04-01','d0_rank':2,'entry_time':'09:36','trade':True,'eligible':True,'high5':.08,'low5':0,'body5':.01}])
    for r in [rules()[0],next(r for r in rules() if r['id']=='r005'),next(r for r in rules() if r['id']=='r051')]:
        assert policy_picks(f,r,False).symbol.tolist()==['a']
        assert policy_picks(f,r,True).empty


def test_fourth_future_trading_day_must_be_before_next_partition():
    cal=pd.bdate_range('2025-01-01','2025-04-10').strftime('%Y-%m-%d').tolist()
    rows=[]
    for d in ['2025-01-15','2025-02-24','2025-02-25','2025-02-28','2025-03-24','2025-03-28']:
        rows.append({'symbol':d,'d0_date':d,'d0_rank':1,'date_i':cal.index(d),'complete':True,'target_month':'2025-04'})
    f=pd.DataFrame(rows);v=f[f.d0_date.str.startswith('2025-03')]
    tr,va,_=window_data(f,v,'2025-04',2,cal)
    assert tr.d0_date.tolist()==['2025-01-15','2025-02-24']
    assert va.d0_date.tolist()==['2025-03-24']


def test_small_sample_does_not_force_a_filter():
    f=pd.DataFrame({'symbol':['a']*3,'d0_date':['2025-01-02','2025-01-06','2025-01-08'],
        'eligible':True,'trade':True,'observed_3':True,'label_3':[1,0,1],'entry_whole_below':True,
        'ret2':.01,'ret3':.01,'ret4':.01,'strategy':.01,'high5':.08,'low5':0,'body5':0,
        'pullback5':.01,'position5':.5,'below5':5,'whole5':5,'touch5':0,'shape':'平走'})
    result=choose_rule(f,f,1,rules())
    assert result['selected']['id']=='all' and result['eligible_rules']==0
