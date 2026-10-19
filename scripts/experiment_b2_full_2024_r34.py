"""B2 two-stage 6-train/1-valid chronological replay from January 2024.

2022 Sep-Oct daily rows warm the Nov-Dec 2022 training samples. The first
causal stage-one forecast is June 2023; full B2 starts January 2024.
Existing R32/R33 later models and evidence are immutable controls.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import experiment_b2_early_2025_r33 as prior
from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_limit_rebound_short_mixed_r12 import add_features, enrich_outcomes
from scripts.experiment_limit_rebound_segments_r18 import RAW, segment_one
from scripts.import_tushare_minutes_2024 import convert
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT = Path('outputs/stock_automl/b2_full_2024_r34_20261019')
R18 = prior.R18
R32 = prior.R32
R33 = prior.OUT
R12 = prior.R12
RAW_2023 = Path('/Volumes/ext/tushare_market_data/2023')
RAW_2024 = Path('/Volumes/ext/tushare_market_data/2024')
KEY = ['symbol', 'd0_date']
FIRST = '2024-01'
LAST = '2026-10'
TEST_MONTHS = [str(m) for m in pd.period_range(FIRST, LAST, freq='M')]
HISTORY = [str(m) for m in pd.period_range('2023-06', LAST, freq='M')]
NEW_MODELS = [str(m) for m in pd.period_range('2023-06', '2024-07', freq='M')]


def sources() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    old = json.loads((R33 / 'daily_sources.json').read_text())['months']
    for item in old:
        assert sha(Path(item['path'])) == item['sha256']
    warm = []
    for month in ['2022-09', '2022-10', '2022-11', '2022-12']:
        path = OUT / 'price_months' / f'price_{month}.csv.gz'
        if not path.exists():
            p = pd.Period(month, freq='M')
            with db_connection() as conn:
                frame = query(conn, 'SELECT trade_date,stk_code,preclose,open,high,low,close,'
                              'volume,amount,turn_ratio,is_limit_price,update_time '
                              'FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s',
                              (str(p.start_time.date()), str(p.end_time.date())))
                conn.rollback()
            assert len(frame) > 75_000
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, compression='gzip')
        q = pd.read_csv(path, dtype={'trade_date': str, 'stk_code': str},
                        usecols=['trade_date', 'stk_code'])
        warm.append(dict(month=month, path=str(path), sha256=sha(path),
                         rows=len(q), dates=sorted(q.trade_date.unique())))
    records = warm + old
    dates = sorted({d for item in records for d in item['dates']})
    minute_dates = sorted(f'{p.name[:4]}-{p.name[4:6]}-{p.name[6:8]}'
                          for p in RAW_2023.glob('2023????.csv.gz'))
    assert len(minute_dates) == 242
    assert dates[-242:] == minute_dates
    minute_2024_dates = sorted(p.name[:8] for p in RAW_2024.glob('2024????.csv.gz'))
    assert len(minute_2024_dates) == 242 and minute_2024_dates[0] == '20240102'
    dump(OUT / 'daily_sources.json', dict(months=records, warmup_days=sum(len(x['dates']) for x in warm),
        year_2023_days=242, minute_2023_days=len(minute_dates),
        source_year_2023=str(RAW_2023), source_year_2024=str(RAW_2024),
        minute_2024_days=len(minute_2024_dates)))
    print('sources verified', len(dates), '2022-09 through 2023-12 days', flush=True)


def panel() -> None:
    manifest = json.loads((OUT / 'daily_sources.json').read_text())
    paths = [Path(x['path']) for x in manifest['months']]
    assert all(sha(p) == x['sha256'] for p, x in zip(paths, manifest['months']))
    january_2024 = R12 / 'price_months/price_2024-01.csv.gz'
    source_manifest = json.loads((R18 / 'source_manifest.json').read_text())
    assert any(x['path'] == str(january_2024) and x['sha256'] == sha(january_2024)
               for x in source_manifest['raw_sources'])
    cols = ['trade_date', 'stk_code', 'preclose', 'open', 'high', 'low', 'close',
            'volume', 'amount', 'turn_ratio', 'is_limit_price']
    raw = pd.concat([pd.read_csv(p, dtype={'stk_code': str, 'trade_date': str}, usecols=cols)
                     for p in paths + [january_2024]], ignore_index=True)
    daily = prepare_daily(raw)
    old_calendar = json.loads((R18 / 'calendar.json').read_text())
    calendar = sorted(set(daily.trade_date) | set(old_calendar))
    mapping = {day: i for i, day in enumerate(calendar)}
    shift = mapping[old_calendar[0]]
    assert shift == len([d for d in calendar if d < old_calendar[0]])
    daily['date_i'] = daily.trade_date.map(mapping)
    # build_panel caches point-in-time status by output path; invalidate a
    # prior interrupted build when the source period has been extended.
    (OUT / 'historical_st.csv.gz').unlink(missing_ok=True)
    early, quality = build_panel(daily, OUT, score_all_d0=True, d0_start='2022-11-01')
    early = early[early.d0_date.le('2023-12-29')].copy()
    early = add_features(early, daily)
    early = enrich_outcomes(early, daily)
    early['first_i'] = early.first_limit_date.map(mapping)
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
    old = pd.read_pickle(R18 / 'panel.pkl').copy()
    for col in ['date_i', 'd1_i', 'first_i']:
        old[col] += shift
    assert set(early) == set(old)
    full = pd.concat([early[old.columns], old], ignore_index=True)
    assert full.d0_date.min() == '2022-11-01'
    assert not full.duplicated(KEY).any()
    assert full.d0_date.map(mapping).eq(full.date_i).all()
    assert full.first_limit_date.map(mapping).eq(full.first_i).all()
    # Previously constructed Nov-Dec rows must preserve all causal features.
    control = pd.read_pickle(R33 / 'panel.pkl')
    control = control[control.d0_date.between('2023-11-01', '2023-12-29')]
    match = early[early.d0_date.between('2023-11-01', '2023-12-29')]
    compare = [c for c in match if c not in ['date_i', 'd1_i', 'first_i', 'run_id']]
    pd.testing.assert_frame_equal(match.sort_values(KEY)[compare].reset_index(drop=True),
        control.sort_values(KEY)[compare].reset_index(drop=True), check_dtype=False)
    full.to_pickle(OUT / 'panel.pkl')
    dump(OUT / 'calendar.json', calendar)
    pd.DataFrame(audit).to_pickle(OUT / 'early_segment_audit.pkl')
    daily.loc[sorted(set(used)), RAW].to_pickle(OUT / 'early_feature_source_rows.pkl')
    dump(OUT / 'panel_manifest.json', dict(early_rows=len(early), old_rows=len(old),
        total_rows=len(full), shift_market_days=shift, early_quality=quality,
        nov_dec_control_exact=True, old_panel_sha256=sha(R18 / 'panel.pkl'),
        panel_sha256=sha(OUT / 'panel.pkl'), calendar_sha256=sha(OUT / 'calendar.json'),
        new_daily_source=manifest, january_2024_sha256=sha(january_2024)))
    print('panel', len(early), '+', len(old), '=', len(full), flush=True)


def specification() -> dict:
    p = OUT / 'specification.json'
    value = dict(experiment='R34 B2 six-train-one-valid full history',
        first_test=FIRST, last_test=LAST, test_months=TEST_MONTHS,
        first_daily_forecast='2023-06', daily_train_months=6, daily_valid_months=1,
        shape_train_months=6, shape_valid_months=1,
        warmup_2022='Sep-Oct daily; Nov-Dec supply training labels for early causal monthly models',
        source_panel_sha256=sha(OUT / 'panel.pkl'),
        source_calendar_sha256=sha(OUT / 'calendar.json'),
        first_stage='R32 RF51 exact family/weights/seed; no test outcome in fit',
        second_stage='R32 fourK79 rules; causal monthly first-stage forecast Top1 train, prior month validation',
        execution='D0 Top3, 4 complete D1 minute bars, prior rule and same-stock next qualifying open through 09:40, D2 close exit, fees0.1% per side',
        account='1000000 initial, flat half cash then carried-position all cash; historical chronological replay',
        production_changed=False, exploratory_history=True)
    if p.exists():
        assert json.loads(p.read_text()) == value
    else:
        dump(p, value)
    return value


def train() -> None:
    from scripts import experiment_b2_validation_r31 as r
    r.BASE = OUT
    r.OUT = OUT
    specification()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(r.train_one, month): month for month in NEW_MODELS}
        for future in as_completed(futures):
            print('trained', future.result(), flush=True)


def configure():
    from scripts import experiment_b2_validation_full_r32 as previous
    from scripts import experiment_b2_validation_r31 as r
    previous.OUT = OUT
    previous.BASE = OUT
    previous.MONTHS = TEST_MONTHS
    previous.HISTORY = HISTORY
    previous.COMMON = [str(m) for m in pd.period_range('2025-05', LAST, freq='M')]
    r.OUT = OUT
    r.BASE = OUT
    r.MONTHS = TEST_MONTHS
    r.HISTORY = HISTORY
    r.VARIANTS = {'D1S1': (1, 1)}
    r.spec = specification
    r.source = lambda dv, month, part: (
        (R18 / month) if dv == 2 else (OUT / 'daily1' / month)
    ) / f'segments_{part}_scores.pkl'
    return previous, r


def historical_states(demand: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time ST interval lookup for newly listed 2023 candidates too."""
    with db_connection() as conn:
        history = query(conn, 'SELECT stk_code,st_type,begin_date,end_date FROM kcrp_stock_st')
        conn.rollback()
    history['symbol'] = history.stk_code.str[:6]
    intervals = {sym: list(zip(g.begin_date.astype(str), g.end_date.astype(str), g.st_type))
                 for sym, g in history.groupby('symbol')}
    for which in (0, 1):
        values = []
        for row in demand.itertuples():
            day = getattr(row, f'd{which}_date')
            if pd.isna(day):
                values.append('unknown')
                continue
            matches = [s for begin, end, s in intervals.get(row.symbol, ()) if begin <= day < end]
            if len(matches) != 1:
                raise ValueError(f'ST interval: {row.symbol}, {day}, {matches}')
            values.append(matches[0])
        demand[f'state{which}'] = values
    demand['ordinary'] = demand.state0.isin(['N', 'R']) & demand.state1.isin(['N', 'R'])
    return demand


