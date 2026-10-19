"""Extend the frozen R32 B2 6+1 study to January and February 2025.

The only newly fitted daily models are June/July 2024. Earlier history is
stored separately; all later daily models, rules and execution remain frozen.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_limit_rebound_short_mixed_r12 import add_features, enrich_outcomes
from scripts.experiment_limit_rebound_segments_r18 import RAW, segment_one
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT = Path('outputs/stock_automl/b2_early_2025_r33_20261019')
R18 = Path('outputs/stock_automl/limit_rebound_segments_r18_20261015')
R32 = Path('outputs/stock_automl/b2_validation_full_r32_20261019')
R12 = Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
CALENDAR = Path('/Volumes/ext/tushare_market_data/calendar_20230101_20231231.json')
KEY = ['symbol', 'd0_date']


def fetch_one(month: str) -> dict:
    path = OUT / 'price_months' / f'price_{month}.csv.gz'
    if not path.exists():
        period = pd.Period(month, freq='M')
        with db_connection() as conn:
            frame = query(conn, 'SELECT trade_date,stk_code,preclose,open,high,low,close,'
                          'volume,amount,turn_ratio,is_limit_price,update_time '
                          'FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s',
                          (str(period.start_time.date()), str(period.end_time.date())))
            conn.rollback()
        if frame.empty:
            raise ValueError(f'No daily source for {month}')
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, compression='gzip')
    p = pd.read_csv(path, dtype={'stk_code': str, 'trade_date': str},
                    usecols=['trade_date', 'stk_code'])
    return dict(month=month, path=str(path), sha256=sha(path), rows=len(p),
                dates=sorted(p.trade_date.unique()))


def sources() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    months = [str(p) for p in pd.period_range('2023-01', '2023-11', freq='M')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_one, month): month for month in months}
        records = []
        for future in as_completed(futures):
            result = future.result()
            records.append(result)
            print('daily source', result['month'], result['rows'], flush=True)
    december = R12 / 'price_months/price_2023-12.csv.gz'
    p = pd.read_csv(december, dtype={'stk_code': str, 'trade_date': str},
                    usecols=['trade_date', 'stk_code'])
    records.append(dict(month='2023-12', path=str(december), sha256=sha(december),
                        rows=len(p), dates=sorted(p.trade_date.unique())))
    records.sort(key=lambda x: x['month'])
    calendar = json.loads(CALENDAR.read_text())
    if isinstance(calendar, dict):
        print('calendar keys', list(calendar), flush=True)
    db_dates = sorted({d for row in records for d in row['dates']})
    assert len(db_dates) == 242 and db_dates[0] == '2023-01-03' and db_dates[-1] == '2023-12-29'
    # Verify each month against the independent Tushare archive filenames.
    minute_dates = sorted(p.stem.removesuffix('.csv') for p in Path('/Volumes/ext/tushare_market_data/2023').glob('*.csv.gz'))
    assert [d.replace('-', '') for d in db_dates] == minute_dates
    dump(OUT / 'daily_sources.json', dict(months=records, trading_days=len(db_dates),
        minute_archive_days=len(minute_dates), source_calendar=str(CALENDAR)))
    print('verified 2023 source days', len(db_dates), flush=True)


def panel() -> None:
    manifest = json.loads((OUT / 'daily_sources.json').read_text())
    paths = [Path(x['path']) for x in manifest['months']]
    assert all(sha(p) == x['sha256'] for p, x in zip(paths, manifest['months']))
    january = R12 / 'price_months/price_2024-01.csv.gz'
    source_manifest = json.loads((R18 / 'source_manifest.json').read_text())
    assert any(x['path'] == str(january) and x['sha256'] == sha(january)
               for x in source_manifest['raw_sources'])
    cols = ['trade_date', 'stk_code', 'preclose', 'open', 'high', 'low', 'close',
            'volume', 'amount', 'turn_ratio', 'is_limit_price']
    raw = pd.concat([pd.read_csv(p, dtype={'stk_code': str, 'trade_date': str}, usecols=cols)
                     for p in paths + [january]], ignore_index=True)
    daily = prepare_daily(raw)
    old_calendar = json.loads((R18 / 'calendar.json').read_text())
    calendar = sorted(set(daily.trade_date) | set(old_calendar))
    assert len(calendar) == len(old_calendar) + 221
    mapping = {d: i for i, d in enumerate(calendar)}
    daily['date_i'] = daily.trade_date.map(mapping)
    early, quality = build_panel(daily, OUT, score_all_d0=True, d0_start='2023-11-01')
    early = early[early.d0_date.le('2023-12-29')].copy()
    early = add_features(early, daily)
    early = enrich_outcomes(early, daily)
    early['first_i'] = early.first_limit_date.map(mapping)
    targets = {sym: f for sym, f in early.groupby('symbol', sort=False)}
    features, audit, used = [], [], []
    for sym, group in daily.groupby('symbol', sort=False):
        if sym not in targets:
            continue
        positions = {d: i for i, d in enumerate(group.trade_date)}
        ix = set()
        for row in targets[sym].itertuples():
            z, meta, positions_used = segment_one(group, positions[row.first_limit_date],
                                                    positions[row.d0_date])
            assert meta['raw_board_days'] == row.board_streak_days
            features.append(dict(symbol=sym, d0_date=row.d0_date, **z))
            audit.append(dict(symbol=sym, d0_date=row.d0_date, **meta))
            ix.update(positions_used)
        used.extend(group.index[list(ix)])
    early = early.merge(pd.DataFrame(features), on=KEY, validate='one_to_one')
    old = pd.read_pickle(R18 / 'panel.pkl').copy()
    for col in ['date_i', 'd1_i', 'first_i']:
        old[col] = old[col] + 221
    assert old.d0_date.min() == '2024-01-02'
    assert not set(map(tuple, early[KEY].to_numpy())) & set(map(tuple, old[KEY].to_numpy()))
    assert set(early) == set(old), (set(early) - set(old), set(old) - set(early))
    merged = pd.concat([early[old.columns], old], ignore_index=True)
    assert not merged.duplicated(KEY).any()
    assert merged.d0_date.map(mapping).eq(merged.date_i).all()
    assert merged.first_limit_date.map(mapping).eq(merged.first_i).all()
    merged.to_pickle(OUT / 'panel.pkl')
    dump(OUT / 'calendar.json', calendar)
    pd.DataFrame(audit).to_pickle(OUT / 'early_segment_audit.pkl')
    daily.loc[sorted(set(used)), RAW].to_pickle(OUT / 'early_feature_source_rows.pkl')
    dump(OUT / 'panel_manifest.json', dict(early_rows=len(early), old_rows=len(old),
        total_rows=len(merged), early_quality=quality, old_panel_sha256=sha(R18 / 'panel.pkl'),
        panel_sha256=sha(OUT / 'panel.pkl'), calendar_sha256=sha(OUT / 'calendar.json'),
        new_daily_source=manifest, january_daily_sha256=sha(january)))
    print('panel', len(early), '+', len(old), '=', len(merged), flush=True)


def train() -> None:
    from scripts import experiment_b2_validation_r31 as r
    r.BASE = OUT
    r.OUT = OUT
    r.HISTORY = ['2024-06', '2024-07']
    # R31's full spec is specific to R31; freeze the R33 contract separately.
    spec = dict(experiment='R33 B2 early 2025 one-month extension',
        months=['2025-01', '2025-02'], daily_models=['2024-06', '2024-07'],
        train_months=6, valid_months=1, source_panel=sha(OUT / 'panel.pkl'),
        model='R32 RF51 exact family, balanced weights, sklearn1.7.1',
        no_future_test_selection=True, production_changed=False)
    dump(OUT / 'specification.json', spec)
    # train_one uses r.source, so its scores and training samples remain in OUT.
    for month in r.HISTORY:
        print('trained', r.train_one(month), flush=True)


def configure():
    from scripts import experiment_b2_validation_full_r32 as previous
    from scripts import experiment_b2_validation_r31 as r
    previous.OUT = OUT
    previous.BASE = OUT
    previous.MONTHS = [str(m) for m in pd.period_range('2025-01', '2026-10', freq='M')]
    previous.HISTORY = [str(m) for m in pd.period_range('2024-06', '2026-10', freq='M')]
    previous.COMMON = [str(m) for m in pd.period_range('2025-05', '2026-10', freq='M')]
    r.OUT = OUT
    r.BASE = OUT
    r.MONTHS = previous.MONTHS
    r.HISTORY = previous.HISTORY
    r.VARIANTS = {'D1S1': (1, 1)}
    r.spec = lambda: json.loads((OUT / 'specification.json').read_text())
    r.source = lambda dv, month, part: (
        (R18 / month) if dv == 2 else (OUT / 'daily1' / month)
    ) / f'segments_{part}_scores.pkl'
    return previous, r


def prepare() -> None:
    previous, r = configure()
    assert (OUT / 'daily1/2024-06/metrics.json').exists()
    assert (OUT / 'daily1/2024-07/metrics.json').exists()
    controls = []
    for month in [str(m) for m in pd.period_range('2024-08', '2026-10', freq='M')]:
        source = R32 / 'daily1' / month
        target = OUT / 'daily1' / month
        assert (source / 'metrics.json').exists(), source
        target.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            dest = target / path.name
            if not dest.exists():
                shutil.copy2(path, dest)
            assert sha(path) == sha(dest), (path, dest)
        controls.append(dict(month=month, source=str(source),
                             model_sha256=sha(source / 'segments.joblib')))
    dump(OUT / 'reused_models.json', controls)
    # R32's audited rows and first-ten-minute evidence are immutable controls.
    # Read only the genuinely new early candidate minutes from the source archive.
    cached = pd.read_pickle(R32 / 'minute_facts.pkl').set_index(['symbol', 'd1_date'])
    oldbars = pd.read_pickle(R32 / 'first10.pkl')
    oldbars['symbol'] = oldbars.symbol.str[:6]
    bar_lookup = {(sym, d0): b for (sym, d0), b in oldbars.groupby(KEY)}
    def cached_enrich(day, demand):
        found, bars, missing = [], [], []
        for row in demand.itertuples():
            key = (row.symbol, day)
            if key not in cached.index:
                missing.append(row.Index)
                continue
            z = dict(symbol=row.symbol, d1_date=day, **cached.loc[key].to_dict())
            found.append(z)
            if z['audit_ok']:
                b = bar_lookup.get((row.symbol, row.d0_date))
                assert b is not None and len(b) == 10
                bars.append(b)
        if missing:
            fresh, newbars = previous.enrich_day(day, demand.loc[missing])
            found.extend(fresh)
            bars.extend(newbars)
        return found, bars
    r.enrich_day = cached_enrich
    r.prepare()
    assert len(pd.read_pickle(OUT / 'stage_samples.pkl')) > len(pd.read_pickle(R32 / 'stage_samples.pkl'))


def select() -> None:
    previous, _ = configure()
    previous.select()
    old = {x['month']: x for x in json.loads((R32 / 'rules_frozen.json').read_text())}
    new = {x['month']: x for x in json.loads((OUT / 'rules_frozen.json').read_text())}
    assert set(old) <= set(new)
    for month, prior in old.items():
        for field in ['selected', 'train_base', 'valid_base', 'train_selected', 'valid_selected']:
            assert new[month][field] == prior[field], (month, field)
    dump(OUT / 'early_rule_controls.json', dict(existing_months=len(old),
        all_existing_rules_and_selection_metrics_exact=True,
        new={m: new[m] for m in ['2025-01', '2025-02']}))


def evaluate() -> None:
    previous, r = configure()
    previous.evaluate()
    old = pd.read_pickle(R32 / 'signals.pkl')
    new = pd.read_pickle(OUT / 'signals.pkl')
    key = KEY + ['d1_date', 'entry_time', 'entry', 'd2_close']
    a = old[old.variant.eq('D1S1')][key].sort_values(KEY).reset_index(drop=True)
    b = new[new.variant.eq('D1S1') & new.d0_date.ge('2025-03-01')][key].sort_values(KEY).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    cal = json.loads((OUT / 'calendar.json').read_text())
    one = new[new.variant.eq('D1S1')]
    accounts = json.loads((OUT / 'accounts.json').read_text())
    months = json.loads((OUT / 'monthly.json').read_text())
    exits = json.loads((OUT / 'exclusions.json').read_text())
    nav = pd.read_pickle(OUT / 'nav.pkl')
    ledger = pd.read_pickle(OUT / 'ledger.pkl')
    for scope, start, end in [
        ('一月全期含1-2月', '2025-01-01', cal[-1]),
        ('新增1-2月单独', '2025-01-01', '2025-02-28'),
    ]:
        picks = one[one.d0_date.ge(start) & one.d0_date.le(end)]
        result, monthly, n, l, excluded = r.replay(picks, cal, start=start, end=end, fee_rate=.001)
        accounts.append(dict(scope=scope, variant='D1S1', **result))
        months += [dict(scope=scope, variant='D1S1', **x) for x in monthly]
        exits += [dict(scope=scope, variant='D1S1', **x) for x in excluded]
        nav = pd.concat([nav, n.assign(scope=scope, variant='D1S1')], ignore_index=True)
        ledger = pd.concat([ledger, l.assign(scope=scope, variant='D1S1')], ignore_index=True)
        for row in n.itertuples():
            bought = l[l.entry_date.le(row.date)]
            sold = l[l.exit_date.fillna('9999').le(row.date)]
            held = bought[bought.exit_date.fillna('9999').gt(row.date)]
            cash = 1e6 - bought.cost.sum() + sold.proceeds.sum()
            assert np.isclose(row.cash, cash, atol=1e-7, rtol=0)
            assert np.isclose(row.nav, cash + (held.shares * held.d1_close).sum(), atol=1e-7, rtol=0)
        print('new account', scope, result, flush=True)
    dump(OUT / 'accounts.json', accounts)
    dump(OUT / 'monthly.json', months)
    dump(OUT / 'exclusions.json', exits)
    nav.to_pickle(OUT / 'nav.pkl')
    ledger.to_pickle(OUT / 'ledger.pkl')
    dump(OUT / 'signal_controls.json', dict(existing_signals_exact=len(a),
        early_signals=int(one.d0_date.lt('2025-03-01').sum()),
        complete_months=list(sorted(one.d0_date.str[:7].unique()))))


def audit() -> None:
    from scripts.automl_b2 import evaluate_morning
    from scripts.experiment_limit_rebound_segments_r18 import FEATURES
    from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
    from scripts.experiment_limit_rebound_two_stage_r15 import rank
    import joblib
    previous, r = configure()
    panel = pd.read_pickle(OUT / 'panel.pkl')
    calendar = json.loads((OUT / 'calendar.json').read_text())
    rows = []
    for month in previous.HISTORY:
        root = OUT / 'daily1' / month
        bundle = joblib.load(root / 'segments.joblib')
        subset = panel[panel.d0_date.str.startswith(month)]
        fresh = rank(subset, bundle['model'].predict_proba(xmatrix(subset, FEATURES['segments']))[:, 1])
        saved = pd.read_pickle(root / 'segments_forecast_scores.pkl')
        joined = fresh[KEY + ['score', 'd0_rank']].merge(saved, on=KEY,
            validate='one_to_one', suffixes=('_new', '_saved'))
        diff = float((joined.score_new - joined.score_saved).abs().max())
        assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_saved).all()
        fit = pd.read_pickle(root / 'training_samples.pkl')
        expected = r.block(panel, calendar, *r.window(month, 1)['train'])
        expected = expected[expected.activated & expected.observed_3]
        pd.testing.assert_frame_equal(fit[KEY].reset_index(drop=True),
                                      expected[KEY].reset_index(drop=True))
        rows.append(dict(month=month, fit=len(fit), scored=len(joined), max_diff=diff))
    dump(OUT / 'model_reload_verification.json', rows)
    f = pd.read_pickle(OUT / 'enriched.pkl')
    first = pd.read_pickle(OUT / 'first10.pkl')
    if first.symbol.str.len().gt(6).any():
        first['symbol'] = first.symbol.str[:6]
    bars = {(s, d): g.sort_values('time') for (s, d), g in first.groupby(KEY)}
    demands = pd.read_pickle(OUT / 'minute_demand.pkl').set_index(KEY)
    four_bar_checks = 0
    for key, b in bars.items():
        row = demands.loc[key]
        changed = b.copy()
        changed.loc[changed.index[4:], ['open', 'high', 'low', 'close']] = 9999.
        assert r.four.first_features(b, row.d1_preclose, row.d1_cap, 4) == \
               r.four.first_features(changed, row.d1_preclose, row.d1_cap, 4)
        four_bar_checks += 1
    rules = {x['month']: x['selected'] for x in json.loads((OUT / 'rules_frozen.json').read_text())}
    decisions, selected = [], []
    for day, group in f[f.part.eq('test')].groupby('d1_date'):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=z.symbol, selection_rank=int(z.d0_rank),
            preclose=float(z.d1_preclose), cap=float(z.d1_cap), ordinary=bool(z.eligible))
            for z in group.itertuples()]
        sources = {z.symbol: bars.get((z.symbol, d0), pd.DataFrame()) for z in group.itertuples()}
        states = evaluate_morning(picks, sources, day,
            datetime.fromisoformat(day + ' 09:40:00'), rules[d0[:7]])
        for state in states:
            decisions.append(dict(d0_date=d0, d1_date=day, **state))
            if state['actionable']:
                selected.append((d0, state['symbol6'], state['entry_bar_end'][11:16],
                                 float(state['entry_price'])))
    signals = pd.read_pickle(OUT / 'signals.pkl')
    signals = signals[signals.variant.eq('D1S1')]
    expected = [(z.d0_date, z.symbol, z.entry_time, float(z.entry)) for z in signals.itertuples()]
    assert sorted(selected) == sorted(expected)
    pd.DataFrame(decisions).to_pickle(OUT / 'execution_decisions.pkl')
    checks = json.loads((OUT / 'verification.json').read_text())
    checks.update(model_reload_count=len(rows), model_score_max_diff=max(x['max_diff'] for x in rows),
        production_evaluator_exact=len(selected), candidate_decisions=len(decisions),
        four_bar_no_future_feature_checks=four_bar_checks, early_extension=True)
    dump(OUT / 'verification.json', checks)
    print('audit', checks, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['sources', 'panel', 'train',
                                          'prepare', 'select', 'evaluate', 'audit'])
    args = parser.parse_args()
    globals()[args.stage]()
