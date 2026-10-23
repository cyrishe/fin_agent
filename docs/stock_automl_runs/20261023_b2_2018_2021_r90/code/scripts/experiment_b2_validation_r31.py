"""R31: fixed B2, six training months, one versus two validation months.

Run under sklearn 1.7.1. No production writes. Stages are restartable and all
models, scores, actual samples, rule candidates and execution evidence survive.
"""
from __future__ import annotations

import argparse
import json
import platform
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts import extend_limit_rebound_2024_minutes as extension
from scripts.experiment_limit_rebound_segments_r18 import FEATURES, OUT as BASE
from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import rank, classify
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.replay_limit_rebound_cash_r23 import dump, sha, replay

OUT = Path('outputs/stock_automl/b2_validation_r31_20261019')
MONTHS = [str(m) for m in pd.period_range('2026-04', '2026-09', freq='M')]
HISTORY = [str(m) for m in pd.period_range('2025-08', '2026-09', freq='M')]
KEY = ['symbol', 'd0_date']
VARIANTS = {'D2S2': (2, 2), 'D1S2': (1, 2), 'D2S1': (2, 1), 'D1S1': (1, 1)}
NAMES = {'D2S2': '两层两月', 'D1S2': '日线一月', 'D2S1': '形态一月', 'D1S1': '两层一月'}


def window(month, valid=2):
    m = pd.Period(month, freq='M')
    return {'train': [str(m-valid-6), str(m-valid-1)],
            'valid': [str(m-valid), str(m-1)], 'test': month}


def spec():
    OUT.mkdir(parents=True, exist_ok=True)
    value = dict(experiment='R31_B2_validation_1_vs_2_months', months=MONTHS,
        variants=VARIANTS, training_months=6, features=FEATURES['segments'],
        first_stage='Fixed R18 RF240 depth7 leaf50 max_features0.8 seed20261013; balanced weights; sklearn1.7.1; no validation refit or algorithm selection.',
        prior='R22 tested shape1+1 and2+1 on fiveK, daily6+2 unchanged. No matching two-stage6+1 B2 study found.',
        second_stage='Original fourK79 rules; unchanged train15/40%, valid6/30%, hit+3pp, strategy-mean guards, three2month training blocks, shortlist3, Wilson. One-month validation retains minimum6; no outcome-based relaxation.',
        validation_scoring='Training Top1 uses each historical month causal forecast. Validation uses target model where not in-fit. D1S2 first validation month uses preceding target model validation scores to avoid fitting on its own validation month. All label D4 cutoffs retained.',
        execution='Original four_fit_delay: Top3 only, four completed bars, first activation09:35..09:40 end labels, tie D0rank; body>-8% after lock without replacement; same-stock next qualifying bar open by09:40. D2 close sell.',
        account='100w; flat half cash, carrying one lot all remaining cash; fractional shares; fees0.1% each side; no slippage/queue/capacity guarantee.',
        boundaries='Select D0 signals Apr1-Sep30. Main account Apr1-Sep30 mark pending at close; separate closed-cohort continuation to final D2. No March carry-in. Monthly account by holding calendar; classification by D0 month.',
        exposure='Previously researched history; monthly chronological holdouts, not new external blind test. All four schemes fixed before results, no per-month test winner.',
        source_panel=str(BASE/'panel.pkl'), panel_sha256=sha(BASE/'panel.pkl'),
        calendar_sha256=sha(BASE/'calendar.json'), baseline_rules_sha256=sha(four.OUT/'four_rules_frozen.json'),
        baseline_picks_sha256=sha(four.OUT/'policy_picks.pkl'))
    path = OUT/'specification.json'
    if path.exists():
        assert json.loads(path.read_text()) == json.loads(json.dumps(value))
    else:
        dump(path, value)
    return value


def source(dv, month, part):
    root = BASE if dv == 2 else OUT/'daily1'
    return root/month/f'segments_{part}_scores.pkl'


