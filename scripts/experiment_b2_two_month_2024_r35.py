"""Extend the original B2 two-month-validation scheme to March 2024.

Uses the same 2022–2026 panel and 2023–2024 minute archive as R34. The
original May 2025 onward B2 decisions and account must reproduce exactly.
"""
from __future__ import annotations

import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts import experiment_b2_full_2024_r34 as one
from scripts import experiment_b2_validation_full_r32 as reference
from scripts import experiment_b2_validation_r31 as r
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import rank, classify
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT = Path('outputs/stock_automl/b2_two_month_2024_r35_20261019')
PANEL = one.OUT
R18 = one.R18
R32 = one.R32
R34 = one.OUT
FIRST = '2024-03'
LAST = '2026-10'
MONTHS = [str(x) for x in pd.period_range(FIRST, LAST, freq='M')]
HISTORY = [str(x) for x in pd.period_range('2023-07', LAST, freq='M')]
NEW_MODELS = [str(x) for x in pd.period_range('2023-07', '2024-12', freq='M')]
KEY = one.KEY


def specification():
    OUT.mkdir(parents=True, exist_ok=True)
    value = dict(experiment='R35 B2 original six-train-two-valid early extension',
        first_test=FIRST, last_test=LAST, month_count=len(MONTHS),
        daily_training_months=6, daily_validation_months=2,
        shape_training_months=6, shape_validation_months=2,
        source_panel_sha256=sha(PANEL / 'panel.pkl'),
        source_calendar_sha256=sha(PANEL / 'calendar.json'),
        daily_model='original R18 fixed RF51 family/weights/seed, sklearn1.7.1',
        shape_rules='original 79 fourK rules and original train/valid selection guards',
        execution='original B2 D0 Top3, D1 fourK, lock/no-replacement, next qualified minute open by09:40, D2 close, 0.1% each side',
        training_history='2022 Nov-Dec to start July2023 daily forecasts; 2023 Jul-Dec shape train, 2024 Jan-Feb shape valid',
        old_months_control='May2025-Oct2026 selected rule, signal, and reset account exact',
        production_changed=False, exploratory_history=True)
    path = OUT / 'specification.json'
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def source(dv, month, part):
    assert dv == 2
    root = OUT / 'daily2' / month if month < '2025-01' else R18 / month
    return root / f'segments_{part}_scores.pkl'


def configure():
    reference.OUT = OUT
    reference.BASE = PANEL
    r.OUT = OUT
    r.BASE = PANEL
    r.MONTHS = MONTHS
    r.HISTORY = HISTORY
    r.VARIANTS = {'D2S2': (2, 2)}
    r.spec = specification
    r.source = source
    return r


