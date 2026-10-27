import numpy as np
import pandas as pd
from scripts.train_low_base_co26 import labels,splits
from scripts.evaluate_low_base_co26 import daily

def test_mature_labels_and_training_window_do_not_cross_test():
 d=pd.DataFrame({'date':['2024-06-30','2024-07-01','2024-12-29','2024-12-30','2025-01-02'],
 'month':['2024-06','2024-07','2024-12','2024-12','2025-01'],
 'label_end2':['2024-07-02','2024-07-03','2024-12-31','2025-01-02','2025-01-06'],
 'net2':[.01,.02,-.03,.2,.1]})
 train,test=splits(d,'2025-01',2)
 assert train.date.tolist()==['2024-07-01','2024-12-29']
 assert test.date.tolist()==['2025-01-02']

def test_middle_class_is_kept_at_boundaries():
 assert labels([-.021,-.02,0,.02,.021]).tolist()==[-1,0,0,0,1]

def test_unknown_outcome_never_becomes_zero_or_changes_ranking():
 s=pd.DataFrame({'date':['2025-01-02']*4,'stk_code':['a','b','c','d'],'score':[.5,.4,.3,.2],'net':[.01,np.nan,-.01,.03]})
 a=daily(s,'x',2).iloc[0]
 assert a.top3_symbols=='a,b,c' and a.top3_n_known==2 and np.isnan(a.top3_net)
 s.loc[0,'net']=-100
 b=daily(s,'x',2).iloc[0]
 assert b.top3_symbols==a.top3_symbols and b.pool_unknown==1