def train_one(month):
    assert sklearn.__version__ == '1.7.1'
    p = pd.read_pickle(BASE/'panel.pkl')
    cal = json.loads((BASE/'calendar.json').read_text())
    w = window(month, 1)
    tr = block(p, cal, *w['train'])
    va = block(p, cal, *w['valid'], purge_events=False)
    te = p[p.d0_date.str.startswith(month)]
    assert not set(zip(tr.symbol, tr.first_limit_date)) & set(zip(va.symbol, va.first_limit_date))
    fit = tr[tr.activated & tr.observed_3].copy()
    root = OUT/'daily1'/month
    root.mkdir(parents=True, exist_ok=True)
    if (root/'metrics.json').exists():
        return month
    model = make_model('rf')
    model.steps[-1][1].n_jobs = 1
    model.fit(xmatrix(fit, FEATURES['segments']), fit.label_3,
              randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
    fit.to_pickle(root/'training_samples.pkl')
    bundle = dict(model=model, features=FEATURES['segments'], month=month, window=w,
                  sklearn=sklearn.__version__, training_keys=fit[KEY+['first_limit_date']].to_dict('records'),
                  spec_sha256=sha(OUT/'specification.json'))
    joblib.dump(bundle, root/'segments.joblib', compress=3)
    restored = joblib.load(root/'segments.joblib')['model']
    assert np.array_equal(model.predict_proba(xmatrix(fit.iloc[:50], FEATURES['segments'])),
                          restored.predict_proba(xmatrix(fit.iloc[:50], FEATURES['segments'])))
    metrics = {}
    for part, frame in [('train', tr), ('valid', va), ('forecast', te)]:
        scores = rank(frame, restored.predict_proba(xmatrix(frame, FEATURES['segments']))[:, 1])
        scores[KEY+['score', 'd0_rank']].to_pickle(source(1, month, part))
        metrics[part] = classify(scores)
    dump(root/'metrics.json', dict(window=w, fit_rows=len(fit), metrics=metrics,
         model_sha256=sha(root/'segments.joblib'), sample_sha256=sha(root/'training_samples.pkl')))
    return month


def train():
    spec()
    dump(OUT/'environment.json', dict(python=platform.python_version(), sklearn=sklearn.__version__,
                                    numpy=np.__version__, pandas=pd.__version__, joblib=joblib.__version__))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(train_one, m) for m in HISTORY]):
            print('trained', future.result(), flush=True)


def assemble():
    panel = pd.read_pickle(BASE/'panel.pkl')
    cal = json.loads((BASE/'calendar.json').read_text())
    frames = []
    provenance = []
    for name, (dv, sv) in VARIANTS.items():
        for month in MONTHS:
            w = window(month, sv)
            for part in ['train', 'valid', 'test']:
                paths = []
                if part == 'train':
                    paths = [(source(dv, str(m), 'forecast'), str(m))
                             for m in pd.period_range(*w['train'], freq='M')]
                elif part == 'valid':
                    for m in pd.period_range(*w['valid'], freq='M'):
                        scoring_month = str(min(pd.Period(month), m+dv))
                        assert window(scoring_month, dv)['train'][1] < str(m)
                        path = source(dv, scoring_month, 'valid')
                        if dv == 1 and sv == 2 and str(m) == w['valid'][0]:
                            # Outcomes at the end of this first validation month
                            # have matured by the target month. Score its full
                            # pool with the prior model, then apply the shared
                            # two-month D4 cutoff below (not an extra monthly gap).
                            path = OUT/'daily1'/scoring_month/f'shape_valid_full_{m}.pkl'
                            model = joblib.load(OUT/'daily1'/scoring_month/'segments.joblib')['model']
                            rows = panel[panel.d0_date.str.startswith(str(m))]
                            ranked = rank(rows, model.predict_proba(xmatrix(rows, FEATURES['segments']))[:, 1])
                            ranked[KEY+['score', 'd0_rank']].to_pickle(path)
                        paths.append((path, str(m)))
                else:
                    paths = [(source(dv, month, 'forecast'), month)]
                scored = []
                for path, keep_month in paths:
                    a = pd.read_pickle(path)
                    a = a[a.d0_date.str.startswith(keep_month)]
                    scored.append(a)
                    provenance.append(dict(variant=name, month=month, part=part,
                                           keep_month=keep_month, path=str(path), sha256=sha(path)))
                s = pd.concat(scored, ignore_index=True)
                if part != 'test':
                    allowed = block(panel, cal, *w[part], purge_events=part == 'train')[KEY]
                    s = s.merge(allowed, on=KEY, validate='one_to_one')
                s = s[s.d0_rank.le(3 if part == 'test' else 1)].merge(panel, on=KEY, validate='one_to_one')
                s['variant'], s['model_month'], s['part'] = name, month, part
                frames.append(s)
    f = pd.concat(frames, ignore_index=True)
    f.to_pickle(OUT/'stage_samples.pkl')
    dump(OUT/'score_provenance.json', provenance)
    print('assembled', len(f), 'unique', len(f.drop_duplicates(KEY)), flush=True)
    return f


