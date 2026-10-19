"""R43: causal monthly search for the B2 daily ranking model.

The B2 four-bar execution and D2 exit are evaluated only after all monthly
daily choices are frozen.  The older reference RF is retained as a candidate.
Nothing in this module writes the live strategy tables or changes autorun.
"""
from __future__ import annotations

import argparse
import json
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import joblib
import lightgbm
import numpy as np
import pandas as pd
import sklearn
import xgboost
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from xgboost import XGBClassifier

warnings.filterwarnings('ignore', message='X does not have valid feature names, but LGBMClassifier was fitted with feature names', category=UserWarning)

from scripts import extend_b2_2022_minutes_r41 as r41
from scripts import experiment_b2_full_2024_r34 as r34
from scripts import experiment_b2_validation_r31 as r31
from scripts import experiment_b2_validation_full_r32 as reference
from scripts.experiment_b2_validation_r31 import block, window
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import classify, rank
from scripts.replay_limit_rebound_cash_r23 import drawdown, dump, replay, sha, sharpe


TRAINING_OUT = Path('outputs/stock_automl/b2_daily_search_r43_20261019')
RISK_OUT = Path('outputs/stock_automl/b2_daily_risk_choice_r44_20261019')
OUT = TRAINING_OUT
PANEL = r41.OUT / 'panel.pkl'
CALENDAR = r41.OUT / 'calendar.json'
KEY = ['symbol', 'd0_date']
HISTORY = [str(x) for x in pd.period_range('2022-09', '2026-09', freq='M')]
TEST = [str(x) for x in pd.period_range('2023-05', '2026-09', freq='M')]
SEEDS = (171, 191)
PARAMS = {
    'rf': [
        dict(n_estimators=160, max_depth=5, min_samples_leaf=70, max_features=.8),
        dict(n_estimators=160, max_depth=8, min_samples_leaf=30, max_features=.8),
    ],
    'lgb': [
        dict(n_estimators=160, learning_rate=.04, num_leaves=7, max_depth=3,
             min_child_samples=80, reg_lambda=5.),
        dict(n_estimators=220, learning_rate=.03, num_leaves=15, max_depth=4,
             min_child_samples=60, reg_lambda=10.),
    ],
    'xgb': [
        dict(n_estimators=160, learning_rate=.04, max_depth=2,
             min_child_weight=20, reg_lambda=5.),
        dict(n_estimators=220, learning_rate=.03, max_depth=3,
             min_child_weight=20, reg_lambda=10.),
    ],
}
GROUPS = [dict(id=f'{algorithm}_p{i}_{scheme}', algorithm=algorithm,
               parameters=params, scheme=scheme, fraction=.8, seeds=list(SEEDS))
          for algorithm, variants in PARAMS.items()
          for i, params in enumerate(variants, 1)
          for scheme in ('natural', 'balanced')]


def baseline_path(month: str, part: str) -> Path:
    if month < '2023-07':
        root = r41.OUT / 'daily2' / month
    elif month < '2025-01':
        root = r41.R35 / 'daily2' / month
    else:
        root = r41.R18 / month
    return root / f'segments_{part}_scores.pkl'


