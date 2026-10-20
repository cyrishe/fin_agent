"""Extend frozen B2 six-train/two-valid history with 2021 and early 2022 bars.

Only May 2022--April 2023 B2 test months are new; May 2023 onward reuses
the previously audited R41 signals, models and selected four-K rules.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts import experiment_b2_full_2024_r34 as r34
from scripts import experiment_b2_two_month_2024_r35 as r35
from scripts import experiment_b2_validation_r31 as r
from scripts import experiment_b2_validation_full_r32 as reference
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.experiment_limit_rebound_segments_r18 import FEATURES, RAW, segment_one
from scripts.experiment_limit_rebound_short_mixed_r12 import add_features, enrich_outcomes, make_model, xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import classify, rank
from scripts.replay_limit_rebound_cash_r23 import dump, sha


OUT = Path('outputs/stock_automl/b2_2021_minute_r60_20261020')
REPORT = Path('docs/stock_automl_runs/20261020_b2_2021_minutes_r60')
MINUTES = Path('/Volumes/ext/tushare_market_data')
PANEL = OUT
R41 = Path('outputs/stock_automl/b2_2022_minute_r41_20261019')
R35 = Path('outputs/stock_automl/b2_two_month_2024_r35_20261019')
R34 = Path('outputs/stock_automl/b2_full_2024_r34_20261019')
R18 = r35.R18
KEY = ['symbol', 'd0_date']
NEW_TEST = [str(m) for m in pd.period_range('2022-05', '2023-04', freq='M')]
NEW_MODELS = [str(m) for m in pd.period_range('2021-09', '2022-08', freq='M')]
START = '2022-05-01'
END = '2026-09-30'


def sources():
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    old = json.loads((R41/'daily_sources.json').read_text())
    prior = {x['month']: x for x in old}
    records = []
    for month in pd.period_range('2020-11', '2021-10', freq='M'):
        name = str(month)
        path = OUT/'price_months'/f'price_{name}.csv.gz'
        if not path.exists():
            with db_connection() as conn:
                data = query(conn,
                    'SELECT trade_date,stk_code,preclose,open,high,low,close,'
                    'volume,amount,turn_ratio,is_limit_price,update_time '
                    'FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s',
                    (str(month.start_time.date()), str(month.end_time.date())))
                conn.rollback()
            assert len(data) > 55_000, (name, len(data))
            path.parent.mkdir(parents=True, exist_ok=True)
            data.to_csv(path, index=False, compression='gzip')
        check = pd.read_csv(path, usecols=['trade_date','stk_code'],
                            dtype={'trade_date': str, 'stk_code': str})
        assert not check.duplicated(['trade_date','stk_code']).any()
        records.append(dict(month=name, path=str(path), sha256=sha(path),
                            rows=len(check), dates=sorted(check.trade_date.unique())))
        print('daily', name, len(check), flush=True)
    for name in sorted(prior):
        item = prior[name]
        assert sha(Path(item['path'])) == item['sha256']
        records.append(item)
    december = {x['month']: x for x in json.loads((R34/'daily_sources.json').read_text())['months']}['2022-12']
    assert sha(Path(december['path'])) == december['sha256']
    records.append(december)
    expected = sorted({day for item in records if '2021-01' <= item['month'] <= '2022-12'
                       for day in item['dates']})
    minute_days = sorted(f'{p.name[:4]}-{p.name[4:6]}-{p.name[6:8]}'
                         for year in ('2021', '2022')
                         for p in (MINUTES/year).glob(f'{year}????.csv.gz'))
    assert minute_days == expected, (len(minute_days), len(expected), sorted(set(expected)-set(minute_days))[:5])
    minutes = []
    for day in minute_days:
        path = MINUTES/day[:4]/f'{day.replace("-", "")}.csv.gz'
        meta = json.loads(path.with_suffix('').with_suffix('.json').read_text())
        h = sha(path)
        assert meta['sha256'] == h and meta['trade_date'] == day.replace('-', '')
        assert meta['bars'] > 700_000 and meta['codes_with_bars'] > 3_000
        minutes.append(dict(day=day, path=str(path), sha256=h,
                            bars=meta['bars'], symbols=meta['codes_with_bars']))
    dump(OUT/'daily_sources.json', records)
    dump(OUT/'minute_sources.json', minutes)
    print('minutes', len(minutes), minute_days[0], minute_days[-1], flush=True)


def panel():
    records = json.loads((OUT/'daily_sources.json').read_text())
    assert all(sha(Path(x['path'])) == x['sha256'] for x in records)
    cols = ['trade_date','stk_code','preclose','open','high','low','close',
            'volume','amount','turn_ratio','is_limit_price']
    raw = pd.concat([pd.read_csv(x['path'], usecols=cols,
                                 dtype={'trade_date': str, 'stk_code': str})
                     for x in records], ignore_index=True)
    daily = prepare_daily(raw)
    old_calendar = json.loads((R41/'calendar.json').read_text())
    calendar = sorted(set(daily.trade_date) | set(old_calendar))
    index = {day: i for i, day in enumerate(calendar)}
    shift = index[old_calendar[0]]
    daily['date_i'] = daily.trade_date.map(index)
    (OUT/'historical_st.csv.gz').unlink(missing_ok=True)
    early, quality = build_panel(daily, OUT, score_all_d0=True,
                                 d0_start='2021-01-01')
    early = early[early.d0_date.lt('2022-01-01')].copy()
    early = add_features(early, daily)
    early = enrich_outcomes(early, daily)
    early['first_i'] = early.first_limit_date.map(index)
    targets = {sym: group for sym, group in early.groupby('symbol', sort=False)}
    features, audit, used = [], [], []
    for sym, group in daily.groupby('symbol', sort=False):
        if sym not in targets:
            continue
        pos = {day: i for i, day in enumerate(group.trade_date)}
        picked = set()
        for row in targets[sym].itertuples():
            z, meta, positions = segment_one(group, pos[row.first_limit_date],
                                              pos[row.d0_date])
            assert meta['raw_board_days'] == row.board_streak_days
            features.append(dict(symbol=sym, d0_date=row.d0_date, **z))
            audit.append(dict(symbol=sym, d0_date=row.d0_date, **meta))
            picked.update(positions)
        used.extend(group.index[list(picked)])
    early = early.merge(pd.DataFrame(features), on=KEY, validate='one_to_one')
    old = pd.read_pickle(R41/'panel.pkl').copy()
    assert set(early) == set(old), (sorted(set(early)-set(old)), sorted(set(old)-set(early)))
    for col in ['date_i','d1_i','first_i']:
        old[col] += shift
    full = pd.concat([early[old.columns], old], ignore_index=True)
    assert full.d0_date.min()[:7] == '2021-01'
    assert not full.duplicated(KEY).any()
    assert full.d0_date.map(index).eq(full.date_i).all()
    assert full.first_limit_date.map(index).eq(full.first_i).all()
    full.to_pickle(OUT/'panel.pkl')
    dump(OUT/'calendar.json', calendar)
    pd.DataFrame(audit).to_pickle(OUT/'early_segment_audit.pkl')
    daily.loc[sorted(set(used)), RAW].to_pickle(OUT/'early_feature_source_rows.pkl')
    dump(OUT/'panel_manifest.json', dict(early_rows=len(early),
        reused_rows=len(old), total_rows=len(full), shift_days=shift,
        early_quality=quality, panel_sha256=sha(OUT/'panel.pkl'),
        calendar_sha256=sha(OUT/'calendar.json'),
        reused_panel_sha256=sha(R41/'panel.pkl')))
    print('panel', len(early), '+', len(old), '=', len(full), flush=True)


def specification():
    value = dict(experiment='R60 B2 2021 minute extension',
        new_test_months=NEW_TEST, first_test=NEW_TEST[0], last_test=END[:7],
        daily_training_months=6, daily_validation_months=2,
        shape_training_months=6, shape_validation_months=2,
        first_daily_forecast='2021-09',
        daily_panel_sha256=sha(PANEL/'panel.pkl'),
        calendar_sha256=sha(PANEL/'calendar.json'),
        minute_sources_sha256=sha(OUT/'minute_sources.json'),
        old_R41_signal_sha256=sha(R41/'signals.pkl'),
        model='R18 RF51 exact family, weights, seed and sklearn1.7.1',
        shape='R35 original four K rule catalog and train/valid selection',
        execution='R35 original D0 Top3, D1 four K, body > -8%, same-stock next qualified minute open, D2 close, 0.1% each side',
        controls='R41 signals 2023-05 onward preserved exactly; new months only 2022-05 through 2023-04',
        production_changed=False, historical_research_not_blind=True)
    path = OUT/'specification.json'
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def source(dv, month, part):
    assert dv == 2
    if month < '2022-09':
        root = OUT/'daily2'/month
    else:
        return r41_source(month, part)
    return root/f'segments_{part}_scores.pkl'


def r41_source(month, part):
    if month < '2023-07':
        root = R41/'daily2'/month
    elif month < '2025-01':
        root = R35/'daily2'/month
    else:
        root = R18/month
    return root/f'segments_{part}_scores.pkl'


def configure():
    reference.OUT = OUT
    reference.BASE = PANEL
    r.OUT = OUT
    r.BASE = PANEL
    r.MONTHS = NEW_TEST
    r.HISTORY = [str(m) for m in pd.period_range('2021-09', '2023-04', freq='M')]
    r.VARIANTS = {'D2S2': (2,2)}
    r.spec = specification
    r.source = source
    return r


def train_one(month):
    assert sklearn.__version__ == '1.7.1'
    panel = pd.read_pickle(PANEL/'panel.pkl')
    calendar = json.loads((PANEL/'calendar.json').read_text())
    window = r.window(month, 2)
    train = r.block(panel, calendar, *window['train'])
    valid = r.block(panel, calendar, *window['valid'], purge_events=False)
    test = panel[panel.d0_date.str.startswith(month)]
    assert not set(zip(train.symbol, train.first_limit_date)) & set(zip(valid.symbol, valid.first_limit_date))
    fit = train[train.activated & train.observed_3].copy()
    assert len(fit) > 100, (month, len(fit))
    root = OUT/'daily2'/month
    root.mkdir(parents=True, exist_ok=True)
    if (root/'metrics.json').exists():
        return month
    model = make_model('rf')
    model.steps[-1][1].n_jobs = 1
    model.fit(xmatrix(fit, FEATURES['segments']), fit.label_3,
              randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
    fit.to_pickle(root/'training_samples.pkl')
    bundle = dict(model=model, features=FEATURES['segments'], month=month,
                  window=window, sklearn=sklearn.__version__,
                  training_keys=fit[KEY+['first_limit_date']].to_dict('records'),
                  spec_sha256=sha(OUT/'specification.json'))
    joblib.dump(bundle, root/'segments.joblib', compress=3)
    restored = joblib.load(root/'segments.joblib')['model']
    assert np.array_equal(model.predict_proba(xmatrix(fit.iloc[:50], FEATURES['segments'])),
                          restored.predict_proba(xmatrix(fit.iloc[:50], FEATURES['segments'])))
    metrics = {}
    for part, frame in [('train',train),('valid',valid),('forecast',test)]:
        scored = rank(frame, restored.predict_proba(xmatrix(frame, FEATURES['segments']))[:,1])
        scored[KEY+['score','d0_rank']].to_pickle(source(2,month,part))
        metrics[part] = classify(scored)
    dump(root/'metrics.json', dict(window=window, fit_rows=len(fit),
        metrics=metrics, model_sha256=sha(root/'segments.joblib'),
        sample_sha256=sha(root/'training_samples.pkl')))
    return month


def train():
    configure()
    specification()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(train_one, m) for m in NEW_MODELS]):
            print('trained', future.result(), flush=True)


def prepare():
    configure()
    specification()
    assert all((OUT/'daily2'/m/'metrics.json').exists() for m in NEW_MODELS)
    oldfacts = pd.read_pickle(R41/'minute_facts.pkl').set_index(['symbol','d1_date']).sort_index()
    assert not oldfacts.index.duplicated().any()
    oldbars = pd.read_pickle(R41/'first10.pkl')
    oldbars['symbol'] = oldbars.symbol.str[:6]
    bars = {(s,d0):b for (s,d0),b in oldbars.groupby(KEY)}
    def daily_high_low(demand):
        wanted_months = set(demand.d1_date.dropna().str[:7])
        wanted_symbols = set(demand.symbol)
        local = json.loads((OUT/'daily_sources.json').read_text())
        old = json.loads((R34/'daily_sources.json').read_text())['months']
        by_month = {x['month']: x for x in old}
        by_month.update({x['month']: x for x in local})
        parts = []
        for month in sorted(wanted_months):
            item = by_month[month]
            path = Path(item['path'])
            assert sha(path) == item['sha256']
            part = pd.read_csv(path, dtype={'stk_code': str, 'trade_date': str},
                               usecols=['stk_code','trade_date','high','low'])
            part['symbol'] = part.stk_code.str[:6]
            parts.append(part[part.symbol.isin(wanted_symbols)][
                ['symbol','trade_date','high','low']].rename(columns={
                    'trade_date':'d1_date','high':'d1_high','low':'d1_low'}))
        result = pd.concat(parts, ignore_index=True)
        assert not result.duplicated(['symbol','d1_date']).any()
        return result
    r.extension.daily_high_low = daily_high_low
    r.extension.with_status = r34.historical_states
    original_path = r34.raw_minute_path
    r34.raw_minute_path = lambda day: (MINUTES/day[:4]/f'{day.replace("-", "")}.csv.gz'
                                        if day.startswith(('2021','2022')) else original_path(day))
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
                b = bars.get((row.symbol,row.d0_date))
                assert b is not None and len(b) == 10
                extracts.append(b)
        if missing:
            frame = demand.loc[missing]
            if day.startswith(('2021','2022','2023')):
                a,b = r34.enrich_raw_day(day, frame, r)
            else:
                a,b = reference.enrich_day(day, frame)
            records.extend(a)
            extracts.extend(b)
        return records, extracts
    r.enrich_day = enrich
    try:
        r.prepare()
    finally:
        r34.raw_minute_path = original_path
    coverage = json.loads((OUT/'coverage.json').read_text())
    print('coverage', coverage, flush=True)


def select():
    configure()
    f = pd.read_pickle(OUT/'enriched.pkl')
    calendar = json.loads((PANEL/'calendar.json').read_text())
    selected, candidates = [], []
    original_window = r.shape.window
    try:
        r.shape.window = lambda month, valid=2: r.window(month, 2)
        for month in NEW_TEST:
            group = f[f.model_month.eq(month)]
            parts = []
            for part in ('train','valid'):
                frame = group[group.part.eq(part)]
                cutoff = str(pd.Period(r.window(month,2)[part][1])+1)+'-01'
                assert all(calendar[int(i)+4] < cutoff for i in frame.date_i)
                parts.append(frame[frame.complete].copy())
            chosen = json.loads(json.dumps(r.four.choose_rule(*parts, month, r.shape.rules())))
            chosen['variant'] = 'D2S2'
            selected.append(chosen)
            for rule in r.shape.rules():
                candidates.append(dict(variant='D2S2',month=month,rule=rule,
                    train=r.shape.metrics(parts[0],rule),valid=r.shape.metrics(parts[1],rule)))
            print('rule', month, chosen['selected']['id'], flush=True)
    finally:
        r.shape.window = original_window
    dump(OUT/'rules_frozen.json', selected)
    dump(OUT/'rule_candidates.json', candidates)


def evaluate():
    configure()
    rules = json.loads((OUT/'rules_frozen.json').read_text())
    f = pd.read_pickle(OUT/'enriched.pkl')
    first = pd.read_pickle(OUT/'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    bars = {(s,d0):g.sort_values('time') for (s,d0),g in first.groupby(KEY)}
    picks, classes = [], []
    for item in rules:
        month = item['month']
        q = f[f.model_month.eq(month) & f.part.eq('test')]
        chosen = r.four.delay_same_stock(r.four.chosen(q,item['selected']),bars)
        chosen['variant'] = 'D2S2'
        chosen['rule_id'] = item['selected']['id']
        chosen['rule_text'] = item['selected']['text'].replace('五分','四分')
        picks.append(chosen)
        for name, group in [('形态前Top3',q[q.eligible]),
                            ('形态通过Top3',q[q.eligible & r.shape.mask(q,item['selected'])]),
                            ('最终交易',chosen)]:
            known = group[group.observed_3]
            classes.append(dict(month=month,group=name,n=len(known),
                hits=int(known.label_3.sum()),
                hit=float(known.label_3.mean()) if len(known) else None))
        print('selected',month,len(chosen),flush=True)
    early = pd.concat(picks,ignore_index=True)
    old = pd.read_pickle(R41/'signals.pkl')
    assert early.d0_date.lt('2023-05-01').all()
    assert old.d0_date.ge('2023-05-01').all()
    combined = pd.concat([early,old],ignore_index=True)
    assert not combined.d1_date.duplicated().any()
    early.to_pickle(OUT/'new_signals.pkl')
    combined.to_pickle(OUT/'signals.pkl')
    dump(OUT/'shape_test_metrics.json',classes)
    dump(OUT/'signal_manifest.json',dict(
        new_months=NEW_TEST,new_signal_count=len(early),
        old_signal_count=len(old),combined_signal_count=len(combined),
        old_signal_sha256=sha(R41/'signals.pkl'),
        new_signal_sha256=sha(OUT/'new_signals.pkl'),
        combined_signal_sha256=sha(OUT/'signals.pkl')))


def audit():
    from scripts.automl_b2 import evaluate_morning
    configure()
    panel = pd.read_pickle(PANEL/'panel.pkl')
    model_checks = []
    for month in NEW_MODELS:
        root = OUT/'daily2'/month
        bundle = joblib.load(root/'segments.joblib')
        sample = panel[panel.d0_date.str.startswith(month)]
        fresh = rank(sample, bundle['model'].predict_proba(
            xmatrix(sample,FEATURES['segments']))[:,1])
        old = pd.read_pickle(root/'segments_forecast_scores.pkl')
        joined = fresh[KEY+['score','d0_rank']].merge(old,on=KEY,
            validate='one_to_one',suffixes=('_new','_old'))
        diff = float((joined.score_new-joined.score_old).abs().max())
        assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_old).all()
        model_checks.append(dict(month=month,rows=len(joined),max_diff=diff,
                                 model_sha256=sha(root/'segments.joblib')))
    enriched = pd.read_pickle(OUT/'enriched.pkl')
    first = pd.read_pickle(OUT/'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    bars = {(s,d0):g.sort_values('time') for (s,d0),g in first.groupby(KEY)}
    rules = {x['month']:x['selected'] for x in json.loads((OUT/'rules_frozen.json').read_text())}
    selected, decisions = [], []
    for day, group in enriched[enriched.part.eq('test')].groupby('d1_date'):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=x.symbol,selection_rank=int(x.d0_rank),
                      preclose=float(x.d1_preclose),cap=float(x.d1_cap),
                      ordinary=bool(x.eligible)) for x in group.itertuples()]
        sources = {x.symbol:bars.get((x.symbol,d0),pd.DataFrame())
                   for x in group.itertuples()}
        states = evaluate_morning(picks,sources,day,
            datetime.fromisoformat(day+' 09:40:00'),rules[d0[:7]])
        for state in states:
            decisions.append(dict(d0_date=d0,d1_date=day,**state))
            if state['actionable']:
                selected.append((d0,state['symbol6'],
                    state['entry_bar_end'][11:16],float(state['entry_price'])))
    expected = [(x.d0_date,x.symbol,x.entry_time,float(x.entry))
                for x in pd.read_pickle(OUT/'new_signals.pkl').itertuples()]
    assert sorted(selected) == sorted(expected)
    pd.DataFrame(decisions).to_pickle(OUT/'execution_decisions.pkl')
    dump(OUT/'verification.json',dict(model_checks=model_checks,
        model_max_score_diff=max(x['max_diff'] for x in model_checks),
        new_signals=len(expected),production_evaluator_exact=len(selected),
        candidate_decisions=len(decisions),
        minute_early_coverage=json.loads((OUT/'coverage.json').read_text())))
    print('audit',len(model_checks),'models',len(selected),'signals',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage',choices=['sources','panel','train','prepare','select','evaluate','audit'])
    args = parser.parse_args()
    globals()[args.stage]()