def train_one(month):
    assert sklearn.__version__ == '1.7.1'
    panel = pd.read_pickle(PANEL / 'panel.pkl')
    calendar = json.loads((PANEL / 'calendar.json').read_text())
    window = r.window(month, 2)
    train = r.block(panel, calendar, *window['train'])
    valid = r.block(panel, calendar, *window['valid'], purge_events=False)
    test = panel[panel.d0_date.str.startswith(month)]
    assert not set(zip(train.symbol, train.first_limit_date)) & set(zip(valid.symbol, valid.first_limit_date))
    fit = train[train.activated & train.observed_3].copy()
    root = OUT / 'daily2' / month
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'metrics.json').exists():
        return month
    model = make_model('rf')
    model.steps[-1][1].n_jobs = 1
    model.fit(xmatrix(fit, FEATURES['segments']), fit.label_3,
              randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
    fit.to_pickle(root / 'training_samples.pkl')
    bundle = dict(model=model, features=FEATURES['segments'], month=month,
                  window=window, sklearn=sklearn.__version__,
                  training_keys=fit[KEY + ['first_limit_date']].to_dict('records'),
                  spec_sha256=sha(OUT / 'specification.json'))
    joblib.dump(bundle, root / 'segments.joblib', compress=3)
    restored = joblib.load(root / 'segments.joblib')['model']
    assert np.array_equal(model.predict_proba(xmatrix(fit.iloc[:50], FEATURES['segments'])),
                          restored.predict_proba(xmatrix(fit.iloc[:50], FEATURES['segments'])))
    metrics = {}
    for part, frame in [('train', train), ('valid', valid), ('forecast', test)]:
        scored = rank(frame, restored.predict_proba(xmatrix(frame, FEATURES['segments']))[:, 1])
        scored[KEY + ['score', 'd0_rank']].to_pickle(source(2, month, part))
        metrics[part] = classify(scored)
    dump(root / 'metrics.json', dict(window=window, fit_rows=len(fit), metrics=metrics,
         model_sha256=sha(root / 'segments.joblib'), sample_sha256=sha(root / 'training_samples.pkl')))
    return month


def train():
    configure()
    specification()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(train_one, month) for month in NEW_MODELS]
        for future in as_completed(futures):
            print('trained two-month', future.result(), flush=True)
    controls = []
    panel = pd.read_pickle(PANEL / 'panel.pkl')
    calendar = json.loads((PANEL / 'calendar.json').read_text())
    for month in ['2024-09', '2024-10', '2024-11', '2024-12']:
        oldroot = Path('outputs/stock_automl/limit_rebound_2024_minute_extension_20261017/early_models') / month
        path = oldroot / 'segments_forecast_scores.pkl'
        if not path.exists():
            continue
        root = OUT / 'daily2' / month
        oldbundle = joblib.load(oldroot / 'segments.joblib')
        fit = pd.read_pickle(root / 'training_samples.pkl')
        assert oldbundle['training_keys'] == fit[KEY + ['first_limit_date']].to_dict('records')
        assert oldbundle['features'] == FEATURES['segments']
        shutil.copy2(oldroot / 'segments.joblib', root / 'segments.joblib')
        model = oldbundle['model']
        window = r.window(month, 2)
        samples = {'train': r.block(panel, calendar, *window['train']),
                   'valid': r.block(panel, calendar, *window['valid'], purge_events=False),
                   'forecast': panel[panel.d0_date.str.startswith(month)]}
        metrics = {}
        for part, frame in samples.items():
            ranked = rank(frame, model.predict_proba(xmatrix(frame, FEATURES['segments']))[:, 1])
            ranked[KEY + ['score', 'd0_rank']].to_pickle(source(2, month, part))
            metrics[part] = classify(ranked)
        dump(root / 'metrics.json', dict(window=window, fit_rows=len(fit), metrics=metrics,
             model_sha256=sha(root / 'segments.joblib'), sample_sha256=sha(root / 'training_samples.pkl'),
             reused_original_early_model=str(oldroot / 'segments.joblib')))
        old = pd.read_pickle(path).sort_values(KEY).reset_index(drop=True)
        new = pd.read_pickle(source(2, month, 'forecast')).sort_values(KEY).reset_index(drop=True)
        pd.testing.assert_frame_equal(old, new, check_dtype=False)
        controls.append(month)
    dump(OUT / 'training_controls.json', dict(forecast_controls_exact=controls,
        prior_models_reused=[str(x) for x in pd.period_range('2025-01', LAST, freq='M')]))


