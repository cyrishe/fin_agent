"""Sensitivity to ex-post full-day D1 audit on the frozen Top3 list."""
from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd
from scripts import experiment_b2_full_2024_r34 as r34
from independent_r91 import ROOT, OUT, rows, bars, rules, four_features, accepts

raw_root = Path('/data/cyris/tushare_market_data')
r34.raw_minute_path = lambda day: raw_root/day[:4]/f'{day.replace("-", "")}.csv.gz'
original = pd.read_pickle(ROOT/'signals.pkl').set_index('d0_date')
bad = rows[rows.ordinary & ~rows.audit_ok.eq(True)].copy()
affected_days = sorted(bad.d0_date.unique())
candidate_audit = []
changed = []


def prefix_valid(b, cap):
    if b is None or len(b) < 10: return False
    v = b.iloc[:10]
    if pd.to_datetime(v.time).dt.strftime('%H:%M').tolist() != [f'09:{i:02}' for i in range(31,41)]: return False
    prices = v[['open','high','low','close','volume']].to_numpy(float)
    if not np.isfinite(prices).all() or (prices[:,:4] <= 0).any() or (prices[:,4] < 0).any(): return False
    if (v.high < v[['open','low','close']].max(axis=1)-.005).any(): return False
    if (v.low > v[['open','high','close']].min(axis=1)+.005).any(): return False
    if (v.high > cap+.011).any(): return False
    return True


by_d1 = {d: r34.tushare_bars(d,set(g.symbol)) for d,g in bad.groupby('d1_date')}
for r in bad.itertuples():
    b = by_d1[r.d1_date].get(r.symbol)
    okay = prefix_valid(b, float(r.d1_cap))
    if okay:
        bars[(r.symbol,r.d0_date)] = b.iloc[:10].assign(d0_date=r.d0_date).copy()
    candidate_audit.append(dict(d0_date=r.d0_date, d1_date=r.d1_date, symbol=r.symbol,
                                old_reason=r.reason, prefix_valid=okay,
                                open_diff=float(b.open.iloc[0]-r.d1_open) if b is not None and len(b) else None,
                                close_diff=float(b.close.iloc[-1]-r.d1_close) if b is not None and len(b) else None))
pd.DataFrame(candidate_audit).to_csv(OUT/'full_day_exclusion_prefix.csv',index=False)

for d0 in affected_days:
    group = rows[rows.d0_date.eq(d0)]
    failed_prefix = [r.symbol for r in group.itertuples() if r.ordinary and
                     not prefix_valid(bars.get((r.symbol,d0)),float(r.d1_cap))]
    old = original.loc[d0] if d0 in original.index else None
    old_symbol = old.symbol if old is not None else None
    new_symbol = None
    if not failed_prefix:
        options = []
        for r in group.itertuples():
            if not r.ordinary: continue
            b = bars[(r.symbol,d0)]
            z = four_features(b,float(r.d1_preclose),float(r.d1_cap))
            if not accepts(z,rules[d0[:7]]): continue
            first = next((n for n in range(35,41)
                          if b.iloc[n-31].volume>0 and b.iloc[n-31].open<float(r.d1_cap)-.005),None)
            if first is not None: options.append((first,int(r.d0_rank),r,z,b))
        if options:
            n,rank,r,z,b = min(options,key=lambda x:(x[0],x[1]))
            if z['body5'] > -.08+1e-12:
                next_bar = next((j for j in range(n+1,41)
                                 if b.iloc[j-31].volume>0 and b.iloc[j-31].open<float(r.d1_cap)-.005),None)
                if next_bar is not None: new_symbol=r.symbol
    changed.append(dict(d0_date=d0, old_symbol=old_symbol,
                        prefix_complete=not failed_prefix, missing_prefix=','.join(failed_prefix),
                        prefix_symbol=new_symbol,
                        changed=old_symbol!=new_symbol if not failed_prefix else None))
result = pd.DataFrame(changed)
result.to_csv(OUT/'prefix_sensitivity.csv',index=False)
summary=dict(full_day_excluded_candidates=len(bad), affected_d0_days=len(affected_days),
             valid_prefix_candidates=int(pd.DataFrame(candidate_audit).prefix_valid.sum()),
             complete_days=int(result.prefix_complete.sum()),
             complete_days_changed=int(result.changed.eq(True).sum()),
             incomplete_days=int((~result.prefix_complete).sum()),
             incomplete_days_with_original_trade=int(((~result.prefix_complete)&result.old_symbol.notna()).sum()))
(OUT/'prefix_sensitivity.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