def early_high_low(demand: pd.DataFrame, previous) -> pd.DataFrame:
    old = previous.hilo(demand)
    months = sorted(set(demand.d1_date.dropna().str[:7]))
    parts = [old]
    symbols = set(demand.symbol)
    raw_sources = json.loads((OUT / 'daily_sources.json').read_text())['months']
    for item in raw_sources:
        if item['month'] not in months or item['month'] >= '2023-12':
            continue
        path = Path(item['path'])
        assert sha(path) == item['sha256']
        p = pd.read_csv(path, dtype={'stk_code': str, 'trade_date': str},
                        usecols=['stk_code', 'trade_date', 'high', 'low'])
        p['symbol'] = p.stk_code.str[:6]
        parts.append(p[p.symbol.isin(symbols)][['symbol', 'trade_date', 'high', 'low']]
            .rename(columns={'trade_date': 'd1_date', 'high': 'd1_high', 'low': 'd1_low'}))
    return pd.concat(parts, ignore_index=True).drop_duplicates(['symbol', 'd1_date'], keep='last')


def raw_minute_path(day: str) -> Path:
    root = RAW_2023 if day.startswith('2023') else RAW_2024
    return root / f'{day.replace("-", "")}.csv.gz'


def tushare_bars(day: str, wanted: set[str]) -> dict[str, pd.DataFrame]:
    """Read only requested stock rows; combine the auction exactly as 2024 importer."""
    path = raw_minute_path(day)
    if not path.exists():
        return {}
    selected = {sym: [] for sym in wanted}
    with gzip.open(path, 'rb') as source:
        header = source.readline()
        assert header.startswith(b'ts_code,trade_time,')
        for line in source:
            code = line[:6].decode('ascii', errors='ignore')
            if code in selected:
                selected[code].append(line)
    result = {}
    for sym, rows in selected.items():
        if not rows:
            continue
        raw = pd.read_csv(io.BytesIO(header + b''.join(rows)),
                          dtype={'ts_code': str, 'trade_time': str})
        try:
            b = convert(raw, day)
        except ValueError:
            continue
        b['symbol'] = sym
        result[sym] = b.sort_values('time').reset_index(drop=True)
    return result