def prepare():
    configure()
    specification()
    assert all((OUT / 'daily2' / m / 'metrics.json').exists() for m in NEW_MODELS)
    oldfacts = pd.read_pickle(R34 / 'minute_facts.pkl').set_index(['symbol', 'd1_date'])
    oldbars = pd.read_pickle(R34 / 'first10.pkl')
    oldbars['symbol'] = oldbars.symbol.str[:6]
    bars = {(sym, d0): b for (sym, d0), b in oldbars.groupby(KEY)}
    r.extension.daily_high_low = lambda demand: one.early_high_low(demand, reference)
    r.extension.with_status = one.historical_states
    def enrich(day, demand):
        records, extracts, missing = [], [], []
        for row in demand.itertuples():
            key = (row.symbol, day)
            if key not in oldfacts.index:
                missing.append(row.Index)
                continue
            item = dict(symbol=row.symbol, d1_date=day, **oldfacts.loc[key].to_dict())
            records.append(item)
            if item['audit_ok']:
                b = bars.get((row.symbol, row.d0_date))
                assert b is not None and len(b) == 10
                extracts.append(b)
        if missing:
            fresh = demand.loc[missing]
            a, b = (one.enrich_raw_day(day, fresh, r)
                    if day.startswith('2023') or
                       (day.startswith('2024') and one.raw_minute_path(day).exists())
                    else reference.enrich_day(day, fresh))
            records.extend(a)
            extracts.extend(b)
        return records, extracts
    r.enrich_day = enrich
    r.prepare()
    coverage = json.loads((OUT / 'coverage.json').read_text())
    print('two-month minute coverage', coverage, flush=True)


def select():
    configure()
    f = pd.read_pickle(OUT / 'enriched.pkl')
    calendar = json.loads((PANEL / 'calendar.json').read_text())
    _, oldrules = reference.baseline()
    baseline = {x['month']: x for x in oldrules}
    selected, candidates = [], []
    original_window = r.shape.window
    try:
        r.shape.window = lambda month, valid=2: r.window(month, 2)
        for month in MONTHS:
            group = f[f.model_month.eq(month)]
            parts = []
            for part in ('train', 'valid'):
                frame = group[group.part.eq(part)]
                cutoff = str(pd.Period(r.window(month, 2)[part][1]) + 1) + '-01'
                assert all(calendar[int(i) + 4] < cutoff for i in frame.date_i)
                parts.append(frame[frame.complete].copy())
            chosen = json.loads(json.dumps(r.four.choose_rule(*parts, month, r.shape.rules())))
            chosen['variant'] = 'D2S2'
            if month in baseline:
                for field in ('selected', 'train_base', 'valid_base', 'train_selected', 'valid_selected'):
                    assert chosen[field] == baseline[month][field], (month, field)
            selected.append(chosen)
            for item in r.shape.rules():
                candidates.append(dict(variant='D2S2', month=month, rule=item,
                    train=r.shape.metrics(parts[0], item), valid=r.shape.metrics(parts[1], item)))
            print('rule two-month', month, chosen['selected']['id'], flush=True)
    finally:
        r.shape.window = original_window
    dump(OUT / 'rules_frozen.json', selected)
    dump(OUT / 'rule_candidates.json', candidates)
    dump(OUT / 'selection_receipt.json', dict(existing_baseline_months_exact=len(baseline),
        new_months=[x for x in MONTHS if x not in baseline],
        rules_sha256=sha(OUT / 'rules_frozen.json'),
        candidates_sha256=sha(OUT / 'rule_candidates.json')))