def specification() -> dict:
    assert sklearn.__version__ == '1.7.1', sklearn.__version__
    OUT.mkdir(parents=True, exist_ok=True)
    if OUT == RISK_OUT:
        value = dict(
            experiment='R44 B2 D2 proxy aligned monthly choice, research only',
            training_source=str(TRAINING_OUT),
            training_spec_sha256=sha(TRAINING_OUT / 'specification.json'),
            trained_groups=GROUPS, history=HISTORY, test=TEST,
            train_quality='Causal prior monthly forecast scores in six-month train block.',
            valid_quality='Current model on the two preceding validation months.',
            proxy='For original D0 Top3 whose D1 opening is below cap: fee-adjusted D2 close / D1 open - 1, averaged across stocks; no minute or future test outcomes used for selection.',
            chooser='Candidate must have proxy mean >= fixed RF both in six-month OOF and two-month validation. Select max of (OOF+validation)/2 - .25*absolute gap - .10*monthly-proxy-std. Otherwise fixed RF.',
            shape_and_trade='After monthly model freeze, original B2 four K rules and D2 execution are retrained/replayed unchanged.',
            caveat='D1 open is a model-choice proxy; actual B2 simulated entry uses later minute open. Previously researched years are not a blind test.',
            panel_sha256=sha(PANEL), calendar_sha256=sha(CALENDAR),
            production_changed=False,
        )
        path = OUT / 'specification.json'
        if path.exists():
            assert json.loads(path.read_text()) == value
        else:
            dump(path, value)
        return value
    value = dict(
        experiment='R43 B2 segments51 daily model search, research only',
        history=HISTORY, test=TEST, features=FEATURES['segments'],
        groups=GROUPS, reference='frozen R18/R35/R41 RF51 models and scores',
        split='For each forecast month M: previous months M-8..M-3 train, M-2..M-1 validation; never refit validation.',
        fit_pool='D1 open below cap and D1-D3 sealed-close label complete; original event purge and D4 cutoff.',
        candidate_sampling='Each seed draws 80% without replacement within training month x label. Natural or class-balanced weights on the sampled fit only.',
        candidate_prediction='Average the two seed probabilities before D0 ranking.',
        train_quality='Causal prior monthly forecasts restricted to the current six-month train block, not same-model in-fit ranking.',
        valid_quality='Current model forecast of the two-month validation block.',
        metric='Mean of D1-open activated Top1 and Top3 three-day sealed-close hit rates; observed labels only.',
        chooser='Require candidate OOF and validation quality >= fixed RF. Select max of (OOF+validation)/2 - .25*abs(OOF-validation) - .10*monthly-quality-std; tied ID alphabetical. Otherwise fixed RF.',
        test_control='Freeze each month choice before reading its test outcomes or minute trades. Historical 2023-24 results remain previously studied, not a blind test.',
        execution='Original B2 Top3, four bars, activation and delay, body > -8%, D2 close; fee 0.1% each side.',
        panel_sha256=sha(PANEL), calendar_sha256=sha(CALENDAR),
        versions=dict(sklearn=sklearn.__version__, lightgbm=lightgbm.__version__,
                      xgboost=xgboost.__version__),
        production_changed=False,
    )
    path = OUT / 'specification.json'
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def sample_train(fit: pd.DataFrame, scheme: str, seed: int):
    rng = np.random.default_rng(seed)
    draws = []
    for _, group in fit.groupby([fit.d0_date.str[:7], fit.label_3], sort=True):
        n = max(1, int(np.floor(.8 * len(group))))
        draws.append(group.iloc[np.sort(rng.choice(len(group), n, replace=False))])
    sampled = pd.concat(draws).sort_values(KEY)
    weights = np.ones(len(sampled), dtype=float)
    if scheme == 'balanced':
        counts = sampled.label_3.value_counts()
        weights = np.asarray([len(sampled) / (2 * counts[y])
                              for y in sampled.label_3], dtype=float)
        weights /= weights.mean()
    return sampled, weights


def model_for(group: dict, seed: int):
    p = group['parameters']
    if group['algorithm'] == 'rf':
        return make_pipeline(SimpleImputer(strategy='median', add_indicator=True),
                             RandomForestClassifier(**p, n_jobs=1, random_state=seed))
    if group['algorithm'] == 'lgb':
        return LGBMClassifier(**p, objective='binary', colsample_bytree=.8,
                              subsample=1., subsample_freq=0, verbosity=-1,
                              deterministic=True, force_col_wise=True,
                              n_jobs=1, random_state=seed)
    return XGBClassifier(**p, objective='binary:logistic', eval_metric='logloss',
                         tree_method='hist', colsample_bytree=.8, subsample=1.,
                         n_jobs=1, random_state=seed)


def matrix(frame: pd.DataFrame) -> np.ndarray:
    return xmatrix(frame, FEATURES['segments']).to_numpy(dtype=np.float64)