def enrich_raw_day(day: str, demand: pd.DataFrame, r):
    path = raw_minute_path(day)
    bars = tushare_bars(day, set(demand.symbol))
    records, extracts = [], []
    for row in demand.itertuples():
        b = bars.get(row.symbol, pd.DataFrame())
        z = dict(symbol=row.symbol, d1_date=day, minute_source=str(path),
                 **r.shape.facts(b, row))
        if z['audit_ok']:
            z.update(r.four.first_features(b, row.d1_preclose, row.d1_cap, 4))
            z.update(r.four.entry_facts(b, row.d1_cap, 4))
            extracts.append(b.iloc[:10].assign(d0_date=row.d0_date))
        records.append(z)
    return records, extracts


def prepare() -> None:
    previous, r = configure()
    assert all((OUT / 'daily1' / m / 'metrics.json').exists() for m in NEW_MODELS)
    controls = []
    for month in [str(m) for m in pd.period_range('2024-08', LAST, freq='M')]:
        source = R32 / 'daily1' / month
        dest = OUT / 'daily1' / month
        dest.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            target = dest / path.name
            if not target.exists():
                shutil.copy2(path, target)
            assert sha(path) == sha(target)
        controls.append(dict(month=month, model_sha256=sha(source / 'segments.joblib')))
    dump(OUT / 'reused_models.json', controls)
    oldfacts = pd.read_pickle(R33 / 'minute_facts.pkl').set_index(['symbol', 'd1_date'])
    oldbars = pd.read_pickle(R33 / 'first10.pkl')
    oldbars['symbol'] = oldbars.symbol.str[:6]
    bar_lookup = {(s, d0): b for (s, d0), b in oldbars.groupby(KEY)}
    r.extension.daily_high_low = lambda demand: early_high_low(demand, previous)
    r.extension.with_status = historical_states
    def cached_enrich(day, demand):
        found, extracts, missing = [], [], []
        for row in demand.itertuples():
            key = (row.symbol, day)
            if key not in oldfacts.index:
                missing.append(row.Index)
                continue
            z = dict(symbol=row.symbol, d1_date=day, **oldfacts.loc[key].to_dict())
            found.append(z)
            if z['audit_ok']:
                b = bar_lookup.get((row.symbol, row.d0_date))
                assert b is not None and len(b) == 10
                extracts.append(b)
        if missing:
            frame = demand.loc[missing]
            fresh, bars = (enrich_raw_day(day, frame, r)
                           if day.startswith('2023') or
                              (day.startswith('2024') and raw_minute_path(day).exists())
                           else previous.enrich_day(day, frame))
            found.extend(fresh)
            extracts.extend(bars)
        return found, extracts
    r.enrich_day = cached_enrich
    r.prepare()
    assert len(pd.read_pickle(OUT / 'stage_samples.pkl')) > len(pd.read_pickle(R33 / 'stage_samples.pkl'))


