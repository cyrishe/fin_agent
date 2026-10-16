"""Frozen-rule clock audit; no fitting or production writes."""
import json
from pathlib import Path
import pandas as pd
import numpy as np
from scripts.experiment_limit_rebound_four_minutes_r24 import chosen,reprice,R20,OUT as R24
from scripts.replay_limit_rebound_cash_r23 import replay,BASE,sha
OUT=Path('outputs/stock_automl/b2_four_five_clock_20261016')

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 f5=pd.read_pickle(R20/'enriched_top3.pkl');f4=pd.read_pickle(R24/'four_all_top3.pkl')
 frames={5:f5[f5.part.eq('test')&f5.d1_date.between('2026-01-01','2026-10-14')].copy(),4:f4[f4.part.eq('test')&f4.d1_date.between('2026-01-01','2026-10-14')].copy()}
 rules={5:{r['month']:r['selected'] for r in json.load(open(R20/'rules_frozen.json'))},4:{r['month']:r['selected'] for r in json.load(open(R24/'four_rules_frozen.json'))}}
 bars={};sources=[]
 for day,g in frames[5][frames[5].audit_ok.eq(True)].groupby('d1_date'):
  path=R20/'minute_days'/f'{day}_raw.pkl';raw=pd.read_pickle(path);sources.append({'path':str(path),'sha256':sha(path)})
  for r in g.itertuples():
   b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time').iloc[:11].copy()
   b['clock']=pd.to_datetime(b.time).dt.strftime('%H:%M');assert b.clock.tolist()==[f'09:{i:02}' for i in range(31,42)]
   bars[(r.symbol,r.d0_date)]=b
 def pick(n,start,end):
  f=frames[n].copy()
  for i,r in f.iterrows():
   b=bars.get((r.symbol,r.d0_date));a=None if b is None else b[b.clock.between(start,end)&b.volume.gt(0)&b.open.lt(r.d1_cap-.005)]
   ok=a is not None and len(a)>0
   f.loc[i,'fill']=ok;f.loc[i,'entry']=float(a.iloc[0].open) if ok else np.nan
   f.loc[i,'entry_time']=a.iloc[0].clock if ok else None
  f=reprice(f)
  return pd.concat([chosen(g,rules[n][month]) for month,g in f.groupby('model_month')],ignore_index=True)
 def delay(f,last):
  rows=[]
  for r in f.to_dict('records'):
   b=bars[(r['symbol'],r['d0_date'])];a=b[b.clock.gt(r['entry_time'])&b.clock.le(last)&b.volume.gt(0)&b.open.lt(r['d1_cap']-.005)]
   if len(a):r.update(entry=float(a.iloc[0].open),entry_time=a.iloc[0].clock);rows.append(r)
  return reprice(pd.DataFrame(rows,columns=f.columns))
 five=pick(5,'09:36','09:40');four=pick(4,'09:35','09:40')
 variants={'five_original':five,'four_original':four,'five_lock_delay':delay(five,'09:40'),'four_B2':delay(four,'09:40'),
 'five_scan_0936_0940':pick(5,'09:37','09:41'),'four_scan_0935_0940':pick(4,'09:36','09:41'),
 'four_scan_0936_0940':pick(4,'09:37','09:41'),
 'five_lock_delay_to0940':delay(five,'09:41'),'four_lock_delay_to0940':delay(four,'09:41')}
 old=pd.read_pickle(R24/'policy_picks.pkl');checks=[]
 for code,prior in [('five_original','five'),('four_original','four_fit'),('five_lock_delay','five_delay'),('four_B2','four_fit_delay')]:
  a=variants[code].sort_values('d0_date');b=old[old.policy.eq(prior)&old.d1_date.between('2026-01-01','2026-10-14')].sort_values('d0_date')
  assert a[['d0_date','symbol','entry_time']].to_records(index=False).tolist()==b[['d0_date','symbol','entry_time']].to_records(index=False).tolist(),code
  assert np.allclose(a.entry,b.entry,atol=0,rtol=0);checks.append({'variant':code,'exact_reproduced':len(a)})
 cal=json.load(open(BASE/'calendar.json'));summaries=[];months=[];ledgers=[];navs=[];picks=[]
 for code,f in variants.items():
  s,m,d,l,e=replay(f,cal,end='2026-10-14',fee_rate=.001)
  known=f[f.observed_3.eq(True)]
  summaries.append({'variant':code,**s,'three_day_known':len(known),'three_day_hit':float(known.label_3.mean())})
  months += [{'variant':code,**r} for r in m];ledgers.append(l.assign(variant=code));navs.append(d.assign(variant=code));picks.append(f.assign(variant=code))
 pd.DataFrame(summaries).to_csv(OUT/'summary.csv',index=False);pd.DataFrame(months).to_csv(OUT/'monthly.csv',index=False)
 pd.concat(ledgers).to_pickle(OUT/'ledger.pkl');pd.concat(navs).to_pickle(OUT/'nav.pkl');pd.concat(picks).to_pickle(OUT/'picks.pkl')
 (OUT/'audit.json').write_text(json.dumps({'reproduction':checks,'minute_sources':sources,'rules_sources':{str(p):sha(p) for p in [R20/'rules_frozen.json',R24/'four_rules_frozen.json']},'fee_each_side':.001,'no_retraining':True,'minute_labels':'end; open clock = label - 1 minute','limitation':'Minute open/volume are retrospective fill proxies, not tick-level executable quotes; original audit eligibility retained; all variants are historical sensitivity tests.'},ensure_ascii=False,indent=2))
 print(pd.DataFrame(summaries)[['variant','entries','total_return','max_drawdown','sharpe','three_day_hit','closed_win_rate']].to_string(index=False))
 print(pd.DataFrame(months).query("month == '2026-10'")[['variant','entries','return_','drawdown']].to_string(index=False))
if __name__=='__main__':main()
