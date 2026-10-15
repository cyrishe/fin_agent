"""Explain original vs delayed buy timing; descriptive attribution, no strategy changes."""
import json
import numpy as np
import pandas as pd
from scripts.analyze_limit_rebound_entry_windows_r23d import OUT,SOURCE
from scripts.replay_limit_rebound_cash_r23 import BASE,replay,dump


def main():
    original=pd.read_pickle(SOURCE/'ledger.pkl');delayed=pd.read_pickle(OUT/'delay_ledger.pkl')
    signals=pd.read_pickle(SOURCE/'signals.pkl');bars=pd.read_pickle(OUT/'minute_evidence.pkl')
    calendar=json.loads((BASE/'calendar.json').read_text());kept=set(delayed.entry_date)
    mid,middle_months,middle_nav,middle_ledger,_=replay(signals[signals.d1_date.isin(kept)],calendar,fee_rate=.001)
    # Independent event totals for the diagnostic middle account.
    for day in middle_nav.itertuples():
        buys=middle_ledger[middle_ledger.entry_date.le(day.date)]
        sold=middle_ledger[middle_ledger.exit_date.fillna('9999').le(day.date)]
        cash=1e6-buys.cost.sum()+sold.proceeds.sum()
        held=buys[buys.exit_date.fillna('9999').gt(day.date)]
        mv=sum(r.shares*r.d1_close for r in held.itertuples())
        assert np.isclose(day.nav,cash+mv,atol=1e-7,rtol=0)
    pair=original[['entry_date','buy','return_']].merge(delayed[['entry_date','buy','return_']],on='entry_date',suffixes=('_old','_delay'))
    changes=pair.buy_delay/pair.buy_old-1
    missing=original[~original.entry_date.isin(kept)];miss=[];windows=[]
    for r in original.itertuples():
        g=bars[bars.entry_date.eq(r.entry_date)].sort_values('time')
        clock=pd.to_datetime(g.time).dt.strftime('%H:%M')
        w=g[clock.ge('09:37')]  # True wall-clock09:36–09:40, four bars, not five end labels.
        below=(w.volume>0)&(w.low<w.cap-.005);whole=(w.volume>0)&(w.high<w.cap-.005)
        fullcap=(w.volume>0)&(w.low>=w.cap-.005)
        episodes=0;active=False
        for low,cap in zip(below,fullcap):
            if low and not active:episodes+=1;active=True
            if cap:active=False
        windows.append(dict(date=r.entry_date,original_end_label=r.entry_time,
            whole_four=bool(whole.all()),below_four=bool(below.all()),below_minutes=int(below.sum()),episodes=episodes))
        if r.entry_date not in kept:
            later=g[clock.gt(r.entry_time)]
            why='截止时间' if later.empty else '仅分钟内板下' if ((later.volume>0)&(later.low<later.cap-.005)).any() else '后续全板'
            miss.append(dict(date=r.entry_date,end_label=r.entry_time,reason=why,old_net_return=r.return_,old_pnl=r.pnl))
    w=pd.DataFrame(windows);w.to_pickle(OUT/'timing_0936_0940_windows.pkl')
    middle_nav.to_pickle(OUT/'timing_attribution_middle_nav.pkl');middle_ledger.to_pickle(OUT/'timing_attribution_middle_ledger.pkl')
    dump(OUT/'timing_missed.json',miss)
    old=json.loads((SOURCE/'summary.json').read_text());new=json.loads((OUT/'delay_summary.json').read_text())
    result=dict(
        interpretation='D0 top3 score/rank known previous close; first5 shape visible after09:35. Final chosen stock requires later activation. End-label09:36 bar open is first print09:35–09:36; delayed case buys later bar open, not09:36 close. Positive full-bar volume is an ex-post proxy, not a live fill proof.',
        original_end_label_counts={k:int(v) for k,v in original.entry_time.value_counts().sort_index().items()},
        delayed_end_label_counts={k:int(v) for k,v in delayed.entry_time.value_counts().sort_index().items()},
        attribution=[{'case':'全部106笔原买价','total_return':old['total_return']},
            {'case':'仅延后可买98笔仍用原买价_事后归因非可执行策略','total_return':mid['total_return']},
            {'case':'同98笔改用延后买价','total_return':new['total_return']}],
        removal_effect_pp=(mid['total_return']-old['total_return'])*100,
        delayed_price_effect_pp=(new['total_return']-mid['total_return'])*100,
        missed=len(missing),missed_profitable=int(missing.return_.gt(0).sum()),missed_mean_return=float(missing.return_.mean()),
        missed_original_path_pnl=float(missing.pnl.sum()),missed_reasons=pd.Series([r['reason'] for r in miss]).value_counts().to_dict(),
        paired_price_higher=int(changes.gt(1e-12).sum()),paired_price_same=int(changes.abs().le(1e-12).sum()),paired_price_lower=int(changes.lt(-1e-12).sum()),
        paired_original_mean=float(pair.return__old.mean()),paired_delayed_mean=float(pair.return__delay.mean()),
        true_clock_window=dict(start='09:36',end='09:40',bar_end_labels=['09:37','09:38','09:39','09:40'],
            universe='106 finally selected stocks; not all were already activated at09:36',
            whole_four=int(w.whole_four.sum()),below_each_four=int(w.below_four.sum()),any_below=int(w.below_minutes.gt(0).sum()),
            repeated_separated_episodes=int(w.episodes.ge(2).sum()),repeat_dates=w[w.episodes.ge(2)].date.tolist(),
            caveat='At least2 below-cap trade segments separated by a whole-minute cap-only trade bar; exact intraminute reopen counts/order unobservable.'),
        first_bar93_subset=dict(n=93,whole_four=int(w[w.original_end_label.eq('09:36')].whole_four.sum()),
            any_below=int(w[w.original_end_label.eq('09:36')].below_minutes.gt(0).sum())),
        preservation='No workbook or strategy inputs changed; middle account is diagnostic attribution only; account effects depend on this decomposition order and capital rotation.')
    dump(OUT/'timing_explanation.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
