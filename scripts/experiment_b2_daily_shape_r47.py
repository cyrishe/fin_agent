"""R47: daily four-bar refinement of causal daily B2 Top3 forecasts.

This is a research variant, not the registered B2. The selector is fitted
every D0 from six earlier months of out-of-fit daily candidates. One or two
recent months select a small ridge penalty before today's D1 decision.
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts import experiment_b2_daily_refit_r46 as daily
from scripts import extend_b2_2022_minutes_r41 as r41
from scripts import experiment_b2_full_2024_r34 as r34
from scripts import experiment_b2_validation_r31 as r31
from scripts import experiment_b2_validation_full_r32 as reference
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts.replay_limit_rebound_cash_r23 import drawdown, dump, replay, sha, sharpe

OUT = Path('outputs/stock_automl/b2_daily_shape_r47_20261020')
R43_OUT = Path('outputs/stock_automl/b2_daily_search_r43_20261019')
PANEL = daily.PANEL
CALENDAR = daily.CALENDAR
START = '2023-05-01'
END = '2026-09-30'
KEY = ['symbol', 'd0_date']
MODES = {'gap2_shape2': ('gap2', 2),
         'direct6_shape1': ('direct6', 1),
         'direct6_shape2': ('direct6', 2)}
SHAPE_FEATURES = ['high5', 'low5', 'body5', 'pullback5',
                  'below5', 'touch5', 'd0_rank']
PENALTIES = (1., 10., 100.)


def specification() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    value = dict(experiment='R47 B2 daily four-bar refinement, research only',
        source_daily=str(daily.ROOT), source_spec_sha256=sha(daily.ROOT / 'specification.json'),
        source_panel_sha256=sha(PANEL), source_calendar_sha256=sha(CALENDAR),
        baseline_signals_sha256=sha(r41.OUT / 'signals.pkl'),
        period=[START, END], variants=MODES, features=SHAPE_FEATURES,
        selector='At each D0 fit three standardized Ridge regressions alpha=1,10,100 on eligible historical causal daily Top3 candidates, targeting clipped [-20%,+20%] D2 fee-adjusted payoff. A candidate rejected by the four-bar body > -8% gate or with no valid post-09:34 simulated buy has zero payoff. Validation chooses penalty only when six-month train and one/two-month validation opportunity-day payoff are each at least the rank-only baseline; otherwise use original D0 rank.',
        stage2_windows='For each D0, six prior calendar months ending one/two calendar months before D0 train; immediately following one/two months validate. D4 maturity and active-board-event purge at both boundaries. All historical candidates scored by their own prior daily model.',
        decision='D1 after four complete bars ending 09:31..09:34, preselect highest stage2 score among original D0 Top3 with valid historical status and audited minute bars; then apply four-bar body > -8% to that one stock. If it fails the body gate, never opens, or cannot be bought after one extra qualifying minute, the day is skipped with no substitute.',
        execution='Original B2 open-below-cap positive-volume minutes ending 09:35..09:40; same-stock next qualifying minute open by09:40 as simulated buy; D2 close sell; 0.1% fee each side. Simulation price is not guaranteed executable.',
        missing_price='D2 training target requires valid D1/D2 reference-price continuity. D4 continuity never filters a D1 trade; the account replay records and excludes invalid D1/D2 price observations by the original B2 rule.',
        quality='Historical minute audit compares full D1 minute record with daily totals; it is a retrospective data-quality exclusion, not a live 09:34 predictor. Coverage and failed reasons are reported separately.',
        comparison='Compare original monthly B2, daily gap2+shape2, daily direct6+shape1, daily direct6+shape2 over the same D1 trading dates and continuous 100w accounts. Last admitted D1 must have D2 by the September 30 account cutoff, so no final open position remains. Report raw and 30/10 shadow gate separately.',
        production_changed=False, prior_history_studied=True)
    value = json.loads(json.dumps(value, ensure_ascii=False))
    p = OUT / 'specification.json'
    if p.exists():
        assert json.loads(p.read_text()) == value
    else:
        dump(p, value)
    return value


def assemble() -> pd.DataFrame:
    specification()
    saved = OUT / 'candidates.pkl'
    if saved.exists():
        return pd.read_pickle(saved)
    calendar = json.loads(CALENDAR.read_text())
    last_forecast_day = calendar[calendar.index(END) - 2]
    days = [d for d in calendar if daily.FIRST <= d <= last_forecast_day]
    for mode in daily.MODES:
        assert all((daily.ROOT / mode / d / 'complete.json').exists() for d in days), mode
    rows = []
    for mode in daily.MODES:
        scores = pd.concat((pd.read_pickle(daily.ROOT / mode / d / 'scores.pkl')
                            for d in days), ignore_index=True)
        selected = scores[scores.d0_rank.le(3)].copy()
        selected['variant'] = mode
        rows.append(selected)
    panel = pd.read_pickle(PANEL)
    f = pd.concat(rows, ignore_index=True).merge(panel, on=KEY, validate='many_to_one')
    assert not f.duplicated(['variant', *KEY]).any()
    f.to_pickle(saved)
    dump(OUT / 'candidate_sources.json', dict(source_spec_sha256=sha(daily.ROOT / 'specification.json'),
         modes={mode: dict(days=len(days), top3=int(f.variant.eq(mode).sum())) for mode in daily.MODES},
         candidate_sha256=sha(saved)))
    return f


def minute_cache() -> tuple[pd.DataFrame, dict]:
    facts, bars = [], {}
    roots = [r41.OUT, R43_OUT, r41.R35, r41.R34, reference.OUT,
             Path('outputs/stock_automl/b2_validation_full_r32_20261019'),
             Path('outputs/stock_automl/b2_validation_r31_20261019')]
    for root in dict.fromkeys(roots):
        fpath, bpath = root / 'minute_facts.pkl', root / 'first10.pkl'
        if fpath.exists():
            facts.append(pd.read_pickle(fpath))
        if bpath.exists():
            raw = pd.read_pickle(bpath)
            raw['symbol'] = raw.symbol.str[:6]
            for key, group in raw.groupby(KEY):
                bars.setdefault(key, group)
    cache = pd.concat(facts, ignore_index=True).drop_duplicates(
        ['symbol', 'd1_date'], keep='first').set_index(['symbol', 'd1_date'])
    return cache, bars


def prepare() -> None:
    specification()
    f = assemble()
    demand = f.drop_duplicates(KEY).copy()
    demand = demand.merge(r34.early_high_low(demand, reference),
                          on=['symbol', 'd1_date'], how='left', validate='one_to_one')
    demand = r34.historical_states(demand)
    demand.to_pickle(OUT / 'minute_demand.pkl')
    cached, bars = minute_cache()
    old_path = r34.raw_minute_path
    r34.raw_minute_path = lambda day: (r41.MINUTE_2022 / f'{day.replace("-", "")}.csv.gz'
                                        if day.startswith('2022') else old_path(day))
    r31.shape, r31.four, r31.OUT = shape, four, OUT
    r31.extension.daily_high_low = lambda x: r34.early_high_low(x, reference)
    r31.extension.with_status = r34.historical_states
    shape.OUT = OUT

    def enrich(day: str, group: pd.DataFrame):
        records, extracts, missing = [], [], []
        for row in group.itertuples():
            key = (row.symbol, day)
            if key not in cached.index:
                missing.append(row.Index)
                continue
            z = dict(symbol=row.symbol, d1_date=day, **cached.loc[key].to_dict())
            records.append(z)
            if z['audit_ok']:
                b = bars.get((row.symbol, row.d0_date))
                assert b is not None and len(b) == 10, key
                extracts.append(b)
        if missing:
            extra = group.loc[missing]
            if day.startswith(('2022', '2023')) or (day.startswith('2024') and r34.raw_minute_path(day).exists()):
                a, b = r34.enrich_raw_day(day, extra, r31)
            else:
                a, b = reference.enrich_day(day, extra)
            records.extend(a)
            extracts.extend(b)
        return records, extracts

    try:
        rows, extracts = [], []
        jobs = list(demand[demand.d1_date.notna()].groupby('d1_date'))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(enrich, day, group) for day, group in jobs]
            for i, future in enumerate(as_completed(futures), 1):
                a, b = future.result()
                rows.extend(a); extracts.extend(b)
                if i % 50 == 0 or i == len(jobs):
                    print('MINUTE', i, '/', len(jobs), flush=True)
        rows.extend(dict(symbol=r.symbol, d1_date=r.d1_date,
                         audit_ok=False, reason='D1日线缺失')
                    for r in demand[demand.d1_date.isna()].itertuples())
        facts = pd.DataFrame(rows)
        facts.to_pickle(OUT / 'minute_facts.pkl')
        pd.concat(extracts, ignore_index=True).to_pickle(OUT / 'first10.pkl')
        f = f.merge(demand[KEY + ['ordinary', 'state0', 'state1', 'd1_high', 'd1_low']],
                    on=KEY, validate='many_to_one')
        f = f.merge(facts, on=['symbol', 'd1_date'], how='left', validate='many_to_one')
        f['eligible'] = f.ordinary & f.audit_ok.eq(True)
        f = four.reprice(f)
        f['observation_bars'] = 4
        f.to_pickle(OUT / 'enriched.pkl')
        dump(OUT / 'coverage.json', dict(stock_days=len(demand),
             minute_ok=int(facts.audit_ok.eq(True).sum()),
             failed=facts.loc[~facts.audit_ok.eq(True),'reason'].fillna('源记录未提供原因').value_counts().to_dict()))
    finally:
        r34.raw_minute_path = old_path


def delayed_candidates() -> pd.DataFrame:
    """Compute each candidate's hypothetical B2 delayed entry, never the rank."""
    path = OUT / 'delayed_candidates.pkl'
    if path.exists():
        return pd.read_pickle(path)
    f = pd.read_pickle(OUT / 'enriched.pkl')
    first = pd.read_pickle(OUT / 'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    bars = {(sym, day): group.sort_values('time')
            for (sym, day), group in first.groupby(KEY)}
    possible = f[f.eligible & f.fill.eq(True) & f.body5.gt(-.08 + 1e-12)]
    # The same stock/day can occur in two first-stage variants.
    unique = possible.drop_duplicates(KEY)
    delayed = four.delay_same_stock(unique, bars)
    keys = delayed[KEY + ['entry', 'entry_time', 'complete']].rename(
        columns={'entry': 'delayed_entry', 'entry_time': 'delayed_time',
                 'complete': 'delayed_complete'})
    out = f.merge(keys, on=KEY, how='left', validate='many_to_one')
    d2_known = (out.delayed_entry.gt(0) & out.d2_close.gt(0)
                & out.d1_preclose.sub(out['close']).abs().le(.011)
                & out.d2_preclose.sub(out.d1_close).abs().le(.011))
    out['delayed_net'] = np.where(d2_known,
        out.d2_close / out.delayed_entry * .999 / 1.001 - 1, np.nan)
    out.to_pickle(path)
    dump(OUT / 'delayed_manifest.json', dict(source_sha256=sha(OUT / 'enriched.pkl'),
         first10_sha256=sha(OUT / 'first10.pkl'), rows=len(out),
         delayed=int(out.delayed_entry.notna().sum()), output_sha256=sha(path)))
    return out


