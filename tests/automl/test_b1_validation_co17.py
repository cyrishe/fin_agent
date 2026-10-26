import pandas as pd
from scripts.research_b1_validation_co17 import windows,choose

def test_train_validate_test_dates_do_not_overlap():
 cal=pd.bdate_range('2026-01-01',periods=40).strftime('%Y-%m-%d').tolist()
 train,val=windows(cal,cal[35]);assert train==cal[5:25] and val==cal[25:35]
 assert not set(train)&set(val) and max(val)<cal[35]

def stat(score,n=3,unknown=0,halves=(.01,.02)):
 return dict(score=score,trades=n,unknown=unknown,half_trades=[1,2],half_net=list(halves))

def test_selection_uses_validation_profit_with_sample_floor():
 assert choose([stat(.001),stat(.02,2),stat(.002)])==(2,True,True)
 assert choose([stat(.001),stat(.001),stat(.0)])==(0,True,True)

def test_unreliable_validation_is_not_called_reliable():
 assert choose([stat(-.01),stat(-.02),stat(-.03)])==(0,False,False)
 assert choose([stat(.1,unknown=1),stat(.2,2),stat(.0,2)])==(0,False,False)
 assert choose([stat(.01,halves=(-.01,.1)),stat(.0),stat(-.01)])==(0,True,False)

def test_next_day_outcomes_cannot_enter_prediction_features():
 from scripts.run_automl_four_class_pipeline import FEATURES,select_inference_candidates
 row=dict(signal_date='2026-02-02',next_date='2026-02-03',symbol6='000001',name='样本',signal_price=10.,second_high=11.,close_40=10.5)
 row.update({f:1. for f in FEATURES})
 original=pd.DataFrame([row]);changed=original.copy()
 changed['second_high']=-1000.;changed['close_40']=100000.
 pd.testing.assert_frame_equal(select_inference_candidates(original,row['signal_date']),select_inference_candidates(changed,row['signal_date']))
