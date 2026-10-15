from types import SimpleNamespace
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_two_stage_r15 import window,early_facts,bad_labels
from scripts.report_limit_rebound_two_stage_r15 import pick,passes

def test_six_two_one_window():
 assert window('2025-09')=={'train':['2025-01','2025-06'],'valid':['2025-07','2025-08'],'test':'2025-09'}

def test_0940_features_do_not_read_later_path():
 times=pd.date_range('2025-01-02 09:31',periods=11,freq='min')
 bars=pd.DataFrame({'time':times,'open':10.,'high':10.1,'low':9.9,'close':10.,'volume':100.})
 r=SimpleNamespace(d1_open=10.,d1_preclose=10.,d1_cap=11.,d1_close=10.,d1_volume=24000.)
 a=early_facts(bars,r)
 bars.loc[10,['open','high','low','close','volume']]=[11.,20.,1.,20.,99999.]
 b=early_facts(bars,r)
 for key in ['early_valid','close0940','open_gap','early_return','early_pullback','early_volume','early_range','below0940']:
  assert a[key]==b[key]
 assert a['fill_proxy'] and not b['fill_proxy']

def test_execution_failure_never_promotes_next_stock():
 f=pd.DataFrame({'d0_date':['2025-01-02']*3,'d0_rank':[1,2,3],'symbol':['a','b','c'],
 'eligible':[True]*3,'reject':[False,False,False],'fill_proxy':[False,True,True],'clean_return':[True]*3})
 assert pick(f).empty
 f.loc[0,'reject']=True
 assert pick(f,True).symbol.tolist()==['b']

def test_no_filter_when_precision_is_no_better():
 s={'removed':10,'retention':.8,'precision':.2,'base_bad_rate':.2,'kept_return':.01,'base_return':0.}
 assert not passes(s,3)
 s['precision']=.3
 assert passes(s,3)

def test_bad_case_excludes_exact_five_percent_and_any_future_board():
 f=pd.DataFrame({'d2_up':[False,False,True], 'd3_up':[False]*3,'d4_up':[False]*3,
                 'd4_close':[9.5,9.49,9.0],'buy_price':[10.]*3})
 assert bad_labels(f).tolist()==[False,True,False]