def enrich_day(day, demand):
    """Reuse existing full-minute evidence before fetching unseen candidates."""
    found, bars = {}, {}
    roots = [four.R20/'minute_days', shape.OUT/'minute_days', shape.OLD/'days']
    for root in roots:
        path = root/f'{day}_raw.pkl'
        if not path.exists():
            continue
        raw = pd.read_pickle(path)
        for (symbol, src), b in raw.groupby(['symbol', 'source']):
            symbol = str(symbol)[:6]
            if symbol in found or symbol not in set(demand.symbol):
                continue
            r = demand[demand.symbol.eq(symbol)].iloc[0]
            z = shape.facts(b, r)
            if z['audit_ok']:
                found[symbol] = dict(symbol=symbol, d1_date=day, minute_source=str(src), **z)
                bars[symbol] = b.sort_values('time').copy()
    missing = demand[~demand.symbol.isin(found)]
    if len(missing):
        # scan_day writes only to the experiment's isolated cache.
        results = shape.scan_day(day, missing)
        raw_path = OUT/'minute_days'/f'{day}_raw.pkl'
        raw = pd.read_pickle(raw_path) if raw_path.exists() else pd.DataFrame()
        for z in results:
            found[z['symbol']] = z
            if z.get('audit_ok'):
                b = raw[raw.symbol.eq(z['symbol']) & raw.source.eq(z['minute_source'])].sort_values('time')
                assert len(b) == 240
                bars[z['symbol']] = b
    records, extracts = [], []
    for r in demand.itertuples():
        z = found[r.symbol]
        if z['audit_ok']:
            b = bars[r.symbol].reset_index(drop=True)
            z.update(four.first_features(b, r.d1_preclose, r.d1_cap, 4))
            z.update(four.entry_facts(b, r.d1_cap, 4))
            extracts.append(b.iloc[:10].assign(d0_date=r.d0_date))
        records.append(z)
    return records, extracts


def prepare():
    spec()
    f = assemble()
    d = f.drop_duplicates(KEY).copy()
    d = d.merge(extension.daily_high_low(d), on=['symbol', 'd1_date'], how='left', validate='one_to_one')
    d = extension.with_status(d)
    d.to_pickle(OUT/'minute_demand.pkl')
    # Global output changed only in this experiment process, not existing files.
    shape.OUT = OUT
    rows, extracts = [], []
    jobs = list(d.groupby('d1_date'))
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(enrich_day, day, group) for day, group in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            r, b = future.result()
            rows.extend(r); extracts.extend(b)
            if i % 25 == 0 or i == len(jobs):
                print('minute days', i, '/', len(jobs), flush=True)
    rows.extend(dict(symbol=r.symbol, d1_date=r.d1_date, audit_ok=False, reason='D1日线缺失')
                for r in d[d.d1_date.isna()].itertuples())
    facts = pd.DataFrame(rows)
    facts.to_pickle(OUT/'minute_facts.pkl')
    pd.concat(extracts, ignore_index=True).to_pickle(OUT/'first10.pkl')
    f = f.merge(d[KEY+['ordinary', 'state0', 'state1', 'd1_high', 'd1_low']], on=KEY, validate='many_to_one')
    f = f.merge(facts, on=['symbol', 'd1_date'], how='left', validate='many_to_one')
    f['eligible'] = f.ordinary & f.audit_ok.eq(True)
    f = four.reprice(f)
    f['observation_bars'] = 4
    f.to_pickle(OUT/'enriched.pkl')
    dump(OUT/'coverage.json', dict(stock_days=len(d), minute_ok=int(facts.audit_ok.eq(True).sum()),
         failed=facts.loc[~facts.audit_ok.eq(True), 'reason'].fillna('源记录未提供原因').value_counts().to_dict()))