def evaluate():
    configure()
    rules = json.loads((OUT / 'rules_frozen.json').read_text())
    f = pd.read_pickle(OUT / 'enriched.pkl')
    first = pd.read_pickle(OUT / 'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    bars = {(sym, d0): g.sort_values('time') for (sym, d0), g in first.groupby(KEY)}
    calendar = json.loads((PANEL / 'calendar.json').read_text())
    picks, classes = [], []
    for rule in rules:
        month = rule['month']
        q = f[f.model_month.eq(month) & f.part.eq('test')]
        chosen = r.four.delay_same_stock(r.four.chosen(q, rule['selected']), bars)
        chosen['variant'] = 'D2S2'
        chosen['rule_id'] = rule['selected']['id']
        chosen['rule_text'] = rule['selected']['text'].replace('五分', '四分')
        picks.append(chosen)
        for name, group in [('形态前Top3', q[q.eligible]),
                            ('形态通过Top3', q[q.eligible & r.shape.mask(q, rule['selected'])]),
                            ('最终交易', chosen)]:
            known = group[group.observed_3]
            classes.append(dict(variant='D2S2', month=month, group=name, n=len(known),
                hits=int(known.label_3.sum()),
                hit=float(known.label_3.mean()) if len(known) else None))
    signals = pd.concat(picks, ignore_index=True)
    original, _ = reference.baseline()
    key = KEY + ['d1_date', 'entry_time', 'entry', 'd2_close']
    a = original[original.d0_date.ge('2025-05-01')][key].sort_values(KEY).reset_index(drop=True)
    b = signals[signals.d0_date.ge('2025-05-01')][key].sort_values(KEY).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    signals.to_pickle(OUT / 'signals.pkl')
    dump(OUT / 'shape_test_metrics.json', classes)
    scopes = [('完整两月2024起', '2024-03-01', calendar[-1]),
              ('两月新增2024段', '2024-03-01', '2024-12-31'),
              ('两月2025重置', '2025-01-01', calendar[-1]),
              ('共同区间', '2025-05-01', calendar[-1])]
    accounts, monthly, navs, ledgers, excluded = [], [], [], [], []
    for scope, start, end in scopes:
        q = signals[signals.d0_date.ge(start) & signals.d0_date.le(end)]
        result, months, nav, ledger, misses = r.replay(q, calendar,
            start=start, end=end, fee_rate=.001)
        accounts.append(dict(scope=scope, variant='D2S2', **result))
        monthly.extend(dict(scope=scope, variant='D2S2', **x) for x in months)
        navs.append(nav.assign(scope=scope, variant='D2S2'))
        ledgers.append(ledger.assign(scope=scope, variant='D2S2'))
        excluded.extend(dict(scope=scope, variant='D2S2', **x) for x in misses)
        print(scope, result['entries'], result['total_return'], result['max_drawdown'], flush=True)
    old = next(x for x in json.loads((R32 / 'accounts.json').read_text())
               if x['scope'] == '共同区间' and x['variant'] == 'D2S2')
    common = accounts[-1]
    for field in ('entries', 'closed', 'pending', 'final_nav', 'total_return', 'max_drawdown', 'sharpe'):
        assert np.isclose(common[field], old[field], atol=1e-8, rtol=0), field
    dump(OUT / 'accounts.json', accounts)
    dump(OUT / 'monthly.json', monthly)
    dump(OUT / 'exclusions.json', excluded)
    pd.concat(navs, ignore_index=True).to_pickle(OUT / 'nav.pkl')
    pd.concat(ledgers, ignore_index=True).to_pickle(OUT / 'ledger.pkl')
    panel = pd.read_pickle(PANEL / 'panel.pkl')
    daily = []
    for month in MONTHS:
        for part in ('train', 'valid', 'forecast'):
            frame = pd.read_pickle(source(2, month, part)).merge(panel, on=KEY, validate='one_to_one')
            daily.append(dict(daily_validation=2, month=month, part=part, **classify(frame)))
    dump(OUT / 'daily_metrics.json', daily)
    for nav, ledger in zip(navs, ledgers):
        for item in nav.itertuples():
            bought = ledger[ledger.entry_date.le(item.date)]
            sold = ledger[ledger.exit_date.fillna('9999').le(item.date)]
            held = bought[bought.exit_date.fillna('9999').gt(item.date)]
            cash = 1e6 - bought.cost.sum() + sold.proceeds.sum()
            assert np.isclose(item.cash, cash, atol=1e-7, rtol=0)
            assert np.isclose(item.nav, cash + (held.shares * held.d1_close).sum(), atol=1e-7, rtol=0)
        closed = ledger[ledger.exit_date.notna()]
        assert closed.exit_date.eq(closed.entry_date.map(lambda day: calendar[calendar.index(day) + 1])).all()
        assert closed.sell.eq(closed.d2_close).all()
        assert np.allclose(closed.return_, closed.sell / closed.buy * .999 / 1.001 - 1,
                           atol=1e-12, rtol=0)
    dump(OUT / 'verification.json', dict(existing_signal_exact=len(a),
        existing_common_account_exact=True, fourk_frozen_before_account=True,
        d2_price_and_fees_exact=True, cash_reconciliation=True))


def audit():
    from scripts.automl_b2 import evaluate_morning
    configure()
    panel = pd.read_pickle(PANEL / 'panel.pkl')
    calendar = json.loads((PANEL / 'calendar.json').read_text())
    reloads = []
    for month in HISTORY:
        root = OUT / 'daily2' / month if month < '2025-01' else R18 / month
        bundle = joblib.load(root / 'segments.joblib')
        subset = panel[panel.d0_date.str.startswith(month)]
        ranked = rank(subset, bundle['model'].predict_proba(xmatrix(subset, FEATURES['segments']))[:, 1])
        saved = pd.read_pickle(source(2, month, 'forecast'))
        joined = ranked[KEY + ['score', 'd0_rank']].merge(saved, on=KEY,
            validate='one_to_one', suffixes=('_new', '_saved'))
        diff = float((joined.score_new - joined.score_saved).abs().max())
        assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_saved).all()
        if month < '2025-01':
            fit = pd.read_pickle(root / 'training_samples.pkl')
            expected = r.block(panel, calendar, *r.window(month, 2)['train'])
            expected = expected[expected.activated & expected.observed_3]
            pd.testing.assert_frame_equal(fit[KEY].reset_index(drop=True),
                                          expected[KEY].reset_index(drop=True))
        reloads.append(dict(month=month, scored=len(joined), max_diff=diff))
    dump(OUT / 'model_reload_verification.json', reloads)
    enriched = pd.read_pickle(OUT / 'enriched.pkl')
    first = pd.read_pickle(OUT / 'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    assert not first.duplicated(KEY + ['time']).any()
    bars = {(s, d0): g.sort_values('time') for (s, d0), g in first.groupby(KEY)}
    demand = pd.read_pickle(OUT / 'minute_demand.pkl').set_index(KEY)
    for key, b in bars.items():
        row = demand.loc[key]
        changed = b.copy()
        changed.loc[changed.index[4:], ['open', 'high', 'low', 'close']] = 9999.
        assert r.four.first_features(b, row.d1_preclose, row.d1_cap, 4) == \
               r.four.first_features(changed, row.d1_preclose, row.d1_cap, 4)
    rules = {x['month']: x['selected'] for x in json.loads((OUT / 'rules_frozen.json').read_text())}
    decisions, selected = [], []
    for day, group in enriched[enriched.part.eq('test')].groupby('d1_date'):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=x.symbol, selection_rank=int(x.d0_rank),
            preclose=float(x.d1_preclose), cap=float(x.d1_cap), ordinary=bool(x.eligible))
            for x in group.itertuples()]
        sources = {x.symbol: bars.get((x.symbol, d0), pd.DataFrame()) for x in group.itertuples()}
        states = evaluate_morning(picks, sources, day,
            datetime.fromisoformat(day + ' 09:40:00'), rules[d0[:7]])
        for item in states:
            decisions.append(dict(d0_date=d0, d1_date=day, **item))
            if item['actionable']:
                selected.append((d0, item['symbol6'], item['entry_bar_end'][11:16],
                                 float(item['entry_price'])))
    signals = pd.read_pickle(OUT / 'signals.pkl')
    expected = [(x.d0_date, x.symbol, x.entry_time, float(x.entry)) for x in signals.itertuples()]
    assert sorted(selected) == sorted(expected)
    pd.DataFrame(decisions).to_pickle(OUT / 'execution_decisions.pkl')
    checks = json.loads((OUT / 'verification.json').read_text())
    checks.update(model_reload_count=len(reloads), model_score_max_diff=max(x['max_diff'] for x in reloads),
        production_evaluator_exact=len(selected), candidate_decisions=len(decisions),
        four_bar_no_future_feature_checks=len(bars))
    dump(OUT / 'verification.json', checks)
    print('two-month audit', checks, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['train', 'prepare', 'select', 'evaluate', 'audit'])
    args = parser.parse_args()
    globals()[args.stage]()
