"""R46: causal daily refit of the frozen B2 RF51 first stage.

Research only. Each D0 model is fitted after that day's close using only
historical samples whose D4 outcome has matured before the training cutoff.
The 6+2 and direct-six-month variants use the same RF and daily candidate pool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.experiment_limit_rebound_two_stage_r15 import rank, classify
from scripts.replay_limit_rebound_cash_r23 import dump, sha

ROOT = Path('outputs/stock_automl/b2_daily_refit_r46_20261020')
SOURCE = Path('outputs/stock_automl/b2_2022_minute_r41_20261019')
PANEL = SOURCE / 'panel.pkl'
CALENDAR = SOURCE / 'calendar.json'
FIRST = '2022-09-01'
LAST = '2026-09-30'
FEATURES51 = FEATURES['segments']
MODES = ('gap2', 'direct6')
_panel: pd.DataFrame | None = None
_calendar: list[str] | None = None


def finished(root: Path) -> bool:
    return all((root / name).exists() for name in
               ('complete.json', 'model.joblib', 'scores.pkl', 'training_idx.npy'))


def spec() -> dict:
    assert sklearn.__version__ == '1.7.1', sklearn.__version__
    ROOT.mkdir(parents=True, exist_ok=True)
    s = dict(
        experiment='R46 daily RF51 refit before shape comparison; research only',
        source_panel=str(PANEL), panel_sha256=sha(PANEL),
        source_calendar=str(CALENDAR), calendar_sha256=sha(CALENDAR),
        period=[FIRST, LAST], modes=list(MODES), features=FEATURES51,
        model='Original B2 RF240 depth7 leaf50 max_features .8 seed 20261013; median imputation, missing indicators, balanced sample weights, sklearn 1.7.1.',
        gap2='For each D0, six calendar months ending two calendar months before D0 are fitted. The next two months are scored for descriptive validation only, never used to choose or refit the fixed model.',
        direct6='For each D0, latest six calendar months before D0 are fitted. No separate first-stage validation period.',
        maturity='Only D0 rows with D4 outcome mature by the last session before training-end boundary. Purge active board events at that boundary. D0 forecast never filters by future D1 activation.',
        next='Historical daily forecast ranks form causal shape-train and shape-valid pools. Shape six-train/one-valid and six-train/two-valid will be compared with original four-bar entry and D2 close exit.',
        production_changed=False, prior_history_studied=True,
    )
    p = ROOT / 'specification.json'
    if p.exists():
        assert json.loads(p.read_text()) == s
    else:
        dump(p, s)
    return s


def init_worker() -> None:
    global _panel, _calendar
    _panel = pd.read_pickle(PANEL)
    _calendar = json.loads(CALENDAR.read_text())


def one(day: str, mode: str) -> tuple[str, str, int, float]:
    assert _panel is not None and _calendar is not None
    root = ROOT / mode / day
    manifest = root / 'complete.json'
    if finished(root):
        return day, mode, -1, 0.
    root.mkdir(parents=True, exist_ok=True)
    panel, calendar = _panel, _calendar
    when = pd.Timestamp(day)
    train_start = str((when - pd.DateOffset(months=8 if mode == 'gap2' else 6)).date())
    train_end = str((when - pd.DateOffset(months=2)).date()) if mode == 'gap2' else day
    end_i = int(np.searchsorted(calendar, train_end) - 1)
    start_i = int(np.searchsorted(calendar, train_start))
    fitmask = (panel.date_i.ge(start_i) & panel.date_i.le(end_i - 4)
               & panel.activated & panel.observed_3)
    boundary = panel[panel.date_i.eq(end_i)]
    active = pd.MultiIndex.from_frame(boundary[['symbol', 'first_limit_date']])
    candidate = panel.loc[fitmask]
    overlap = pd.MultiIndex.from_frame(candidate[['symbol', 'first_limit_date']]).isin(active)
    fit = candidate.loc[~overlap]
    assert len(fit) >= 100 and fit.label_3.nunique() == 2, (day, mode, len(fit))
    assert fit.date_i.add(4).le(end_i).all()
    assert fit.d0_date.max() < train_end <= day
    model = make_model('rf')
    model.steps[-1][1].n_jobs = 1
    model.fit(xmatrix(fit, FEATURES51), fit.label_3,
              randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
    today = panel[panel.d0_date.eq(day)]
    scored = rank(today, model.predict_proba(xmatrix(today, FEATURES51))[:, 1])
    score_path = root / 'scores.pkl'
    scored[['symbol', 'd0_date', 'score', 'd0_rank']].to_pickle(score_path)
    training_idx = fit.index.to_numpy(dtype=np.int32)
    np.save(root / 'training_idx.npy', training_idx)
    bundle = dict(model=model, mode=mode, forecast_day=day,
                  training_start=train_start, training_end_exclusive=train_end,
                  features=FEATURES51, sklearn=sklearn.__version__,
                  panel_sha256=sha(PANEL), sample_index_sha256=sha(root / 'training_idx.npy'))
    joblib.dump(bundle, root / 'model.joblib', compress=3)
    restored = joblib.load(root / 'model.joblib')['model']
    check = restored.predict_proba(xmatrix(today, FEATURES51))[:, 1]
    diff = float(np.max(np.abs(scored.sort_index().score.to_numpy() -
                               pd.Series(check, index=today.index).sort_index().to_numpy())))
    assert diff < 1e-12, diff
    valid_stats = None
    if mode == 'gap2':
        valid = panel[(panel.d0_date.ge(train_end)) & (panel.d0_date.lt(day))
                      & panel.date_i.add(4).lt(int(np.searchsorted(calendar, day)))]
        if len(valid):
            valid_ranked = rank(valid, restored.predict_proba(xmatrix(valid, FEATURES51))[:, 1])
            valid_stats = classify(valid_ranked)['open']
    keys = fit[['symbol', 'd0_date', 'first_limit_date']].to_csv(index=False).encode()
    dump(manifest, dict(day=day, mode=mode, train_start=train_start,
        train_end_exclusive=train_end, train_rows=len(fit),
        training_keys_sha256=hashlib.sha256(keys).hexdigest(),
        training_index_sha256=sha(root / 'training_idx.npy'),
        model_sha256=sha(root / 'model.joblib'), score_sha256=sha(score_path),
        forecast_rows=len(today), score_reload_max_diff=diff,
        valid_open=valid_stats, test_results_seen=False,
        specification_sha256=sha(ROOT / 'specification.json')))
    return day, mode, len(fit), diff


def fit(workers: int, first: str | None, last: str | None) -> None:
    spec()
    calendar = json.loads(CALENDAR.read_text())
    days = [d for d in calendar if FIRST <= d <= LAST
            and (first is None or d >= first) and (last is None or d <= last)]
    jobs = [(d, mode) for d in days for mode in MODES
            if not finished(ROOT / mode / d)]
    print('FIT jobs', len(jobs), 'days', len(days), 'workers', workers, flush=True)
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker) as pool:
        futures = [pool.submit(one, day, mode) for day, mode in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            day, mode, n, diff = future.result()
            if i % 25 == 0 or i == len(jobs):
                print('FIT', i, '/', len(jobs), day, mode, n, diff, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['spec', 'fit'])
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--first')
    parser.add_argument('--last')
    args = parser.parse_args()
    {'spec': spec, 'fit': lambda: fit(args.workers, args.first, args.last)}[args.stage]()
