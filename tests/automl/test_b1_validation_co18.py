import pandas as pd
from scripts.research_b1_validation_co18 import windows,choose

def st(score,n=1,unknown=0,halves=(.01,0),counts=(1,0)):
 return dict(score=score,trades=n,unknown=unknown,half_trades=list(counts),half_net=list(halves))
def test_18_training_two_validation_no_overlap():
 cal=pd.bdate_range('2026-01-01',periods=35).strftime('%Y-%m-%d').tolist()
 train,val=windows(cal,cal[30]);assert train==cal[10:28] and val==cal[28:30]
 assert not set(train)&set(val) and max(val)<cal[30]
def test_one_known_trade_can_pass_but_not_two_day_gate():
 assert choose([st(.01),st(.02,n=0),st(.005)])==(0,True,False)
def test_both_days_must_profit_and_have_trades():
 assert choose([st(.01,n=2,halves=(.01,.01),counts=(1,1)),st(-.01),st(.03,unknown=1)])==(0,True,True)
 assert choose([st(-.01),st(-.02),st(-.03)])==(0,False,False)
 assert choose([st(.01,n=0),st(.01,n=0),st(.01,unknown=1)])==(0,False,False)
