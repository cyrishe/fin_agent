"""R4: historical-ST and size-feature controls for the three-day rebound model.

All variants use the R2 event-safe time split and the same shallow boosting
configuration. Only training weights and feature inputs differ. The test period
is scored once after the variants have been fixed.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, brier_score_loss, f1_score, precision_score, recall_score, roc_auc_score

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import KEY
from scripts.experiment_automl_limit_rebound_2y import BASE
from scripts.experiment_automl_limit_rebound_3day import split
from scripts.experiment_automl_limit_rebound_model import make_model


SOURCE = Path('outputs/stock_automl/limit_rebound_2y_r1')
OUT = Path('outputs/stock_automl/limit_rebound_st_size_r4_20261014')
TARGET = 'label_next3_any_limit_up'
EXTRA = ('day_amount_100m', 'total_mv_100m')
SCALE = ('day_amount_100m', 'day_turnover_pct', 'total_mv_100m')
VARIANTS = (
    ('R2_reference', 'all', 'base', False),
    ('balanced_all_base', 'all', 'base', True),
    ('balanced_nonst_base', 'nonst', 'base', True),
    ('balanced_all_amount_only', 'all', 'amount_only', True),
    ('balanced_nonst_amount_only', 'nonst', 'amount_only', True),
    ('balanced_all_raw', 'all', 'raw', True),
    ('balanced_all_normalized', 'all', 'normalized', True),
    ('balanced_nonst_raw', 'nonst', 'raw', True),
    ('balanced_nonst_normalized', 'nonst', 'normalized', True),
)


def fetch_market_cap(panel: pd.DataFrame, out: Path) -> pd.DataFrame:
    root = out / 'market_cap_months'
    root.mkdir(parents=True, exist_ok=True)
    wanted = set(zip(panel.asof_date, panel.symbol))
    parts = []
    for month in pd.period_range(panel.asof_date.min()[:7], panel.asof_date.max()[:7], freq='M'):
        path = root / f'total_mv_{month}.csv.gz'
        if not path.exists():
            lo = max(panel.asof_date.min(), str(month.start_time.date()))
            hi = min(panel.asof_date.max(), str(month.end_time.date()))
            with db_connection() as conn:
                raw = query(conn, 'SELECT trade_date,stk_code,total_mv FROM kcrp_stock_pricevaluate '
                                  'WHERE trade_date BETWEEN %s AND %s', (lo, hi))
                conn.rollback()
            raw['asof_date'] = raw.trade_date.astype(str)
            raw['symbol'] = raw.stk_code.str[:6]
            found = [key in wanted for key in zip(raw.asof_date, raw.symbol)]
            raw = raw.loc[found, ['asof_date', 'symbol', 'total_mv']]
            raw.to_csv(path, index=False, compression='gzip')
            print('market cap', month, len(raw), flush=True)
        parts.append(pd.read_csv(path, dtype={'asof_date': str, 'symbol': str}))
    result = pd.concat(parts, ignore_index=True)
    if result.duplicated(['asof_date', 'symbol']).any():
        raise ValueError('Duplicate historical market cap stock-date')
    return result


def historical_status(panel: pd.DataFrame, out: Path) -> pd.DataFrame:
    path = out / 'historical_st.csv.gz'
    if path.exists():
        return pd.read_csv(path, dtype={'symbol': str, 'asof_date': str})
    with db_connection() as conn:
        raw = query(conn, 'SELECT stk_code,st_type,begin_date,end_date FROM kcrp_stock_st')
        conn.rollback()
    raw['symbol'] = raw.stk_code.str[:6]
    intervals = {symbol: list(zip(group.begin_date.astype(str), group.end_date.astype(str), group.st_type))
                 for symbol, group in raw.groupby('symbol')}
    unique = panel[['symbol', 'asof_date']].drop_duplicates().copy()
    types = []
    for symbol, day in unique.itertuples(index=False):
        matches = [kind for begin, end, kind in intervals.get(symbol, ()) if begin <= day < end]
        if len(matches) != 1:
            raise ValueError(f'Historical ST status missing or duplicated: {symbol} {day}: {matches}')
        types.append(matches[0])
    unique['st_type'] = types
    unique['is_st'] = unique.st_type.isin(('S', 'Y')).astype(int)
    unique.to_csv(path, index=False, compression='gzip')
    return unique


def recent_share_history(panel: pd.DataFrame, out: Path) -> dict:
    """Keep each month's initial, changed, and final share counts for as-of lookup."""
    root = out / 'share_months'
    root.mkdir(parents=True, exist_ok=True)
    symbols = set(panel.symbol)
    parts = []
    start = (pd.Period(panel.asof_date.min()[:7], freq='M') - 1)
    for month in pd.period_range(start, panel.asof_date.max()[:7], freq='M'):
        path = root / f'total_share_{month}.csv.gz'
        if not path.exists():
            lo = str(month.start_time.date())
            hi = min(panel.asof_date.max(), str(month.end_time.date()))
            with db_connection() as conn:
                raw = query(conn, 'SELECT trade_date,stk_code,total_share FROM kcrp_stock_pricevaluate '
                                  'WHERE trade_date BETWEEN %s AND %s AND total_share>0', (lo, hi))
                conn.rollback()
            raw['symbol'] = raw.stk_code.str[:6]
            raw = raw[raw.symbol.isin(symbols)].copy()
            raw['trade_date'] = raw.trade_date.astype(str)
            raw['total_share'] = pd.to_numeric(raw.total_share)
            raw = raw.sort_values(['symbol', 'trade_date'])
            first_or_changed = raw.total_share.ne(raw.groupby('symbol').total_share.shift())
            last = raw.trade_date.eq(raw.groupby('symbol').trade_date.transform('max'))
            raw = raw.loc[first_or_changed | last, ['symbol', 'trade_date', 'total_share']]
            raw.to_csv(path, index=False, compression='gzip')
            print('share history', month, len(raw), flush=True)
        parts.append(pd.read_csv(path, dtype={'symbol': str, 'trade_date': str}))
    history = pd.concat(parts, ignore_index=True).sort_values(['symbol', 'trade_date'])
    if history.duplicated(['symbol', 'trade_date']).any():
        raise ValueError('Duplicate share stock-date')
    return {symbol: (group.trade_date.tolist(), group.total_share.to_numpy(dtype=float))
            for symbol, group in history.groupby('symbol')}