def select() -> None:
    previous, _ = configure()
    previous.select()
    old = {x['month']: x for x in json.loads((R33 / 'rules_frozen.json').read_text())}
    new = {x['month']: x for x in json.loads((OUT / 'rules_frozen.json').read_text())}
    assert set(old) <= set(new)
    for month, prior_rule in old.items():
        for field in ['selected', 'train_base', 'valid_base', 'train_selected', 'valid_selected']:
            assert new[month][field] == prior_rule[field], (month, field)
    dump(OUT / 'rule_controls.json', dict(existing_months_exact=len(old),
        added_months=sorted(set(new)-set(old)), all_existing_rules_exact=True))


def evaluate() -> None:
    previous, r = configure()
    previous.evaluate()
    old = pd.read_pickle(R33 / 'signals.pkl')
    new = pd.read_pickle(OUT / 'signals.pkl')
    columns = KEY + ['d1_date', 'entry_time', 'entry', 'd2_close']
    a = old[old.variant.eq('D1S1')][columns].sort_values(KEY).reset_index(drop=True)
    b = new[new.variant.eq('D1S1') & new.d0_date.ge('2025-01-01')][columns].sort_values(KEY).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    calendar = json.loads((OUT / 'calendar.json').read_text())
    one = new[new.variant.eq('D1S1')]
    accounts = json.loads((OUT / 'accounts.json').read_text())
    months = json.loads((OUT / 'monthly.json').read_text())
    excluded = json.loads((OUT / 'exclusions.json').read_text())
    nav = pd.read_pickle(OUT / 'nav.pkl')
    ledger = pd.read_pickle(OUT / 'ledger.pkl')
    for scope, start, end in [
        ('完整2024起', '2024-01-01', calendar[-1]),
        ('新增2024段', '2024-01-01', '2024-12-31'),
        ('2024三月同起', '2024-03-01', calendar[-1]),
        ('2024三月单段', '2024-03-01', '2024-12-31'),
        ('2025重置复核', '2025-01-01', calendar[-1]),
    ]:
        picks = one[one.d0_date.ge(start) & one.d0_date.le(end)]
        s, m, n, l, e = r.replay(picks, calendar, start=start, end=end, fee_rate=.001)
        accounts.append(dict(scope=scope, variant='D1S1', **s))
        months.extend(dict(scope=scope, variant='D1S1', **x) for x in m)
        excluded.extend(dict(scope=scope, variant='D1S1', **x) for x in e)
        nav = pd.concat([nav, n.assign(scope=scope, variant='D1S1')], ignore_index=True)
        ledger = pd.concat([ledger, l.assign(scope=scope, variant='D1S1')], ignore_index=True)
        for row in n.itertuples():
            bought = l[l.entry_date.le(row.date)]
            sold = l[l.exit_date.fillna('9999').le(row.date)]
            held = bought[bought.exit_date.fillna('9999').gt(row.date)]
            cash = 1e6 - bought.cost.sum() + sold.proceeds.sum()
            assert np.isclose(row.cash, cash, atol=1e-7, rtol=0)
            assert np.isclose(row.nav, cash+(held.shares*held.d1_close).sum(), atol=1e-7, rtol=0)
        print(scope, s['entries'], s['total_return'], s['max_drawdown'], flush=True)
    r33 = next(x for x in json.loads((R33 / 'accounts.json').read_text())
               if x['scope'] == '一月全期含1-2月')
    reset = next(x for x in accounts if x['scope'] == '2025重置复核')
    for field in ['entries', 'closed', 'pending', 'final_nav', 'total_return', 'max_drawdown', 'sharpe']:
        assert np.isclose(reset[field], r33[field], atol=1e-8, rtol=0), field
    dump(OUT / 'accounts.json', accounts)
    dump(OUT / 'monthly.json', months)
    dump(OUT / 'exclusions.json', excluded)
    nav.to_pickle(OUT / 'nav.pkl')
    ledger.to_pickle(OUT / 'ledger.pkl')
    dump(OUT / 'signal_controls.json', dict(existing_signals_exact=len(a),
        new_2024_signals=int(one.d0_date.lt('2025-01-01').sum()),
        first_test=one.d0_date.min(), reset_2025_account_exact=True))


