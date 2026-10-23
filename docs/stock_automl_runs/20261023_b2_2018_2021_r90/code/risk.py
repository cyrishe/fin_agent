from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from scripts.replay_limit_rebound_cash_r23 import replay,dump,sha
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
spec=json.loads((ROOT/'risk_spec.json').read_text())
F=spec['features']
FEE=.001
sig=pd.read_pickle(ROOT/'signals_exit.pkl').sort_values('d1_date').copy()
gate=json.loads((ROOT/'overall.json').read_text())['gate']
sig['model_month']=sig.d0_date.str[:7]
sig['paused']=sig.model_month.map(lambda m:gate[m]['paused'])
sig['risk_target']=(sig.d2_open_floor|sig.d2_close_floor).astype(int)
sig['early_net_return']=(sig.sell_price*(1-FEE)+sig.d2_cash_dividend)/(sig.entry*(1+FEE))-1
active=sig[~sig.paused].copy()
# Historical shadow outcomes are available even when that month was paused.
# Train before 2019, then evaluate only actually allowed candidate trades.
train=sig[sig.d0_date.lt('2019-01-01')&sig.d2_date.lt('2019-01-01')].copy()
valid=active[active.d0_date.between('2019-01-01','2019-12-31')&active.d2_date.lt('2020-01-01')].copy()
test=active[active.d0_date.ge('2020-01-01')].copy()
assert len(train)>100 and train.risk_target.sum()>=5,(len(train),train.risk_target.sum())
def X(x):
 return x[F].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan)
pipe=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(C=.1,max_iter=2000,random_state=20261013))
pipe.fit(X(train),train.risk_target)
joblib.dump(dict(model=pipe,features=F,spec_sha256=sha(ROOT/'risk_spec.json'),training_d0=train.d0_date.tolist()),ROOT/'risk_model.joblib',compress=3)
model=joblib.load(ROOT/'risk_model.joblib')['model']
threshold=float(np.quantile(model.predict_proba(X(train))[:,1],.75))
pred=[]
for part,frame in [('train',train),('validation',valid),('test',test)]:
 f=frame.copy();f['split']=part
 f['risk_score']=model.predict_proba(X(f))[:,1]
 f['alert']=f.risk_score.gt(threshold)
 pred.append(f)
pred=pd.concat(pred,ignore_index=True)
pred.to_pickle(ROOT/'risk_predictions.pkl')
cal=json.loads((ROOT/'calendar.json').read_text())
summaries=[];accounts=[]
for part,g in pred.groupby('split',sort=False):
 alerts=g[g.alert]
 saved=-10000*alerts.loc[alerts.early_net_return.lt(0),'early_net_return'].sum()
 missed=10000*alerts.loc[alerts.early_net_return.gt(0),'early_net_return'].sum()
 summaries.append(dict(split=part,n=len(g),target=int(g.risk_target.sum()),alerts=len(alerts),target_caught=int(alerts.risk_target.sum()),profitable_missed=int(alerts.early_net_return.gt(0).sum()),avoided_loss_1w=float(saved),missed_profit_1w=float(missed),net_1w=float(saved-missed),alert_win_rate=float(alerts.early_net_return.gt(0).mean()) if len(alerts) else None))
periods=[('2019','2019-01-01',active[active.d0_date.str.startswith('2019')]),
         ('2020','2020-01-01',active[active.d0_date.str.startswith('2020')]),
         ('2021','2021-01-01',active[active.d0_date.str.startswith('2021')]),
         ('2019-2021','2019-01-01',active[active.d0_date.ge('2019-01-01')]),
         ('2018-2021','2018-01-01',active[active.d0_date.ge('2018-01-01')])]
for label,start,scope in periods:
 flagged=set(zip(pred[pred.alert].symbol,pred[pred.alert].d0_date))
 for variant,chosen in [('baseline',scope),('risk_filter',scope[[((r.symbol,r.d0_date) not in flagged) or r.d0_date<'2019-01-01' for r in scope.itertuples()]])]:
  q=chosen.assign(d2_close=chosen.sell_price)
  final=max(scope.d2_date)
  result,_,nav,ledger,bad=replay(q,cal,start=start,end=final,initial=1_000_000.,fee_rate=FEE)
  assert not bad,(label,variant,bad)
  accounts.append(dict(period=label,variant=variant,trades=result['entries'],account_return=result['total_return'],max_drawdown=result['max_drawdown'],win_rate=result['closed_win_rate'],final_nav=result['final_nav']))
pd.DataFrame(summaries).to_csv(ROOT/'risk_split_effect.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(accounts).to_csv(ROOT/'risk_account_comparison.csv',index=False,encoding='utf-8-sig')
pred[['split','model_month','d0_date','d1_date','d2_date','symbol','risk_score','alert','risk_target','early_net_return','d2_open_floor','d2_close_floor','d2_oneprice_floor']].to_csv(ROOT/'risk_trade_predictions.csv',index=False,encoding='utf-8-sig')
dump(ROOT/'risk_audit.json',dict(model_sha256=sha(ROOT/'risk_model.joblib'),spec_sha256=sha(ROOT/'risk_spec.json'),training_n=len(train),training_target=int(train.risk_target.sum()),threshold=threshold,split_effect=summaries,production_changed=False))
print(pd.DataFrame(summaries).to_string(index=False),flush=True)
print(pd.DataFrame(accounts).to_string(index=False),flush=True)