def decision_close(panel: pd.DataFrame) -> pd.DataFrame:
    wanted = set(zip(panel.asof_date, panel.symbol))
    parts = []
    for path in sorted((SOURCE / 'price_months').glob('price_*.csv.gz')):
        raw = pd.read_csv(path, usecols=['trade_date', 'stk_code', 'close'],
                          dtype={'trade_date': str, 'stk_code': str})
        raw['symbol'] = raw.stk_code.str[:6]
        found = [key in wanted for key in zip(raw.trade_date, raw.symbol)]
        parts.append(raw.loc[found, ['trade_date', 'symbol', 'close']])
    result = pd.concat(parts, ignore_index=True).rename(columns={'trade_date': 'asof_date'})
    if result.duplicated(['symbol', 'asof_date']).any():
        raise ValueError('Duplicate quote stock-date')
    return result


def balanced_weights(y: pd.Series) -> np.ndarray:
    count = y.value_counts()
    return np.where(y.to_numpy() == 1, len(y) / (2 * count[1]), len(y) / (2 * count[0]))


def inputs(train: pd.DataFrame, others: tuple[pd.DataFrame, ...], mode: str):
    columns = list(BASE) + (['day_amount_100m'] if mode == 'amount_only' else
                            list(EXTRA) if mode in ('raw', 'normalized') else [])
    train_x = train[columns].copy()
    other_x = [frame[columns].copy() for frame in others]
    if mode == 'normalized':
        # All transformations are monotonic; HGB should be largely invariant.
        for col in SCALE:
            values = np.log1p(train_x[col].clip(lower=0))
            mean = float(values.mean())
            std = float(values.std(ddof=0)) or 1.0
            train_x[col] = (values - mean) / std
            for frame in other_x:
                frame[col] = (np.log1p(frame[col].clip(lower=0)) - mean) / std
    return train_x, other_x, columns


def scores_for_fit(train: pd.DataFrame, others: tuple[pd.DataFrame, ...], mode: str, balance: bool):
    x, other_x, columns = inputs(train, others, mode)
    model = make_model('shallow_boost')
    kwargs = {'sample_weight': balanced_weights(train[TARGET])} if balance else {}
    model.fit(x, train[TARGET], **kwargs)
    return [model.predict_proba(frame)[:, 1] for frame in other_x], columns


def metrics(frame: pd.DataFrame, scores: np.ndarray) -> dict:
    y = frame[TARGET].to_numpy(dtype=int)
    chosen = scores >= .5
    ranked = frame[['symbol', 'first_limit_date', 'asof_date', TARGET, 'is_st']].copy()
    ranked['score'] = scores
    ranked = ranked.sort_values(['asof_date', 'score', 'symbol', 'first_limit_date'],
                                ascending=[True, False, True, True])
    ranked['rank'] = ranked.groupby('asof_date').cumcount() + 1
    top = {}
    for k in (1, 2, 3):
        picks = ranked[ranked['rank'].le(k)]
        top[str(k)] = {'days': int(picks.asof_date.nunique()), 'picks': len(picks),
                       'hits': int(picks[TARGET].sum()), 'hit_rate': float(picks[TARGET].mean()),
                       'st_picks': int(picks.is_st.sum())}
    return {'n': len(y), 'positives': int(y.sum()), 'prevalence': float(y.mean()),
            'ap': float(average_precision_score(y, scores)),
            'auc': float(roc_auc_score(y, scores)),
            'brier': float(brier_score_loss(y, scores)),
            'threshold_0_5': {'precision': float(precision_score(y, chosen, zero_division=0)),
                              'recall': float(recall_score(y, chosen, zero_division=0)),
                              'f1': float(f1_score(y, chosen, zero_division=0))},
            'top': top}


