"""Independent price-account and four-bar decision replay for R90.

This does not import the R90 selection, gate, or cash-replay functions. It
accepts the frozen scored Top3 and full-day-audited candidate set as inputs.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT = ROOT / 'audit_r91'
OUT.mkdir(exist_ok=True)
calendar = json.loads((ROOT / 'calendar.json').read_text())
rows = pd.read_pickle(ROOT / 'enriched.pkl')
rows = rows[rows.part.eq('test')].sort_values(['d0_date', 'd0_rank'])
first10 = pd.read_pickle(ROOT / 'first10.pkl')
first10['symbol'] = first10.symbol.str[:6]
bars = {(s, d): b.sort_values('time').reset_index(drop=True)
        for (s, d), b in first10.groupby(['symbol', 'd0_date'])}
rules = {r['month']: r['selected']['atoms']
         for r in json.loads((ROOT / 'rules_frozen.json').read_text())}


def four_features(b, preclose, cap):
    v = b.iloc[:4]
    op = float(v.open.iloc[0]); cl = float(v.close.iloc[-1])
    hi = float(v.high.max()); lo = float(v.low.min())
    early = float(v.close.iloc[1]) - op
    late = cl - float(v.close.iloc[1])
    if abs(early) < .005 and abs(late) < .005:
        name = '平走'
    elif early < -.005 and late > .005:
        name = '前弱后强'
    elif early > .005 and late < -.005:
        name = '前强后弱'
    elif early >= -.005 and late >= -.005:
        name = '两段走强'
    else:
        name = '两段走弱'
    z = dict(high5=hi/preclose-1, low5=lo/preclose-1, body5=cl/op-1,
             pullback5=(hi-cl)/preclose, position5=(cl-lo)/(hi-lo) if hi-lo>.005 else .5,
             below5=int(((v.volume>0) & (v.low<cap-.005)).sum()),
             whole5=int(((v.volume>0) & (v.high<cap-.005)).sum()),
             touch5=int(((v.volume>0) & (v.high>=cap-.005)).sum()), shape=name)
    return {k: round(a, 15) if isinstance(a, float) else a for k, a in z.items()}


def accepts(z, atoms):
    for field, op, val in atoms:
        x = z[field]
        if op == '>=' and not x >= val: return False
        if op == '<' and not x < val: return False
        if op == '==' and not x == val: return False
    return True


decisions = []
feature_diffs = []
for d0, group in rows.groupby('d0_date'):
    options = []
    for r in group.itertuples():
        if not r.eligible:
            continue
        b = bars[(r.symbol, d0)]
        assert pd.to_datetime(b.time).dt.strftime('%H:%M').iloc[:10].tolist() == [f'09:{i:02}' for i in range(31,41)]
        z = four_features(b, float(r.d1_preclose), float(r.d1_cap))
        for k, value in z.items():
            saved = getattr(r, k)
            if isinstance(value, str):
                assert value == saved, (d0, r.symbol, k)
            else:
                delta = abs(float(value)-float(saved))
                feature_diffs.append(delta)
                assert delta < 1e-11, (d0, r.symbol, k, value, saved)
        if accepts(z, rules[d0[:7]]):
            opens = [(n, b.iloc[n-31]) for n in range(35,41)
                     if b.iloc[n-31].volume > 0 and b.iloc[n-31].open < float(r.d1_cap)-.005]
            if opens:
                options.append((opens[0][0], int(r.d0_rank), r, z, b))
    if not options:
        continue
    n, rank, r, z, b = min(options, key=lambda x: (x[0], x[1]))
    if z['body5'] <= -.08 + 1e-12:
        continue
    later = [(j, b.iloc[j-31]) for j in range(n+1,41)
             if b.iloc[j-31].volume > 0 and b.iloc[j-31].open < float(r.d1_cap)-.005]
    if later:
        j, bar = later[0]
        decisions.append(dict(d0_date=d0, symbol=r.symbol, entry_time=f'09:{j:02}',
                              entry=float(bar.open), d0_rank=rank))
reference = pd.read_pickle(ROOT / 'signals.pkl')
key = ['d0_date', 'symbol', 'entry_time']
a = pd.DataFrame(decisions).sort_values(key).reset_index(drop=True)
b = reference[key+['entry']].sort_values(key).reset_index(drop=True)
assert len(a) == len(b) and a[key].equals(b[key]), (len(a), len(b))
assert np.allclose(a.entry, b.entry, rtol=0, atol=1e-12)


def account(signals, start, final, sell_field):
    orders = {r.d1_date: r for r in signals.itertuples()}
    money = 1_000_000.
    holding = []
    history = []
    for day in (d for d in calendar if start <= d <= final):
        if day in orders:
            r = orders[day]
            assert len(holding) <= 1
            invest = money if holding else money/2
            units = invest/(float(r.entry)*1.001)
            money -= invest
            holding.append((r, units))
        next_holding = []
        marked = 0.
        for r, units in holding:
            if day == r.d1_date:
                marked += units * float(r.d1_close)
                next_holding.append((r, units))
            else:
                assert day == r.d2_date
                dividend = float(r.d2_cash_dividend or 0.)
                money += units * (float(getattr(r, sell_field))*.999 + dividend)
        holding = next_holding
        history.append((day, money+marked))
    return pd.DataFrame(history, columns=['date', 'nav']).set_index('date')


sig = pd.read_pickle(ROOT / 'signals_exit.pkl').sort_values('d1_date')
last = max(sig.d2_date)
shadow = account(sig, '2017-05-01', last, 'd2_close_original')
saved_shadow = pd.read_pickle(ROOT / 'shadow_close_nav.pkl').set_index('date')
shadow_delta = float((shadow.nav - saved_shadow.nav).abs().max())
assert shadow_delta < 1e-5, shadow_delta

gates = {}
counts = sig.d0_date.str[:7].value_counts().to_dict()
months = [str(x) for x in pd.period_range('2018-01','2021-12',freq='M')]
for month in months:
    prev = str(pd.Period(month)-1)
    x = shadow[shadow.index.str.startswith(prev)].nav
    prev_nav = shadow.nav.loc[shadow.index[shadow.index.get_loc(x.index[-5])-1]]
    ret5 = float(x.iloc[-1]/prev_nav-1)
    prev_nav10 = shadow.nav.loc[shadow.index[shadow.index.get_loc(x.index[-10])-1]]
    ret10 = float(x.iloc[-1]/prev_nav10-1)
    paused = counts.get(prev,0) == 0 or ret5+ret10 < 0
    gates[month] = dict(previous_month=prev, previous_candidates=counts.get(prev,0),
                        r5=ret5, r10=ret10, paused=paused)
stored = json.loads((ROOT / 'overall.json').read_text())['gate']
for month in months:
    v = stored[month]; q = gates[month]
    assert q['paused'] == v['paused']
    assert abs(q['r5']-v['previous_r5']) < 1e-10
    assert abs(q['r10']-v['previous_r10']) < 1e-10

recent = sig[sig.d0_date.ge('2018-01-01')].copy()
allowed = recent[recent.d0_date.str[:7].map(lambda m: not gates[m]['paused'])]
main = account(allowed, '2018-01-01', last, 'sell_price')
saved_main = pd.read_pickle(ROOT / 'primary_pause_7pct_nav.pkl').set_index('date')
main_delta = float((main.nav-saved_main.nav).abs().max())
assert main_delta < 1e-5, main_delta
assert len(allowed) == 295
report = dict(decision_days=int(rows.d0_date.nunique()), four_bar_candidates=int(rows.eligible.sum()),
              selected=len(a), max_feature_diff=max(feature_diffs),
              shadow_daily_nav_max_abs_diff=shadow_delta,
              gate_months=len(gates), gate_differences=0, main_entries=len(allowed),
              main_daily_nav_max_abs_diff=main_delta,
              final_nav=float(main.nav.iloc[-1]), total_return=float(main.nav.iloc[-1]/1_000_000-1),
              limitation='full-day audited eligibility and source daily panel are frozen inputs; no independent raw-data reconstruction')
(OUT / 'independent_replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