def train_group(group: dict, fit: pd.DataFrame, frames: dict[str, pd.DataFrame],
                root: Path, month: str) -> None:
    gid = group['id']
    done = root / f'{gid}_complete.json'
    if done.exists():
        return
    started = time.monotonic()
    models, sampling = [], []
    for seed in group['seeds']:
        sampled, weights = sample_train(fit, group['scheme'], seed)
        model = model_for(group, seed)
        kw = ({'randomforestclassifier__sample_weight': weights}
              if group['algorithm'] == 'rf' else {'sample_weight': weights})
        model.fit(matrix(sampled), sampled.label_3.astype(int), **kw)
        models.append(model)
        sampling.append(dict(seed=seed, drawn=len(sampled),
                             positive=int(sampled.label_3.sum()),
                             training_keys=sampled[KEY + ['first_limit_date']].to_dict('records')))
    bundle = dict(month=month, group=group, models=models, sampling=sampling,
                  features=FEATURES['segments'], spec_sha256=sha(OUT / 'specification.json'))
    path = root / f'{gid}.joblib'
    before = np.mean([m.predict_proba(matrix(fit.iloc[:30]))[:, 1] for m in models], axis=0)
    joblib.dump(bundle, path, compress=3)
    bundle = joblib.load(path)
    after = np.mean([m.predict_proba(matrix(fit.iloc[:30]))[:, 1] for m in bundle['models']], axis=0)
    np.testing.assert_array_equal(before, after)
    scores = {}
    for part, frame in frames.items():
        x = matrix(frame)
        prob = np.mean([m.predict_proba(x)[:, 1] for m in bundle['models']], axis=0)
        out = frame[KEY].copy()
        out['score'] = prob
        out.to_pickle(root / f'{gid}_{part}_scores.pkl')
        if part != 'forecast':
            scores[part] = classify(rank(frame, prob))
    dump(root / f'{gid}_metrics.json', dict(window=window(month, 2),
         fit_rows=len(fit), scores=scores, model_sha256=sha(path)))
    dump(done, dict(seconds=round(time.monotonic() - started, 2),
                    model_sha256=sha(path)))


def train_month(month: str) -> str:
    specification()
    root = OUT / 'models' / month
    root.mkdir(parents=True, exist_ok=True)
    if all((root / f'{g["id"]}_complete.json').exists() for g in GROUPS):
        return f'{month} cached'
    panel = pd.read_pickle(PANEL)
    calendar = json.loads(CALENDAR.read_text())
    w = window(month, 2)
    frames = dict(train=block(panel, calendar, *w['train']),
                  valid=block(panel, calendar, *w['valid'], purge_events=False),
                  forecast=panel[panel.d0_date.str.startswith(month)].copy())
    fit = frames['train'][frames['train'].activated & frames['train'].observed_3]
    assert len(fit) > 100 and fit.label_3.nunique() == 2, (month, len(fit))
    for group in GROUPS:
        train_group(group, fit, frames, root, month)
    return f'{month} {len(fit)} fitted'


