import pandas as pd
from scripts.experiment_limit_rebound_shape_top3_r20 import select


def sample():
    return pd.DataFrame([
      {'d0_date':'2026-01-05','symbol':'000001','d0_rank':1,'entry_time':'09:38','trade':True,'shape_pass':True},
      {'d0_date':'2026-01-05','symbol':'000002','d0_rank':2,'entry_time':'09:36','trade':True,'shape_pass':True},
      {'d0_date':'2026-01-05','symbol':'000003','d0_rank':3,'entry_time':'09:36','trade':True,'shape_pass':True},
      {'d0_date':'2026-01-06','symbol':'000004','d0_rank':1,'entry_time':'09:36','trade':True,'shape_pass':False},
      {'d0_date':'2026-01-06','symbol':'000005','d0_rank':2,'entry_time':'09:37','trade':True,'shape_pass':True},
      {'d0_date':'2026-01-06','symbol':'000006','d0_rank':3,'entry_time':'09:36','trade':False,'shape_pass':True}])


def test_earliest_minute_before_rank_and_same_minute_rank_priority():
    out=select(sample());assert out.symbol.tolist()==['000002','000005']
    assert not out.d0_date.duplicated().any()


def test_original_top1_never_falls_back():
    assert select(sample(),1,True).symbol.tolist()==['000001']


def test_unfiltered_policy_has_same_execution_timing():
    assert select(sample(),3,False).symbol.tolist()==['000002','000004']


def test_selection_does_not_use_outcomes_or_later_prices():
    a=sample();a['ret4']=[1,-1,2,-2,3,-3];a['label_3']=[1,0,1,0,1,0]
    b=a.copy();b['ret4']=-b.ret4;b['label_3']=1-b.label_3
    assert select(a).symbol.tolist()==select(b).symbol.tolist()