def audit() -> None:
    from scripts.automl_b2 import evaluate_morning
    from scripts.experiment_limit_rebound_segments_r18 import FEATURES
    from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
    from scripts.experiment_limit_rebound_two_stage_r15 import rank
    import joblib
    previous, r = configure()
    panel = pd.read_pickle(OUT / 'panel.pkl')
    calendar = json.loads((OUT / 'calendar.json').read_text())
    reloads = []
    for month in HISTORY:
        root = OUT / 'daily1' / month
        bundle = joblib.load(root / 'segments.joblib')
        subset = panel[panel.d0_date.str.startswith(month)]
        fresh = rank(subset, bundle['model'].predict_proba(xmatrix(subset, FEATURES['segments']))[:, 1])
        saved = pd.read_pickle(root / 'segments_forecast_scores.pkl')
        joined = fresh[KEY + ['score', 'd0_rank']].merge(saved, on=KEY,
            validate='one_to_one', suffixes=('_new', '_saved'))
        diff = float((joined.score_new-joined.score_saved).abs().max())
        assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_saved).all()
        fit = pd.read_pickle(root / 'training_samples.pkl')
        expected = r.block(panel, calendar, *r.window(month, 1)['train'])
        expected = expected[expected.activated & expected.observed_3]
        pd.testing.assert_frame_equal(fit[KEY].reset_index(drop=True),
                                      expected[KEY].reset_index(drop=True))
        reloads.append(dict(month=month, fit=len(fit), scored=len(joined), max_diff=diff))
    dump(OUT / 'model_reload_verification.json', reloads)
    f = pd.read_pickle(OUT / 'enriched.pkl')
    first = pd.read_pickle(OUT / 'first10.pkl')
    if first.symbol.str.len().gt(6).any():
        first['symbol'] = first.symbol.str[:6]
    assert not first.duplicated(KEY + ['time']).any()
    bars = {(s, day): g.sort_values('time') for (s, day), g in first.groupby(KEY)}
    demand = pd.read_pickle(OUT / 'minute_demand.pkl').set_index(KEY)
    for key, b in bars.items():
        row = demand.loc[key]
        changed = b.copy()
        changed.loc[changed.index[4:], ['open', 'high', 'low', 'close']] = 9999.
        assert r.four.first_features(b, row.d1_preclose, row.d1_cap, 4) == \
               r.four.first_features(changed, row.d1_preclose, row.d1_cap, 4)
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
    checks.update(model_reload_count=len(reloads), model_score_max_diff=max(x['max_diff'] for x in reloads),
        production_evaluator_exact=len(selected), candidate_decisions=len(decisions),
        four_bar_no_future_feature_checks=len(bars), start_2024=True)
    dump(OUT / 'verification.json', checks)
    print('audit', checks, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['sources', 'panel', 'train',
                                          'prepare', 'select', 'evaluate', 'audit'])
    args = parser.parse_args()
    globals()[args.stage]()