def select_rules():
    f = pd.read_pickle(OUT/'enriched.pkl')
    cal = json.loads((BASE/'calendar.json').read_text())
    old = {s['month']: s for s in json.loads((four.OUT/'four_rules_frozen.json').read_text())}
    selected, candidates = [], []
    original_window = shape.window
    try:
        for name, (dv, sv) in VARIANTS.items():
            shape.window = lambda m, v=sv: window(m, v)
            for month in MONTHS:
                q = f[f.variant.eq(name) & f.model_month.eq(month)]
                parts = []
                for part in ['train', 'valid']:
                    a = q[q.part.eq(part)]
                    cutoff = str(pd.Period(window(month, sv)[part][1])+1)+'-01'
                    assert all(cal[int(i)+4] < cutoff for i in a.date_i)
                    parts.append(a[a.complete].copy())
                rule = json.loads(json.dumps(four.choose_rule(*parts, month, shape.rules())))
                # Baseline must reproduce the original rule and fitted metrics.
                if name == 'D2S2':
                    for k in ['selected', 'train_base', 'valid_base', 'train_selected', 'valid_selected']:
                        assert rule[k] == old[month][k], (name, month, k)
                rule['variant'] = name
                selected.append(rule)
                for r in shape.rules():
                    candidates.append(dict(variant=name, month=month, rule=r,
                                           train=shape.metrics(parts[0], r), valid=shape.metrics(parts[1], r)))
                print('rule', name, month, rule['selected']['id'], flush=True)
    finally:
        shape.window = original_window
    dump(OUT/'rules_frozen.json', selected)
    dump(OUT/'rule_candidates.json', candidates)
    dump(OUT/'selection_receipt.json', dict(rules_sha256=sha(OUT/'rules_frozen.json'),
         candidates_sha256=sha(OUT/'rule_candidates.json'), baseline_six_rules_exact=True,
         saved_before_account_evaluation=True))


def evaluate():
    rules = json.loads((OUT/'rules_frozen.json').read_text())
    f = pd.read_pickle(OUT/'enriched.pkl')
    first = pd.read_pickle(OUT/'first10.pkl')
    bars = {(s, day): g.sort_values('time') for (s, day), g in first.groupby(KEY)}
    cal = json.loads((BASE/'calendar.json').read_text())
    ref = pd.read_pickle(four.OUT/'policy_picks.pkl')
    ref = ref[ref.policy.eq('four_fit_delay') & ref.d0_date.str[:7].isin(MONTHS)]
    picks, classifications = [], []
    for r in rules:
        name, month = r['variant'], r['month']
        q = f[f.variant.eq(name) & f.model_month.eq(month) & f.part.eq('test')]
        a = four.chosen(q, r['selected'])
        a = four.delay_same_stock(a, bars)
        a['rule_id'] = r['selected']['id']
        a['rule_text'] = r['selected']['text'].replace('五分', '四分')
        picks.append(a)
        for group, z in [('形态前Top3', q[q.eligible]),
                         ('形态通过Top3', q[q.eligible & shape.mask(q, r['selected'])]),
                         ('最终交易', a)]:
            known = z[z.observed_3]
            classifications.append(dict(variant=name, month=month, group=group, n=len(known),
                                        hits=int(known.label_3.sum()), hit=float(known.label_3.mean()) if len(known) else None))
    p = pd.concat(picks, ignore_index=True)
    keys = KEY+['d1_date', 'entry_time', 'entry']
    pd.testing.assert_frame_equal(p[p.variant.eq('D2S2')][keys].sort_values('d0_date').reset_index(drop=True),
                                  ref[keys].sort_values('d0_date').reset_index(drop=True), check_dtype=False)
    p.to_pickle(OUT/'signals.pkl')
    dump(OUT/'shape_test_metrics.json', classifications)
    accounts, months, navs, ledgers, exclusions, follow = [], [], [], [], [], []
    for name in VARIANTS:
        q = p[p.variant.eq(name)]
        s, m, n, l, e = replay(q, cal, start='2026-04-01', end='2026-09-30', fee_rate=.001)
        accounts.append(dict(variant=name, **s))
        months.extend([dict(variant=name, **r) for r in m])
        navs.append(n.assign(variant=name)); ledgers.append(l.assign(variant=name))
        exclusions.extend([dict(variant=name, **r) for r in e])
        final_i = max(cal.index(d)+1 for d in q.d1_date)
        s2, _, _, l2, e2 = replay(q, cal, start='2026-04-01', end=cal[final_i], fee_rate=.001)
        follow.append(dict(variant=name, **s2))
        l2.to_pickle(OUT/f'{name}_closed_cohort.pkl')
    dump(OUT/'accounts.json', accounts); dump(OUT/'monthly.json', months)
    dump(OUT/'closed_cohort_accounts.json', follow); dump(OUT/'exclusions.json', exclusions)
    pd.concat(navs).to_pickle(OUT/'nav.pkl'); pd.concat(ledgers).to_pickle(OUT/'ledger.pkl')
    daily_metrics = []
    panel = pd.read_pickle(BASE/'panel.pkl')
    for dv in [2, 1]:
        for month in MONTHS:
            for part in ['train', 'valid', 'forecast']:
                scored = pd.read_pickle(source(dv, month, part)).merge(panel, on=KEY, validate='one_to_one')
                daily_metrics.append(dict(daily_validation=dv, month=month, part=part, **classify(scored)))
    dump(OUT/'daily_metrics.json', daily_metrics)
    # Independent cash reconciliation, including all idle days and pending marks.
    for name, g in pd.concat(navs).groupby('variant'):
        l = pd.concat(ledgers).query('variant == @name')
        for r in g.itertuples():
            bought = l[l.entry_date.le(r.date)]
            sold = l[l.exit_date.fillna('9999').le(r.date)]
            held = bought[bought.exit_date.fillna('9999').gt(r.date)]
            cash = 1e6-bought.cost.sum()+sold.proceeds.sum()
            assert np.isclose(r.cash, cash, atol=1e-7, rtol=0)
            assert np.isclose(r.nav, cash+(held.shares*held.d1_close).sum(), atol=1e-7, rtol=0)
    dump(OUT/'verification.json', dict(baseline_trade_rows_exact=True,
          baseline_rules_exact=True, independent_cash_rows=sum(len(n) for n in navs),
          sample_label_maturity_checked=True, account_not_used_for_rule_selection=True))
    print(pd.DataFrame(accounts)[['variant', 'entries', 'total_return', 'max_drawdown', 'sharpe']].to_string(index=False), flush=True)


