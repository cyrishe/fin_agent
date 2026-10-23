from pathlib import Path
import json,numpy as np,pandas as pd
from scripts.benchmark_automl_1440_inference import db_connection,query
from scripts.replay_limit_rebound_cash_r23 import dump,sha
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
s=pd.read_pickle(R/'signals.pkl').copy()
cal=json.loads((R/'calendar.json').read_text());ix={d:i for i,d in enumerate(cal)}
s['d2_date']=s.d1_date.map(lambda d:cal[ix[d]+1])
prebuy=s[~np.isclose(s['close'],s.d1_preclose,atol=.011,rtol=0)]
held=s[~np.isclose(s.d1_close,s.d2_preclose,atol=.011,rtol=0)]
keys=[(r.symbol+('.SH' if r.symbol.startswith('6') else '.SZ'),r.d2_date) for r in held.itertuples()]
assert len(keys)==len(set(keys))
if keys:
 marks=','.join(['(%s,%s)']*len(keys))
 with db_connection() as c:
  a=query(c,f'SELECT stk_code,record_date,ex_date,div_cash_pre_tax,div_cash_after_tax,div_conversed_rate,div_bonus_rate,div_payout_date,div_progress FROM kcrp_stock_dividend WHERE (stk_code,ex_date) IN ({marks})',tuple(v for k in keys for v in k))
  c.rollback()
else:a=pd.DataFrame()
assert len(a)==len(keys),(keys,a.to_dict('records'))
s['d2_cash_dividend']=0.
for row in held.itertuples():
 ticker=row.symbol+('.SH' if row.symbol.startswith('6') else '.SZ')
 x=a[(a.stk_code==ticker)&(a.ex_date.astype(str)==row.d2_date)]
 assert len(x)==1,(ticker,row.d2_date)
 x=x.iloc[0]
 assert str(x.record_date)==row.d1_date and str(x.div_payout_date)==row.d2_date
 assert pd.isna(x.div_bonus_rate) and pd.isna(x.div_conversed_rate)
 amount=float(x.div_cash_pre_tax)
 assert np.isclose(float(row.d1_close)-float(row.d2_preclose),amount,atol=.011,rtol=0),(row,amount)
 s.loc[(s.symbol==row.symbol)&(s.d1_date==row.d1_date),'d2_cash_dividend']=amount
s.to_pickle(R/'signals_account_ready.pkl')
a.to_csv(R/'corporate_actions.csv',index=False,encoding='utf-8-sig')
prebuy[['symbol','d0_date','d1_date','close','d1_preclose']].to_csv(R/'prebuy_ex_adjustments.csv',index=False,encoding='utf-8-sig')
dump(R/'corporate_action_receipt.json',dict(prebuy_discontinuities=len(prebuy),held_discontinuities=len(held),matched_actions=len(a),signals_sha256=sha(R/'signals.pkl'),account_ready_sha256=sha(R/'signals_account_ready.pkl'),notes='D0-to-D1 gap occurs before the strategy buys. D1-to-D2 cash dividend is applied to the held shares, with selling fee charged only on share proceeds.'))
print('prebuy',len(prebuy),'held',len(held),flush=True)
