"""R19 frozen-test execution windows; descriptive diagnostics, no retuning."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_r19 import OUT,dump,sha


def longest(flags):
    best=run=0
    for x in flags:
        run=run+1 if x else 0;best=max(best,run)
    return best


def returns(r,price):
    ans={};valid=True
    for k in [2,3,4]:
        valid &= getattr(r,f'd{k}_close')>0 and abs(getattr(r,f'd{k}_preclose')-getattr(r,f'd{k-1}_close'))<=.011
        ans[f'ret{k}']=getattr(r,f'd{k}_close')/price-1 if valid else np.nan
    ans['strategy']=ans['ret2'] if r.d2_up==True else ans['ret3'] if r.d3_up==True else ans['ret4']
    return ans


def main():
    f=pd.read_pickle(OUT/'test_top1.pkl');qualified=f[f.rule_pass];records=[];minute_rows=[];delayed=[]
    spec_hash=sha(OUT/'all_rules_frozen.json')
    for r in qualified.itertuples():
        raw=pd.read_pickle(OUT/'minute_days'/f'{r.d1_date}_raw.pkl')
        b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time')
        assert len(b)==240
        w=b.iloc[5:10].reset_index(drop=True)
        below=(w.volume.gt(0)&w.low.lt(r.d1_cap-.005)).to_numpy()
        whole=(w.volume.gt(0)&w.high.lt(r.d1_cap-.005)).to_numpy()
        opens=(w.volume.gt(0)&w.open.lt(r.d1_cap-.005)).to_numpy()
        touches=(w.volume.gt(0)&w.high.ge(r.d1_cap-.005)).to_numpy()
        assert bool(opens.any())==bool(r.selected_trade)
        idx=int(np.flatnonzero(opens)[0]) if opens.any() else None
        prefix=0;first_touch=None
        if idx is not None:
            for j in range(idx,5):
                if not whole[j]:break
                prefix+=1
            hits=np.flatnonzero(touches[idx:]);first_touch=int(hits[0]) if len(hits) else None
            assert r.entry==w.open.iloc[idx]
            assert r.entry_time==pd.Timestamp(w.time.iloc[idx]).strftime('%H:%M')
        rec={'d0_date':r.d0_date,'d1_date':r.d1_date,'symbol':r.symbol,'rule':r.rule_text,
          'filled':bool(r.selected_trade),'below_minutes':int(below.sum()),'whole_minutes':int(whole.sum()),
          'longest_whole_run':longest(whole),'touch_minutes':int(touches.sum()),
          'entry_minute':idx+6 if idx is not None else None,'entry_time':r.entry_time if idx is not None else None,
          'entry_price':r.entry if idx is not None else None,'entry_whole':bool(whole[idx]) if idx is not None else None,
          'entry_whole_prefix':prefix if idx is not None else None,
          'first_touch_offset':first_touch,'whole_after_entry':int(whole[idx:].sum()) if idx is not None else None,
          'entry_gap_to_cap':r.d1_cap/r.entry-1 if idx is not None else None,
          'below_at_0935':bool(b.close.iloc[4]<r.d1_cap-.005),
          'touch_first5':r.touch5>0,'label3':int(r.label_3) if r.observed_3 else None,
          'ret2':r.ret2,'ret3':r.ret3,'ret4':r.ret4,'strategy':r.strategy}
        records.append(rec)
        for j,x in w.iterrows():
            minute_rows.append({'d0_date':r.d0_date,'d1_date':r.d1_date,'symbol':r.symbol,'minute':j+6,
              'end_time':pd.Timestamp(x.time).strftime('%H:%M'),'open':x.open,'high':x.high,'low':x.low,'close':x.close,
              'volume':x.volume,'cap':r.d1_cap,'below':bool(below[j]),'whole':bool(whole[j]),'open_below':bool(opens[j])})
        # One-minute extra observation/computation delay, same frozen signal.
        poss=np.flatnonzero(opens[1:])+1
        if len(poss):
            k=poss[0];price=float(w.open.iloc[k]);v=returns(r,price)
            delayed.append({'d0_date':r.d0_date,'symbol':r.symbol,'entry_minute':int(k+6),'price':price,
              'label3':int(r.label_3) if r.observed_3 else None,'whole':bool(whole[k]),**v})
    a=pd.DataFrame(records);picked=a[a.filled];miss=a[~a.filled];delay=pd.DataFrame(delayed)
    def dist(s):return {str(int(k)):int(v) for k,v in s.value_counts().sort_index().items()}
    def describe(g):
        return {'n':len(g),'hit':float(g.label3.mean()),'whole_at_least1':int(g.whole_minutes.ge(1).sum()),
          'whole_at_least3':int(g.whole_minutes.ge(3).sum()),'whole_all5':int(g.whole_minutes.eq(5).sum()),
          'whole_count_dist':dist(g.whole_minutes),'longest_whole_dist':dist(g.longest_whole_run),
          'below_count_dist':dist(g.below_minutes),'whole_median':float(g.whole_minutes.median()),
          'longest_whole_median':float(g.longest_whole_run.median()),
          'ret2':float(g.ret2.mean()),'ret3':float(g.ret3.mean()),'strategy':float(g.strategy.mean())}
    report={'scope':'Fixed R19 monthly rules, test D0 2025-09-01 through 2026-10-13. No fitting or selection.',
      'definition':'Five bars ending09:36..09:40. Whole means positive-volume bar high strictly below cap; not evidence of quote availability throughout every second.',
      'qualified':len(a),'filled':len(picked),'no_fill':len(miss),
      'no_fill_all_cap':int(miss.below_minutes.eq(0).sum()),
      'no_fill_intraminute_only':int(miss.below_minutes.gt(0).sum()),
      'selected_window':describe(picked),'qualified_window':describe(a),
      'entry_distribution':dist(picked.entry_minute),
      'entry_whole_prefix':dist(picked.entry_whole_prefix),
      'entry_first_touch_offset':dist(picked.first_touch_offset.dropna()),
      'no_touch_from_entry_to0940':int(picked.first_touch_offset.isna().sum()),
      'already_below_at0935':int(picked.below_at_0935.sum()),
      'entry_gap_to_cap_median':float(picked.entry_gap_to_cap.median()),
      'notouched_first5':describe(picked[~picked.touch_first5]),
      'touched_first5':describe(picked[picked.touch_first5]),
      'delay_one_minute':{'n':len(delay),'retained_original':len(set(delay.d0_date)&set(picked.d0_date)),
        'hit':float(delay.label3.mean()),'entry_whole':int(delay.whole.sum()),
        **{c:{'n':int(delay[c].count()),'mean':float(delay[c].mean())} for c in ['ret2','ret3','ret4','strategy']}},
      'frozen_rules_sha256':spec_hash}
    a.to_pickle(OUT/'execution_windows.pkl');pd.DataFrame(minute_rows).to_pickle(OUT/'execution_window_minutes.pkl')
    delay.to_pickle(OUT/'execution_delay_1min.pkl');dump(OUT/'execution_windows.json',report)
    assert sha(OUT/'all_rules_frozen.json')==spec_hash
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