def run(out: Path = OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    features = pd.read_csv(SOURCE / 'features.csv.gz', dtype={'symbol': str, 'asof_date': str})
    labels = pd.read_csv(SOURCE / 'labels.csv.gz', dtype={'symbol': str, 'asof_date': str})
    if features.duplicated(KEY).any() or labels.duplicated(KEY).any():
        raise ValueError('Duplicate panel key')
    panel = features.merge(labels, on=KEY, validate='one_to_one')
    panel = panel[panel.label_next3_observed.eq(True)].copy()
    panel[TARGET] = panel[TARGET].astype(int)
    status = historical_status(panel, out)
    panel = panel.merge(status, on=['symbol', 'asof_date'], validate='many_to_one')
    market_cap = fetch_market_cap(panel, out)
    panel = panel.merge(market_cap, on=['symbol', 'asof_date'], how='left', validate='many_to_one')
    panel = panel.merge(decision_close(panel), on=['symbol', 'asof_date'],
                        how='left', validate='many_to_one')
    shares = recent_share_history(panel, out)
    latest_share, share_age = [], []
    for symbol, day in zip(panel.symbol, panel.asof_date):
        history = shares.get(symbol)
        position = bisect_right(history[0], day) - 1 if history else -1
        latest_share.append(history[1][position] if position >= 0 else np.nan)
        share_age.append((pd.Timestamp(day) - pd.Timestamp(history[0][position])).days
                         if position >= 0 else np.nan)
    panel['share_age_days'] = share_age
    actual = pd.to_numeric(panel.total_mv, errors='coerce')
    estimated = np.asarray(latest_share) * panel.close.to_numpy(dtype=float)
    panel['total_mv_100m'] = actual.where(actual.gt(0), estimated) / 1e8
    panel['mv_is_estimated'] = (~actual.gt(0) & panel.total_mv_100m.notna()).astype(int)
    panel['day_amount_100m'] = panel.day_amount / 1e8
    coverage = float(panel.total_mv_100m.notna().mean())
    actual_coverage = float(actual.gt(0).mean())
    age = panel.loc[panel.mv_is_estimated.eq(1), 'share_age_days']
    print('panel', len(panel), 'total market cap coverage', coverage,
          'actual coverage', actual_coverage, 'estimate age median/max',
          age.median(), age.max(), flush=True)
    market_cap_quality = {'coverage': coverage, 'actual_coverage': actual_coverage,
                          'estimated_rows': int(panel.mv_is_estimated.sum()),
                          'estimate_age_days_median': float(age.median()),
                          'estimate_age_days_p95': float(age.quantile(.95)),
                          'estimate_age_days_max': float(age.max())}
    (out / 'market_cap_quality.json').write_text(json.dumps(market_cap_quality, indent=2) + '\n')
    train_all, valid_all, refit_all, test_all = split(panel)
    results = {}
    for title, universe, mode, balance in VARIANTS:
        filt = (lambda frame: frame[frame.is_st.eq(0)].copy()) if universe == 'nonst' else (lambda frame: frame.copy())
        train, valid, refit, test = map(filt, (train_all, valid_all, refit_all, test_all))
        (train_score, valid_score), columns = scores_for_fit(train, (train, valid), mode, balance)
        (test_score,), _ = scores_for_fit(refit, (test,), mode, balance)
        results[title] = {'universe': universe, 'features': columns, 'preprocess': mode,
                          'balanced_train': balance, 'train': metrics(train, train_score),
                          'valid': metrics(valid, valid_score), 'test': metrics(test, test_score)}
        for label, frame, score in (('valid', valid, valid_score), ('test', test, test_score)):
            scored = frame[[*KEY, 'is_st', TARGET]].copy()
            scored['score'] = score
            scored.to_csv(out / f'{title}_{label}_scores.csv.gz', index=False, compression='gzip')
        print(title, 'test AP', results[title]['test']['ap'],
              'top3', results[title]['test']['top']['3']['hits'], flush=True)

    reference = results['R2_reference']
    expected = [('train', 552, 1068), ('valid', 68, 171), ('test', 63, 198)]
    for part, hits, picks in expected:
        observed = reference[part]['top']['3']
        if (observed['hits'], observed['picks']) != (hits, picks):
            raise AssertionError(f'R2 reproduction failed: {part} {observed}')
    summary = {'experiment': 'R4_ST_balance_size_normalization',
               'target': TARGET, 'sklearn_version': sklearn.__version__,
               'source_sha256': {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
                                 for name in ('features.csv.gz', 'labels.csv.gz')},
               'source_end': '2026-10-13', 'market_cap_source': 'kingdomai.kcrp_stock_pricevaluate.total_mv; '
                                    'missing days estimated using latest prior total_share times current close',
               'st_source': 'kingdomai.kcrp_stock_st, half-open [begin_date,end_date), S/Y=ST',
               'market_cap_quality': market_cap_quality,
               'split': {name: {'start': frame.asof_date.min(), 'end': frame.asof_date.max(), 'n': len(frame)}
                         for name, frame in [('train', train_all), ('valid', valid_all), ('refit', refit_all), ('test', test_all)]},
               'variants': results, 'generated_at_utc': datetime.now(timezone.utc).isoformat()}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    run(args.out)
