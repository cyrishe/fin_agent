"""Frozen four/five rules: locked candidate, next minute open-close mean."""
import json
from pathlib import Path
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import replay
SRC=Path('outputs/stock_automl/b2_four_five_clock_20261016')
OUT=Path('outputs/stock_automl/b2_next_minute_mean_20261016')
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 picks=pd.read_pickle(SRC/'picks.pkl');raw=pd.read_pickle(SRC/'inputs/first11.pkl')
 raw['label']=pd.to_datetime(raw.time).dt.strftime('%H:%M');bars={(s,d):g.set_index('label') for (s,d),g in raw.groupby(['symbol','d0_date'])}
 cal=json.load(open(SRC/'inputs/calendar.json'));ss=[];mm=[];ll=[];decisions=[]
 for n,base in [(4,'four_original'),(5,'five_original')]:
  locked=picks[picks.variant.eq(base)].copy()
  for timing,cutoff in [('immediate_next','09:40'),('next_available','09:40'),('immediate_next','09:41'),('next_available','09:41')]:
   accepted=[]
   for r in locked.to_dict('records'):
    b=bars[(r['symbol'],r['d0_date'])];later=b[(b.index>r['entry_time'])&(b.index<=cutoff)]
    a=later.iloc[:1] if timing=='immediate_next' else later
    a=a[a.volume.gt(0)&a.open.lt(r['d1_cap']-.005)]
    reason='已成交' if len(a) else '下一分钟不可买或窗口结束'
    decisions.append(dict(bars=n,timing=timing,cutoff=cutoff,d0_date=r['d0_date'],d1_date=r['d1_date'],symbol=r['symbol'],activation_end=r['entry_time'],reason=reason))
    if len(a):
     v=a.iloc[0];assert v.name>r['entry_time'];r.update(entry_time=v.name,minute_open=float(v.open),minute_close=float(v.close),mean_price=float((v.open+v.close)/2),activation_end=r['entry_time']);accepted.append(r)
   for price in ['open','mean']:
    f=pd.DataFrame(accepted);f['entry']=f.minute_open if price=='open' else f.mean_price
    code=f'{n}K_{timing}_{price}_end{cutoff.replace(":","")}'
    s,m,d,l,e=replay(f,cal,end='2026-10-14',fee_rate=.001)
    known=f[f.observed_3.eq(True)];ss.append(dict(variant=code,locked=len(locked),skipped=len(locked)-len(f),three_day_known=len(known),three_day_hit=float(known.label_3.mean()),**s))
    mm.extend([dict(variant=code,**r) for r in m]);ll.append(l.assign(variant=code))
 pd.DataFrame(ss).to_csv(OUT/'summary.csv',index=False);pd.DataFrame(mm).to_csv(OUT/'monthly.csv',index=False);pd.concat(ll).to_pickle(OUT/'ledger.pkl');pd.DataFrame(decisions).to_csv(OUT/'decisions.csv',index=False)
 print(pd.DataFrame(ss)[['variant','locked','entries','skipped','total_return','max_drawdown','sharpe','three_day_hit']].to_string(index=False))
 print(pd.DataFrame(mm).query("month == '2026-10'")[['variant','return_']].to_string(index=False))
if __name__=='__main__':main()
