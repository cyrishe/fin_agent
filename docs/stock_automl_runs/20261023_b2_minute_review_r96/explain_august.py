import sys,json
from pathlib import Path
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_minute_review_r96_20261023');sys.path.insert(0,str(R))
from scripts.replay_limit_rebound_cash_r23 import replay
cal=json.loads((R/'calendar.json').read_text());data={};gates={}
for name,path in [('base',R),('strict',R/'rejected_strict_extrema')]:
 s=pd.read_pickle(path/'signals_exit.pkl');data[name]=s;gates[name]=json.loads((path/'overall.json').read_text())['gate']
a=data['base'];b=data['strict'];common=a[a.d0_date.ge('2021-04-01')&a.d0_date.lt('2022-01-01')].merge(b,on=['symbol','d0_date'],suffixes=('_base','_strict'))
price_match=bool(np.allclose(common.entry_base,common.entry_strict)&np.allclose(common.sell_price_base,common.sell_price_strict))
q=b[b.d0_date.str.startswith('2021-08')].copy();v,_,_,_,bad=replay(q.assign(d2_close=q.sell_price),cal,start='2021-08-01',end=q.d2_date.max(),initial=1e6,fee_rate=.001);assert not bad
j=a[a.d0_date.str.startswith('2021-07')].merge(b[b.d0_date.str.startswith('2021-07')][['symbol','d0_date']],on=['symbol','d0_date'],how='left',indicator=True);removed=j[j['_merge'].eq('left_only')]
rec=dict(august_base_gate=gates['base']['2021-08'],august_strict_gate=gates['strict']['2021-08'],august_strict_return=v['total_return'],common_shadow_entry_exit_prices_equal=price_match,july_removed=removed[['symbol','d0_date','d1_date','entry','d2_close','extrema_proxy_gap_pct']].to_dict('records'))
(O/'august_explanation.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2,default=str));print(json.dumps(rec,ensure_ascii=False,indent=2),flush=True)