def train(workers: int, first: str | None, last: str | None) -> None:
    specification()
    months = [m for m in HISTORY if (first is None or m >= first) and (last is None or m <= last)]
    jobs = [m for m in months if not all((OUT / 'models' / m / f'{g["id"]}_complete.json').exists()
                                        for g in GROUPS)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(train_month, m) for m in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            print('TRAIN', i, '/', len(jobs), future.result(), flush=True)


def group_scores(month: str, group_id: str, part: str) -> pd.DataFrame:
    path = (baseline_path(month, part) if group_id == 'reference'
            else TRAINING_OUT / 'models' / month / f'{group_id}_{part}_scores.pkl')
    frame = pd.read_pickle(path)
    return frame[KEY + ['score']]


def quality(frame: pd.DataFrame) -> dict:
    result = classify(rank(frame, frame.score.to_numpy()))
    open_ = result['open']
    q = float(np.mean([open_[f'top{k}']['rate'] or 0 for k in (1, 3)]))
    return dict(quality=q, open= open_)


def d2_proxy_rows(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = rank(frame, frame.score.to_numpy())
    picked = ranked[ranked.d0_rank.le(3) & ranked.activated &
                    ranked.d1_open.gt(0) & ranked.d2_close.gt(0)].copy()
    picked['d2_proxy_return'] = picked.d2_close / picked.d1_open * .999 / 1.001 - 1
    return picked


def d2_proxy_quality(picked: pd.DataFrame) -> dict:
    returns = picked.d2_proxy_return
    return dict(n=len(picked), mean=float(returns.mean()) if len(returns) else None,
                loss_gt5=float(returns.lt(-.05).mean()) if len(returns) else None)


def candidate_stats(panel: pd.DataFrame, calendar: list[str], month: str,
                    group_id: str) -> dict:
    w = window(month, 2)
    allowed = block(panel, calendar, *w['train'])[KEY]
    train_months = [str(m) for m in pd.period_range(*w['train'], freq='M')]
    oof = pd.concat([group_scores(m, group_id, 'forecast') for m in train_months],
                    ignore_index=True).merge(allowed, on=KEY, validate='one_to_one')
    valid = group_scores(month, group_id, 'valid')
    expected = block(panel, calendar, *w['valid'], purge_events=False)
    assert len(valid) == len(expected)
    oof = oof.merge(panel, on=KEY, validate='one_to_one')
    valid = valid.merge(panel, on=KEY, validate='one_to_one')
    qa, qb = quality(oof), quality(valid)
    combined = pd.concat([oof, valid], ignore_index=True)
    monthly = [quality(g)['quality'] for _, g in combined.groupby(combined.d0_date.str[:7])]
    spread = float(np.std(monthly))
    score = (qa['quality'] + qb['quality']) / 2 - .25 * abs(qa['quality'] - qb['quality']) - .10 * spread
    risk_oof_rows, risk_valid_rows = d2_proxy_rows(oof), d2_proxy_rows(valid)
    risk_a, risk_b = d2_proxy_quality(risk_oof_rows), d2_proxy_quality(risk_valid_rows)
    risk_combined = pd.concat([risk_oof_rows, risk_valid_rows], ignore_index=True)
    risk_monthly = risk_combined.groupby(risk_combined.d0_date.str[:7]).d2_proxy_return.mean().tolist()
    risk_spread = float(np.std(risk_monthly))
    risk_score = (risk_a['mean'] + risk_b['mean']) / 2 - .25 * abs(
        risk_a['mean'] - risk_b['mean']) - .10 * risk_spread
    return dict(id=group_id, oof=qa, valid=qb, monthly_std=spread,
                selection_score=float(score), risk_oof=risk_a, risk_valid=risk_b,
                risk_monthly_std=risk_spread, risk_selection_score=float(risk_score))


def choose(first: str | None, last: str | None, wait: bool = False) -> None:
    specification()
    panel = pd.read_pickle(PANEL)
    calendar = json.loads(CALENDAR.read_text())
    for month in TEST:
        if (first and month < first) or (last and month > last):
            continue
        root = OUT / 'selected' / month
        root.mkdir(parents=True, exist_ok=True)
        if (root / 'choice.json').exists():
            print('CHOICE cached', month, flush=True)
            continue
        needed = [str(m) for m in pd.period_range(*window(month, 2)['train'], freq='M')]
        needed.append(month)
        expected_files = [TRAINING_OUT / 'models' / m / f'{g["id"]}_complete.json'
                          for m in needed for g in GROUPS]
        while not all(p.exists() for p in expected_files):
            if not wait:
                raise RuntimeError(f'Candidate fits incomplete for {month}')
            time.sleep(10)
        candidates = [candidate_stats(panel, calendar, month, gid)
                      for gid in ['reference', *[g['id'] for g in GROUPS]]]
        baseline = candidates[0]
        if OUT == RISK_OUT:
            eligible = [r for r in candidates if
                        r['risk_oof']['mean'] >= baseline['risk_oof']['mean'] and
                        r['risk_valid']['mean'] >= baseline['risk_valid']['mean']]
            key = 'risk_selection_score'
        else:
            eligible = [r for r in candidates if
                        r['oof']['quality'] >= baseline['oof']['quality'] and
                        r['valid']['quality'] >= baseline['valid']['quality']]
            key = 'selection_score'
        winner = sorted(eligible, key=lambda r: (-r[key], r['id']))[0]
        assert winner['id'] in [r['id'] for r in eligible]
        choice = dict(month=month, window=window(month, 2), selected=winner['id'],
                      selected_group=next((g for g in GROUPS if g['id'] == winner['id']), None),
                      candidates=candidates, test_results_seen=False,
                      spec_sha256=sha(OUT / 'specification.json'))
        dump(root / 'choice.json', choice)
        for part in ('train', 'valid', 'forecast'):
            scored = group_scores(month, winner['id'], part).merge(panel, on=KEY, validate='one_to_one')
            ranked = rank(scored, scored.score.to_numpy())
            ranked[KEY + ['score', 'd0_rank']].to_pickle(root / f'segments_{part}_scores.pkl')
        print('CHOICE', month, winner['id'],
              round(winner['oof']['quality'], 4),
              round(winner['valid']['quality'], 4),
              round(winner['risk_oof']['mean'], 4),
              round(winner['risk_valid']['mean'], 4), flush=True)


def configure_b2() -> None:
    reference.OUT = OUT
    reference.BASE = r41.OUT
    r31.OUT = OUT
    r31.BASE = r41.OUT
    r31.MONTHS = TEST
    r31.HISTORY = HISTORY
    r31.VARIANTS = {'D2S2': (2, 2)}
    r31.spec = specification
    r31.source = lambda dv, month, part: (
        (OUT / 'selected' / month / f'segments_{part}_scores.pkl')
        if month in TEST else baseline_path(month, part))


def minute_cache() -> tuple[pd.DataFrame, dict]:
    """Reuse immutable earlier B2 minute evidence before reading new raw rows."""
    facts, bars = [], {}
    roots = [r41.OUT, TRAINING_OUT, r41.R35, r41.R34, reference.OUT,
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
    configure_b2()
    specification()
    assert all((OUT / 'selected' / m / 'choice.json').exists() for m in TEST)
    cached, bars = minute_cache()
    r31.extension.daily_high_low = lambda demand: r34.early_high_low(demand, reference)
    r31.extension.with_status = r34.historical_states
    original_path = r34.raw_minute_path
    r34.raw_minute_path = lambda day: (
        r41.MINUTE_2022 / f'{day.replace("-", "")}.csv.gz'
        if day.startswith('2022') else original_path(day))

    def enrich(day, demand):
        records, extracts, missing = [], [], []
        for row in demand.itertuples():
            key = (row.symbol, day)
            if key not in cached.index:
                missing.append(row.Index)
                continue
            item = dict(symbol=row.symbol, d1_date=day, **cached.loc[key].to_dict())
            records.append(item)
            if item['audit_ok']:
                b = bars.get((row.symbol, row.d0_date))
                assert b is not None and len(b) == 10, key
                extracts.append(b)
        if missing:
            frame = demand.loc[missing]
            if day.startswith(('2022', '2023')) or (
                    day.startswith('2024') and r34.raw_minute_path(day).exists()):
                a, b = r34.enrich_raw_day(day, frame, r31)
            else:
                a, b = reference.enrich_day(day, frame)
            records.extend(a)
            extracts.extend(b)
        return records, extracts

    r31.enrich_day = enrich
    try:
        r31.prepare()
    finally:
        r34.raw_minute_path = original_path


def select_rules() -> None:
    configure_b2()
    f = pd.read_pickle(OUT / 'enriched.pkl')
    calendar = json.loads(CALENDAR.read_text())
    selected, candidates = [], []
    original_window = r31.shape.window
    try:
        r31.shape.window = lambda month, valid=2: window(month, 2)
        for month in TEST:
            q = f[f.model_month.eq(month)]
            parts = []
            for part in ('train', 'valid'):
                frame = q[q.part.eq(part)]
                cutoff = str(pd.Period(window(month, 2)[part][1]) + 1) + '-01'
                assert all(calendar[int(i) + 4] < cutoff for i in frame.date_i)
                parts.append(frame[frame.complete].copy())
            chosen = json.loads(json.dumps(r31.four.choose_rule(
                *parts, month, r31.shape.rules())))
            chosen['variant'] = 'D2S2'
            selected.append(chosen)
            for rule in r31.shape.rules():
                candidates.append(dict(month=month, rule=rule,
                    train=r31.shape.metrics(parts[0], rule),
                    valid=r31.shape.metrics(parts[1], rule)))
            print('RULE', month, chosen['selected']['id'], flush=True)
    finally:
        r31.shape.window = original_window
    dump(OUT / 'rules_frozen.json', selected)
    dump(OUT / 'rule_candidates.json', candidates)


def evaluate() -> None:
    configure_b2()
    f = pd.read_pickle(OUT / 'enriched.pkl')
    rules = json.loads((OUT / 'rules_frozen.json').read_text())
    first = pd.read_pickle(OUT / 'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    bars = {(s, d0): group.sort_values('time')
            for (s, d0), group in first.groupby(KEY)}
    picked, classes = [], []
    for item in rules:
        month = item['month']
        q = f[f.model_month.eq(month) & f.part.eq('test')]
        chosen = r31.four.delay_same_stock(
            r31.four.chosen(q, item['selected']), bars)
        chosen['variant'] = 'R43'
        chosen['rule_id'] = item['selected']['id']
        chosen['rule_text'] = item['selected']['text'].replace('五分', '四分')
        picked.append(chosen)
        for stage, frame in [('形态前Top3', q[q.eligible]),
                             ('形态通过Top3', q[q.eligible & r31.shape.mask(q, item['selected'])]),
                             ('最终交易', chosen)]:
            known = frame[frame.observed_3]
            classes.append(dict(month=month, stage=stage, n=len(known),
                hits=int(known.label_3.sum()),
                hit=float(known.label_3.mean()) if len(known) else None))
        print('SIGNAL', month, len(chosen), flush=True)
    signals = pd.concat(picked, ignore_index=True).sort_values('d1_date')
    assert not signals.d1_date.duplicated().any()
    signals.to_pickle(OUT / 'signals.pkl')
    dump(OUT / 'shape_test_metrics.json', classes)
    dump(OUT / 'signal_manifest.json', dict(
        signal_sha256=sha(OUT / 'signals.pkl'),
        rules_sha256=sha(OUT / 'rules_frozen.json'),
        selection_sha256=sha(OUT / 'specification.json'),
        signal_count=len(signals), production_changed=False))


def audit() -> None:
    from scripts.automl_b2 import evaluate_morning
    configure_b2()
    panel = pd.read_pickle(PANEL)
    model_checks = []
    for month in TEST:
        choice = json.loads((OUT / 'selected' / month / 'choice.json').read_text())
        gid = choice['selected']
        candidate = next(x for x in choice['candidates'] if x['id'] == gid)
        baseline = next(x for x in choice['candidates'] if x['id'] == 'reference')
        if OUT == RISK_OUT:
            assert candidate['risk_oof']['mean'] >= baseline['risk_oof']['mean']
            assert candidate['risk_valid']['mean'] >= baseline['risk_valid']['mean']
        else:
            assert candidate['oof']['quality'] >= baseline['oof']['quality']
            assert candidate['valid']['quality'] >= baseline['valid']['quality']
        frame = panel[panel.d0_date.str.startswith(month)]
        if gid == 'reference':
            bundle_path = baseline_path(month, 'forecast').parent / 'segments.joblib'
            bundle = joblib.load(bundle_path)
            fresh = bundle['model'].predict_proba(xmatrix(frame, FEATURES['segments']))[:, 1]
        else:
            bundle_path = TRAINING_OUT / 'models' / month / f'{gid}.joblib'
            bundle = joblib.load(bundle_path)
            train_end = window(month, 2)['train'][1]
            assert all(x['d0_date'][:7] <= train_end
                       for sample in bundle['sampling'] for x in sample['training_keys'])
            x = matrix(frame)
            fresh = np.mean([m.predict_proba(x)[:, 1] for m in bundle['models']], axis=0)
        ranked = rank(frame, fresh)[KEY + ['score', 'd0_rank']]
        saved = pd.read_pickle(OUT / 'selected' / month / 'segments_forecast_scores.pkl')
        joined = ranked.merge(saved, on=KEY, validate='one_to_one',
                              suffixes=('_new', '_old'))
        diff = float((joined.score_new - joined.score_old).abs().max())
        assert diff < 1e-12 and joined.d0_rank_new.eq(joined.d0_rank_old).all()
        model_checks.append(dict(month=month, group=gid, rows=len(joined),
                                 max_score_diff=diff, model_sha256=sha(bundle_path)))
    f = pd.read_pickle(OUT / 'enriched.pkl')
    first = pd.read_pickle(OUT / 'first10.pkl')
    first['symbol'] = first.symbol.str[:6]
    bars = {(s, d0): group.sort_values('time')
            for (s, d0), group in first.groupby(KEY)}
    rules = {x['month']: x['selected'] for x in json.loads(
        (OUT / 'rules_frozen.json').read_text())}
    decisions, selected = [], []
    for day, group in f[f.part.eq('test')].groupby('d1_date'):
        d0 = group.d0_date.iloc[0]
        picks = [dict(symbol6=x.symbol, selection_rank=int(x.d0_rank),
            preclose=float(x.d1_preclose), cap=float(x.d1_cap),
            ordinary=bool(x.eligible)) for x in group.itertuples()]
        sources = {x.symbol: bars.get((x.symbol, d0), pd.DataFrame())
                   for x in group.itertuples()}
        states = evaluate_morning(picks, sources, day,
            datetime.fromisoformat(day + ' 09:40:00'), rules[d0[:7]])
        for state in states:
            decisions.append(dict(d0_date=d0, d1_date=day, **state))
            if state['actionable']:
                selected.append((d0, state['symbol6'],
                    state['entry_bar_end'][11:16], float(state['entry_price'])))
    expected = [(x.d0_date, x.symbol, x.entry_time, float(x.entry))
                for x in pd.read_pickle(OUT / 'signals.pkl').itertuples()]
    assert sorted(selected) == sorted(expected)
    pd.DataFrame(decisions).to_pickle(OUT / 'execution_decisions.pkl')
    dump(OUT / 'verification.json', dict(
        model_checks=model_checks, max_model_score_diff=max(x['max_score_diff'] for x in model_checks),
        production_evaluator_exact=len(expected), candidate_decisions=len(decisions),
        no_duplicate_trade_day=True, panel_sha256=sha(PANEL),
        minute_coverage=json.loads((OUT / 'coverage.json').read_text())))
    print('AUDIT', len(expected), 'signals', flush=True)


def report() -> None:
    from scripts.report_b2_double_shadow_full_r40 import step
    calendar = json.loads(CALENDAR.read_text())
    start, end = r41.START, r41.END
    dates = [d for d in calendar if start <= d <= end]
    novel = pd.read_pickle(OUT / 'signals.pkl')
    original = pd.read_pickle(r41.OUT / 'signals.pkl')
    plans = {'fixed_RF51': original,
             'searched_R44_risk' if OUT == RISK_OUT else 'searched_R43_hit': novel}
    monthly, yearly, overall, navs, ledgers, choices = [], [], [], [], [], []
    for name, source in plans.items():
        signals = source[source.d1_date.between(start, end)].sort_values('d1_date')
        by_day = {x['d1_date']: x for x in signals.to_dict('records')}
        paper = {'cash': 1_000_000., 'history': [1_000_000.], 'positions': []}
        gated = {'cash': 1_000_000., 'history': [1_000_000.], 'positions': []}
        selected_days = set()
        for i, day in enumerate(dates):
            prior = paper['history'][i]
            hist30 = prior / paper['history'][i-30] - 1 if i >= 30 else None
            hist10 = prior / paper['history'][i-10] - 1 if i >= 10 else None
            stop = bool(hist30 is not None and hist30 <= -.10 and
                        hist10 is not None and hist10 < .01)
            item = by_day.get(day)
            if item is not None:
                choices.append(dict(plan=name, day=day, symbol=item['symbol'],
                    entry=float(item['entry']), exit=float(item['d2_close']),
                    d2_net=float(item['d2_close'] / item['entry'] * .999 / 1.001 - 1),
                    shadow30=hist30, shadow10=hist10,
                    gated_action='skip' if stop else 'buy'))
                if not stop:
                    selected_days.add(day)
            step(paper, day, i, item, {})
            step(gated, day, i, item if not stop else None, {})
        for gate, sample in [('raw', signals),
                             ('30_10', signals[signals.d1_date.isin(selected_days)])]:
            stats, months, nav, ledger, exclusions = replay(
                sample, calendar, start=start, end=end, fee_rate=.001)
            assert not exclusions
            state = paper if gate == 'raw' else gated
            np.testing.assert_allclose(nav.nav, state['history'][1:], atol=1e-7, rtol=0)
            overall.append(dict(plan=name, gate=gate, **stats))
            monthly += [dict(plan=name, gate=gate, **x) for x in months]
            navs.append(nav.assign(plan=name, gate=gate))
            ledgers.append(ledger.assign(plan=name, gate=gate))
            previous = 1_000_000.
            for year, group in nav.groupby(nav.date.str[:4], sort=True):
                closing = float(group.nav.iloc[-1])
                yearly.append(dict(plan=name, gate=gate, year=year,
                    opening_nav=previous, closing_nav=closing,
                    return_=closing / previous - 1,
                    drawdown=drawdown(np.r_[previous, group.nav.to_numpy()]),
                    sharpe=sharpe(group.return_),
                    entries=int(ledger.entry_date.str.startswith(year).sum())))
                previous = closing
    control = json.loads((r41.REPORT / 'summary.json').read_text())
    for gate, key in [('raw', 'baseline'), ('30_10', 'historical_gate')]:
        actual = next(x for x in overall if x['plan'] == 'fixed_RF51' and x['gate'] == gate)
        for field in ('entries', 'total_return', 'max_drawdown', 'final_nav'):
            assert np.isclose(actual[field], control[key][field], atol=1e-9, rtol=0), (gate, field)
    panel = pd.read_pickle(PANEL)
    daily = []
    for month in TEST:
        choice = json.loads((OUT / 'selected' / month / 'choice.json').read_text())
        test = panel[panel.d0_date.str.startswith(month)]
        for gid in dict.fromkeys(('reference', choice['selected'])):
            candidate = next(x for x in choice['candidates'] if x['id'] == gid)
            train = block(panel, calendar, *window(month, 2)['train'])
            train_scores = group_scores(month, gid, 'train').merge(train[KEY], on=KEY,
                validate='one_to_one').merge(panel, on=KEY, validate='one_to_one')
            test_scores = group_scores(month, gid, 'forecast').merge(test[KEY], on=KEY,
                validate='one_to_one').merge(panel, on=KEY, validate='one_to_one')
            daily.append(dict(month=month, model=gid, selected=gid == choice['selected'],
                train_infit=quality(train_scores), oof=candidate['oof'],
                valid=candidate['valid'], test=quality(test_scores),
                selection_score=candidate['selection_score']))
    dump(OUT / 'overall.json', overall)
    dump(OUT / 'monthly.json', monthly)
    dump(OUT / 'yearly.json', yearly)
    dump(OUT / 'daily_model_metrics.json', daily)
    pd.concat(navs).to_pickle(OUT / 'nav.pkl')
    pd.concat(ledgers).to_pickle(OUT / 'ledger.pkl')
    pd.DataFrame(choices).to_pickle(OUT / 'trade_choices.pkl')
    print(pd.DataFrame(overall)[['plan', 'gate', 'entries', 'total_return',
                                 'max_drawdown', 'sharpe']].to_string(index=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['spec', 'train', 'choose', 'prepare',
                                           'select_rules', 'evaluate', 'audit', 'report'])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--first')
    parser.add_argument('--last')
    parser.add_argument('--wait-for-training', action='store_true')
    parser.add_argument('--risk', action='store_true',
                        help='Use predeclared R44 D1-open to D2-close proxy chooser and separate output.')
    arg = parser.parse_args()
    if arg.risk:
        OUT = RISK_OUT
    if arg.stage == 'train':
        assert not arg.risk, 'R44 reuses R43 candidate models; train only R43.'
        train(arg.workers, arg.first, arg.last)
    elif arg.stage == 'choose':
        choose(arg.first, arg.last, arg.wait_for_training)
    elif arg.stage == 'spec':
        specification()
    else:
        globals()[arg.stage]()