def features(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[SHAPE_FEATURES].replace([np.inf, -np.inf], np.nan)


def opportunity(frame: pd.DataFrame, scores: np.ndarray) -> tuple[float, int, float]:
    """Equal-weight mean over D0 opportunities; no-fill days contribute zero."""
    q = frame.copy()
    q['refine_score'] = np.asarray(scores, dtype=float)
    q = q[q.eligible]
    if q.empty:
        return 0., 0, 0.
    picked = q.sort_values(['d0_date', 'refine_score', 'symbol'],
                           ascending=[True, False, True]).drop_duplicates('d0_date')
    payoff = picked.delayed_net.fillna(0).to_numpy()
    days = frame.d0_date.nunique()
    # Missing eligible candidates are idle days with zero return.
    return float(payoff.sum() / days), int(np.isfinite(picked.delayed_net).sum()), \
           float((payoff < -.05).sum() / days)


def train_one_shape(day: str, variant: str, all_rows: pd.DataFrame,
                    calendar: list[str], active_events: dict[int, pd.MultiIndex]) -> tuple[dict, pd.DataFrame | None]:
    first_mode, valid_months = MODES[variant]
    root = OUT / 'daily_shape' / variant / day
    done = root / 'complete.json'
    if done.exists() and all((root / name).exists() for name in
                             ('decision.pkl', 'model.joblib', 'training_idx.npy',
                              'validation_idx.npy')):
        return json.loads(done.read_text()), pd.read_pickle(root / 'decision.pkl')
    root.mkdir(parents=True, exist_ok=True)
    q = all_rows[all_rows.variant.eq(first_mode)]
    now = pd.Timestamp(day)
    start = str((now - pd.DateOffset(months=6 + valid_months)).date())
    boundary = str((now - pd.DateOffset(months=valid_months)).date())
    train_end_i = int(np.searchsorted(calendar, boundary) - 1)
    day_i = int(np.searchsorted(calendar, day))
    train = q[(q.d0_date.ge(start)) & (q.d0_date.lt(boundary))
              & q.date_i.add(4).le(train_end_i) & q.eligible].copy()
    valid = q[(q.d0_date.ge(boundary)) & (q.d0_date.lt(day))
              & q.date_i.add(4).lt(day_i) & q.eligible].copy()
    test = q[q.d0_date.eq(day)].copy()
    # An apparent fill with a broken or incomplete D2 price is unknown,
    # whereas a genuine no-fill is a zero-payoff opportunity.
    train = train[~(train.delayed_entry.notna() & train.delayed_net.isna())]
    valid = valid[~(valid.delayed_entry.notna() & valid.delayed_net.isna())]
    assert len(train) and len(valid) and len(test), (day, variant, len(train), len(valid), len(test))
    # Purge the board event still running at the training/validation boundary.
    active = active_events.get(train_end_i)
    if active is not None and len(active):
        overlap = pd.MultiIndex.from_frame(train[['symbol', 'first_limit_date']]).isin(
            active)
        train = train.loc[~overlap]
    assert train.d0_date.max() < boundary <= day
    np.save(root / 'training_idx.npy', train.index.to_numpy(dtype=np.int32))
    np.save(root / 'validation_idx.npy', valid.index.to_numpy(dtype=np.int32))
    target = train.delayed_net.fillna(0).clip(-.2, .2)
    candidate = []
    base_train = opportunity(train, -train.d0_rank.to_numpy())
    base_valid = opportunity(valid, -valid.d0_rank.to_numpy())
    candidate.append(dict(name='rank_only', train=base_train, valid=base_valid))
    models = {}
    for alpha in PENALTIES:
        model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), Ridge(alpha=alpha))
        model.fit(features(train), target)
        models[f'ridge_{alpha:g}'] = model
        tr = opportunity(train, model.predict(features(train)))
        va = opportunity(valid, model.predict(features(valid)))
        candidate.append(dict(name=f'ridge_{alpha:g}', train=tr, valid=va))
    eligible = [c for c in candidate if
                c['train'][0] >= base_train[0] - 1e-12 and
                c['valid'][0] >= base_valid[0] - 1e-12]
    winner = sorted(eligible, key=lambda c: (-(c['train'][0] + c['valid'][0])/2,
                                            c['name']))[0]
    chosen_model = models.get(winner['name'])
    joblib.dump(dict(model=chosen_model, variant=variant, forecast_day=day,
                     features=SHAPE_FEATURES, train_start=start,
                     train_end_exclusive=boundary, valid_start=boundary,
                     valid_end_exclusive=day, selection=winner['name'],
                     specification_sha256=sha(OUT / 'specification.json')),
                root / 'model.joblib', compress=3)
    restored = joblib.load(root / 'model.joblib')['model']
    if chosen_model is not None:
        np.testing.assert_array_equal(chosen_model.predict(features(test)),
                                      restored.predict(features(test)))
    scores = (-test.d0_rank.to_numpy() if restored is None else
              restored.predict(features(test)))
    test['refine_score'] = scores
    eligible_test = test[test.eligible].sort_values(
        ['refine_score', 'symbol'], ascending=[False, True])
    selected = eligible_test.head(1).copy()
    selected['shape_pass'] = selected.body5.gt(-.08 + 1e-12)
    selected['shape_variant'] = variant
    selected['shape_model'] = winner['name']
    selected.to_pickle(root / 'decision.pkl')
    receipt = dict(day=day, variant=variant, train=[start, boundary],
        valid=[boundary, day], train_rows=len(train), valid_rows=len(valid),
        train_days=train.d0_date.nunique(), valid_days=valid.d0_date.nunique(),
        selected=winner['name'], candidates=candidate,
        preselected_symbol=selected.symbol.iloc[0] if len(selected) else None,
        simulated_buy=bool(len(selected) and selected.shape_pass.iloc[0]
                           and pd.notna(selected.delayed_entry.iloc[0])),
        training_keys_sha256=sha_bytes(train[KEY].to_csv(index=False).encode()),
        validation_keys_sha256=sha_bytes(valid[KEY].to_csv(index=False).encode()),
        training_index_sha256=sha(root / 'training_idx.npy'),
        validation_index_sha256=sha(root / 'validation_idx.npy'),
        model_sha256=sha(root / 'model.joblib'),
        no_test_selection=True)
    dump(done, receipt)
    return receipt, selected


