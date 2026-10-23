from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import replay as r
from scripts.replay_limit_rebound_cash_r23 import replay,dump
ROOT=r.ROOT
cal=json.loads((ROOT/'calendar.json').read_text())
sig=pd.read_pickle(ROOT/'signals_exit.pkl').sort_values('d1_date').copy()
source=json.loads((ROOT/'overall.json').read_text())
assert source['scope']['last_model_month']=='2021-12'
sig['model_month']=sig.d0_date.str[:7]
sig['paused']=sig.model_month.map(lambda m:source['gate'][m]['paused'])
assert sig.paused.notna().all()
primary=sig[sig.d0_date.ge('2018-01-01')].copy()
assert primary.d0_date.str[:4].isin(['2018','2019','2020','2021']).all()
end=max(primary.d2_date)
scenarios={}
annual=[];calendar=[]
for name,frame in [('shadow_close',primary),('V2_pause_close',primary[~primary.paused].copy()),('pause_7pct',primary[~primary.paused].assign(d2_close=lambda x:x.sell_price))]:
 total,_,nav,ledger,bad=replay(frame,cal,start='2018-01-01',end=end,initial=r.INITIAL,fee_rate=r.FEE)
 assert not bad and total['entries']==len(frame) and total['pending']==0,(name,bad,total)
 scenarios[name]=total
 nav.to_pickle(ROOT/f'primary_{name}_nav.pkl')
 ledger.to_pickle(ROOT/f'primary_{name}_ledger.pkl')
 for year,g in nav.groupby(nav.date.str[:4],sort=True):
  if year=='2022':continue
  prior=r.INITIAL if g.index[0]==0 else float(nav.loc[g.index[0]-1,'nav'])
  bought=ledger[ledger.entry_date.str[:4].eq(year)]
  sold=ledger[ledger.exit_date.fillna('').str[:4].eq(year)]
  calendar.append(dict(year=year,scenario=name,trades=len(bought),sold=len(sold),opening_nav=prior,closing_nav=float(g.nav.iloc[-1]),calendar_return=float(g.nav.iloc[-1]/prior-1),sold_win_rate=float(sold.return_.gt(0).mean()) if len(sold) else None))
 for year in ['2018','2019','2020','2021']:
  q=frame[frame.model_month.str[:4].eq(year)].copy()
  year_end=max(q.d2_date) if len(q) else str(pd.Period(year).end_time.date())
  outcome,_,_,_,excluded=replay(q,cal,start=year+'-01-01',end=year_end,initial=r.INITIAL,fee_rate=r.FEE)
  assert not excluded and outcome['entries']==len(q) and outcome['pending']==0,(name,year,excluded,outcome)
  annual.append(dict(year=year,scenario=name,model_trades=len(q),first_d0=q.d0_date.min() if len(q) else None,last_d2=year_end,return_=outcome['total_return'],max_drawdown=outcome['max_drawdown'],win_rate=outcome['closed_win_rate'],mean_trade_return=outcome['closed_mean_return'],final_nav=outcome['final_nav']))
dump(ROOT/'primary_overall.json',dict(scope='D0 model months 2018-01 through 2021-12, each scenario account reset to 1m on 2018-01-01; final D2 all settled',source_gate_uses_2017_shadow=True,scenarios=scenarios))
pd.DataFrame(annual).to_csv(ROOT/'primary_model_year.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(calendar).to_csv(ROOT/'primary_calendar_year.csv',index=False,encoding='utf-8-sig')
pd.read_csv(ROOT/'monthly_model.csv').query("month >= '2018-01' and month <= '2021-12'").to_csv(ROOT/'primary_monthly_model.csv',index=False,encoding='utf-8-sig')
print(json.dumps({k:dict(trades=v['entries'],return_=v['total_return'],win_rate=v['closed_win_rate'],max_drawdown=v['max_drawdown']) for k,v in scenarios.items()},ensure_ascii=False),flush=True)
print(pd.DataFrame(annual).to_string(index=False),flush=True)