def audit():
    from datetime import datetime
    from scripts.automl_b2 import evaluate_morning
    panel = pd.read_pickle(BASE/'panel.pkl')
    reloads = []
    for dv, months in [(2, MONTHS), (1, HISTORY)]:
        for month in months:
            root = BASE if dv == 2 else OUT/'daily1'
            bundle = joblib.load(root/month/'segments.joblib')
            frame = panel[panel.d0_date.str.startswith(month)]
            scores = rank(frame, bundle['model'].predict_proba(xmatrix(frame, FEATURES['segments']))[:, 1])
            ref = pd.read_pickle(source(dv, month, 'forecast'))
            joined = scores[KEY+['score', 'd0_rank']].merge(ref, on=KEY, validate='one_to_one', suffixes=('_new','_old'))
            diff = float(abs(joined.score_new-joined.score_old).max())
            assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_old).all()
            if dv == 1:
                fit = pd.read_pickle(root/month/'training_samples.pkl')
                assert fit[KEY+['first_limit_date']].to_dict('records') == bundle['training_keys']
                assert fit.activated.all() and fit.observed_3.all()
            reloads.append(dict(validation=dv, month=month, rows=len(joined), max_diff=diff))
    dump(OUT/'model_reload_verification.json', reloads)
    f = pd.read_pickle(OUT/'enriched.pkl')
    first = pd.read_pickle(OUT/'first10.pkl')
    bars = {(s, d0): g.sort_values('time') for (s, d0), g in first.groupby(KEY)}
    rules = {(r['variant'], r['month']): r['selected'] for r in json.loads((OUT/'rules_frozen.json').read_text())}
    signals = pd.read_pickle(OUT/'signals.pkl')
    selected = []; decisions = []
    for (variant, day), group in f[f.part.eq('test')].groupby(['variant','d1_date']):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=r.symbol, selection_rank=int(r.d0_rank), preclose=float(r.d1_preclose),
                      cap=float(r.d1_cap), ordinary=bool(r.eligible)) for r in group.itertuples()]
        sources = {r.symbol: bars.get((r.symbol, d0), pd.DataFrame()) for r in group.itertuples()}
        states = evaluate_morning(picks, sources, day, datetime.fromisoformat(day+' 09:40:00'), rules[(variant,d0[:7])])
        for state in states:
            decisions.append(dict(variant=variant, d0_date=d0, d1_date=day, **state))
            if state['actionable']:
                selected.append((variant,d0,state['symbol6'],state['entry_bar_end'][11:16],float(state['entry_price'])))
    expected = [(r.variant,r.d0_date,r.symbol,r.entry_time,float(r.entry)) for r in signals.itertuples()]
    assert sorted(selected) == sorted(expected)
    pd.DataFrame(decisions).to_pickle(OUT/'execution_decisions.pkl')
    verification = json.loads((OUT/'verification.json').read_text())
    verification.update(model_reload_count=len(reloads), model_max_score_diff=max(r['max_diff'] for r in reloads),
                        production_evaluator_same_signals=len(selected),
                        candidate_decisions=len(decisions), original_input_spec_sha256=sha(OUT/'specification.json'))
    dump(OUT/'verification.json', verification)
    print(json.dumps(verification,ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['train', 'prepare', 'select', 'evaluate', 'audit'])
    arg = parser.parse_args()
    {'train': train, 'prepare': prepare, 'select': select_rules, 'evaluate': evaluate, 'audit': audit}[arg.stage]()