def sha_bytes(blob: bytes) -> str:
    import hashlib
    return hashlib.sha256(blob).hexdigest()


def fit_shape(first: str | None, last: str | None) -> None:
    specification()
    f = delayed_candidates()
    calendar = json.loads(CALENDAR.read_text())
    last_forecast_day = calendar[calendar.index(END) - 2]
    panel = pd.read_pickle(PANEL)
    active_events = {int(day_i): pd.MultiIndex.from_frame(
        group[['symbol', 'first_limit_date']])
        for day_i, group in panel.groupby('date_i')}
    days = [d for d in calendar if START <= d <= last_forecast_day and
            (first is None or d >= first) and (last is None or d <= last)]
    for i, day in enumerate(days, 1):
        for variant in MODES:
            train_one_shape(day, variant, f, calendar, active_events)
        if i % 20 == 0 or i == len(days):
            print('SHAPE', i, '/', len(days), day, flush=True)


def shadow_gate(signals: pd.DataFrame, calendar: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    from scripts.report_b2_double_shadow_full_r40 import step
    dates = [d for d in calendar if START <= d <= END]
    by_day = {x['d1_date']: x for x in signals.to_dict('records')}
    paper = dict(cash=1_000_000., history=[1_000_000.], positions=[])
    kept, decisions = [], []
    index = {d: i for i, d in enumerate(dates)}
    for i, day in enumerate(dates):
        prior = paper['history'][i]
        r30 = prior / paper['history'][i-30] - 1 if i >= 30 else None
        r10 = prior / paper['history'][i-10] - 1 if i >= 10 else None
        stop = bool(r30 is not None and r30 <= -.10 and r10 is not None and r10 < .01)
        item = by_day.get(day)
        if item is not None:
            decisions.append(dict(day=day, symbol=item['symbol'],
                                  shadow30=r30, shadow10=r10, stop=stop))
            if not stop:
                kept.append(day)
        step(paper, day, i, item, index)
    return signals[signals.d1_date.isin(kept)].copy(), pd.DataFrame(decisions)


def report() -> None:
    specification()
    calendar = json.loads(CALENDAR.read_text())
    last_forecast_day = calendar[calendar.index(END) - 2]
    days = [d for d in calendar if START <= d <= last_forecast_day]
    last_entry_day = calendar[calendar.index(END) - 1]
    plans = {'fixed_monthly_B2': pd.read_pickle(r41.OUT / 'signals.pkl')}
    decisions = []
    for variant in MODES:
        roots = [OUT / 'daily_shape' / variant / d for d in days]
        assert all((r / 'complete.json').exists() for r in roots), variant
        chosen = [pd.read_pickle(r / 'decision.pkl') for r in roots]
        all_decisions = pd.concat(chosen, ignore_index=True)
        decisions.append(all_decisions)
        signal = all_decisions[all_decisions.delayed_entry.gt(0)].copy()
        signal['entry'] = signal.delayed_entry
        signal['entry_time'] = signal.delayed_time
        signal = four.reprice(signal)
        assert not signal.d1_date.duplicated().any()
        plans[variant] = signal
    pd.concat(decisions, ignore_index=True).to_pickle(OUT / 'daily_decisions.pkl')
    pd.concat((p[p.d1_date.between(START, last_entry_day)].assign(plan=name)
               for name, p in plans.items()),
              ignore_index=True).to_pickle(OUT / 'all_signals.pkl')
    overall, monthly, yearly, classifications, exclusions, navs, ledgers = [], [], [], [], [], [], []
    gate_decisions = []
    for name, source in plans.items():
        source = source[source.d1_date.between(START, last_entry_day)].sort_values('d1_date')
        raw_result = replay(source, calendar, start=START, end=END, fee_rate=.001)
        raw_excluded = {(e['symbol'], e['d1_date']) for e in raw_result[-1]}
        valid_source = source[~pd.MultiIndex.from_frame(source[['symbol', 'd1_date']]).isin(
            pd.MultiIndex.from_tuples(list(raw_excluded), names=['symbol', 'd1_date']))]
        gated, gate_log = shadow_gate(valid_source, calendar)
        gate_log['plan'] = name
        gate_decisions.append(gate_log)
        for gate, signals in [('raw', source), ('shadow30_10', gated)]:
            stats, months, nav, ledger, errors = (raw_result if gate == 'raw' else
                replay(signals, calendar, start=START, end=END, fee_rate=.001))
            assert stats['pending'] == 0, (name, gate, stats['pending'])
            assert np.isclose(np.prod([1 + m['return_'] for m in months]) - 1,
                              stats['total_return'], atol=1e-10), (name, gate)
            overall.append(dict(plan=name, gate=gate, **stats))
            monthly.extend(dict(plan=name, gate=gate, **m) for m in months)
            navs.append(nav.assign(plan=name, gate=gate))
            ledgers.append(ledger.assign(plan=name, gate=gate))
            exclusions.extend(dict(plan=name, gate=gate, **e) for e in errors)
            previous = 1_000_000.
            for year, group in nav.groupby(nav.date.str[:4], sort=True):
                closing = float(group.nav.iloc[-1])
                yearly.append(dict(plan=name, gate=gate, year=year,
                    opening_nav=previous, closing_nav=closing,
                    return_=closing / previous - 1,
                    drawdown=drawdown(np.r_[previous, group.nav.to_numpy()]),
                    sharpe=sharpe(group.return_),
                    entries=int(ledger.entry_date.str.startswith(year).sum()) if len(ledger) else 0))
                previous = closing
        known = valid_source[valid_source.observed_3]
        net = known.d2_close / known.entry * .999 / 1.001 - 1
        classifications.append(dict(plan=name, stage='可成交信号', n=len(known),
            hits=int(known.label_3.sum()),
            hit=float(known.label_3.mean()) if len(known) else None,
            d2_fee_mean=float(net.mean()) if len(net) else None,
            loss_gt5=float(net.lt(-.05).mean()) if len(net) else None))
    enriched = pd.read_pickle(OUT / 'enriched.pkl')
    for name, selected in zip(MODES, decisions):
        q = selected[selected.d1_date.between(START, last_entry_day)
                     & selected.observed_3.eq(True)]
        for stage, rows in [('四K预选', q), ('实体通过', q[q.shape_pass.eq(True)])]:
            classifications.append(dict(plan=name, stage=stage, n=len(rows),
                hits=int(rows.label_3.sum()),
                hit=float(rows.label_3.mean()) if len(rows) else None))
    for mode in daily.MODES:
        q = enriched[enriched.variant.eq(mode) & enriched.d1_date.between(START, last_entry_day)]
        for k in (1, 3):
            for stage, opened in [('全量', False), ('D1开盘打开', True)]:
                x = q[q.d0_rank.le(k) & q.observed_3
                      & (q.activated if opened else True)]
                classifications.append(dict(plan=mode, stage=f'Top{k}_{stage}', n=len(x),
                    hits=int(x.label_3.sum()), hit=float(x.label_3.mean()) if len(x) else None))
    dump(OUT / 'overall.json', overall)
    dump(OUT / 'monthly.json', monthly)
    dump(OUT / 'yearly.json', yearly)
    dump(OUT / 'classification.json', classifications)
    dump(OUT / 'exclusions.json', exclusions)
    pd.concat(navs, ignore_index=True).to_pickle(OUT / 'nav.pkl')
    pd.concat(ledgers, ignore_index=True).to_pickle(OUT / 'ledger.pkl')
    pd.concat(gate_decisions, ignore_index=True).to_pickle(OUT / 'gate_decisions.pkl')
    print(pd.DataFrame(overall)[['plan', 'gate', 'entries', 'total_return',
                                 'max_drawdown', 'sharpe']].to_string(index=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['spec', 'assemble', 'prepare',
                                          'delay', 'fit', 'report'])
    parser.add_argument('--first')
    parser.add_argument('--last')
    args = parser.parse_args()
    {'spec': specification, 'assemble': assemble, 'prepare': prepare,
     'delay': delayed_candidates,
     'fit': lambda: fit_shape(args.first, args.last),
     'report': report}[args.stage]()
