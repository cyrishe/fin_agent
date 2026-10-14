"""C1430-A: score the first possible cooling day at 14:30 using only bars so far.

D0 is any standard sealed-limit close. D1 is eligible if its 14:30 minute
close is below the D1 limit price. Labels begin on D2, after the decision day.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_automl_limit_rebound_d0_open_r5 import (
    FEATURES as PRIOR_FEATURES, build_panel, load_daily,
)
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.mk_minute_archive import day_file


OUT = Path('outputs/stock_automl/limit_rebound_1430_r7_20261014')
MINUTE_ROOT = Path('outputs/stock_automl/mk_1m_compact')
PRIOR_ST = Path('outputs/stock_automl/limit_rebound_d0_open_r5_20261014/historical_st.csv.gz')
CUTOFF = '14:30:00'
INTRADAY_FEATURES = (
    'd1_open_gap', 'd1_1430_return', 'd1_1430_from_open',
    'd1_high_return', 'd1_low_return', 'd1_range',
    'd1_close_position', 'd1_1430_vs_vwap',
    'd1_morning_return', 'd1_afternoon_return',
    'd1_first60_amount_fraction', 'd1_amount_ratio_d0',
)


def extract_prefix(bars: pd.DataFrame, d0_close: float, d0_amount: float) -> dict | None:
    """Use exactly 09:31–14:30; later bars cannot affect eligibility/features."""
    bars = bars.copy()
    bars['clock'] = pd.to_datetime(bars.time).dt.strftime('%H:%M:%S')
    bars = bars[bars.clock.le(CUTOFF)].sort_values('clock')
    expected = [t.strftime('%H:%M:%S') for t in pd.date_range('09:31', '11:30', freq='min')]
    expected += [t.strftime('%H:%M:%S') for t in pd.date_range('13:01', '14:30', freq='min')]
    if bars.clock.tolist() != expected:
        return None
    x = bars[['open', 'high', 'low', 'close', 'volume', 'turnover']].apply(pd.to_numeric, errors='coerce')
    if (x.isna().any().any() or x[['open', 'high', 'low', 'close']].le(0).any().any()
            or x[['volume', 'turnover']].lt(0).any().any()
            or x.volume.sum() <= 0 or x.turnover.sum() <= 0):
        return None
    first_open = float(x.open.iloc[0])
    last = float(x.close.iloc[-1])
    high = float(x.high.max())
    low = float(x.low.min())
    vwap = float(x.turnover.sum() / x.volume.sum())
    return {
        'd1_1430_price': last,
        'd1_open_gap': first_open / d0_close - 1,
        'd1_1430_return': last / d0_close - 1,
        'd1_1430_from_open': last / first_open - 1,
        'd1_high_return': high / d0_close - 1,
        'd1_low_return': low / d0_close - 1,
        'd1_range': (high - low) / d0_close,
        'd1_close_position': (last - low) / (high - low) if high > low else np.nan,
        'd1_1430_vs_vwap': last / vwap - 1,
        'd1_morning_return': float(x.close.iloc[119] / first_open - 1),
        'd1_afternoon_return': last / float(x.close.iloc[119]) - 1,
        'd1_first60_amount_fraction': float(x.turnover.iloc[:60].sum() / x.turnover.sum()),
        'd1_amount_ratio_d0': float(x.turnover.sum() / d0_amount) if d0_amount > 0 else np.nan,
    }


def scan_local_minutes(panel: pd.DataFrame, minute_root: Path = MINUTE_ROOT) -> pd.DataFrame:
    wanted = {(day, symbol): (float(close), float(amount)) for day, symbol, close, amount
              in panel[['d1_date', 'symbol', 'close', 'amount']].itertuples(index=False, name=None)}
    by_day = panel.groupby('d1_date').symbol.apply(set).to_dict()
    rows = []
    for n, (day, symbols) in enumerate(sorted(by_day.items()), 1):
        try:
            path = day_file(minute_root, day)
        except FileNotFoundError:
            continue
        parts = []
        for chunk in pd.read_csv(path, dtype={'symbol': str}, chunksize=200_000):
            selected = chunk[chunk.symbol.str[:6].isin(symbols) & chunk.time.str[-8:].le(CUTOFF)]
            if not selected.empty:
                parts.append(selected)
        if parts:
            for full_symbol, bars in pd.concat(parts).groupby('symbol'):
                symbol = str(full_symbol)[:6]
                prior = wanted.get((day, symbol))
                if prior is None:
                    continue
                features = extract_prefix(bars, *prior)
                if features is not None:
                    rows.append({'d1_date': day, 'symbol': symbol, **features})
        if n % 20 == 0:
            print(f'14:30 minute files {n}/{len(by_day)}; valid candidate bars {len(rows)}', flush=True)
    return pd.DataFrame(rows)


def add_labels(panel: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    quote = daily.set_index(['symbol', 'date_i'])['up']
    for gap in (2, 3, 4):
        x = quote.reindex(pd.MultiIndex.from_arrays([panel.symbol, panel.date_i + gap])).to_numpy()
        panel[f'd{gap}_observed'] = pd.notna(x)
        panel[f'd{gap}_up'] = pd.Series(x).eq(True).to_numpy(dtype=bool)
    panel['label_next1'] = panel.d2_up.astype(int)
    panel['label_next3'] = (panel.d2_up | panel.d3_up | panel.d4_up).astype(int)
    panel['label_next3_after_cold_close'] = (panel.d1_up.eq(False) &
                                               panel.label_next3.eq(1)).astype(int)
    panel['observed_next1'] = panel.d2_observed
    panel['observed_next3'] = panel[['d2_observed', 'd3_observed', 'd4_observed']].all(axis=1)
    return panel


def split(panel: pd.DataFrame, calendar: list[str], horizon: int):
    day_i = {day: i for i, day in enumerate(calendar)}
    valid_start, test_start = day_i['2026-04-01'], day_i['2026-06-01']
    label = 'next1' if horizon == 1 else 'next3'
    rows = panel[panel[f'observed_{label}']].copy()
    train = rows[rows.d1_i.add(horizon).lt(valid_start)].copy()
    valid = rows[rows.d1_i.ge(valid_start) & rows.d1_i.add(horizon).lt(test_start)].copy()
    test = rows[rows.d1_i.ge(test_start)].copy()
    valid_events = set(zip(valid.symbol, valid.first_limit_date))
    test_events = set(zip(test.symbol, test.first_limit_date))
    train = train[[key not in valid_events for key in zip(train.symbol, train.first_limit_date)]]
    refit = rows[rows.d1_i.add(horizon).lt(test_start)].copy()
    refit = refit[[key not in test_events for key in zip(refit.symbol, refit.first_limit_date)]]
    if (set(zip(train.symbol, train.first_limit_date)) & valid_events or
            set(zip(refit.symbol, refit.first_limit_date)) & test_events):
        raise AssertionError('One board event crosses a split boundary')
    return train, valid, refit, test


def metrics(frame: pd.DataFrame, score: np.ndarray, target: str) -> dict:
    y = frame[target].to_numpy(dtype=int)
    ranked = frame[['symbol', 'd1_date', 'd1_up', target]].copy()
    ranked['score'] = score
    ranked = ranked.sort_values(['d1_date', 'score', 'symbol'], ascending=[True, False, True])
    top = {}
    for k in (1, 3, 5):
        picks = ranked.groupby('d1_date').head(k)
        top[str(k)] = {'hits': int(picks[target].sum()), 'picks': len(picks),
                       'days': int(picks.d1_date.nunique()),
                       'd1_close_reboard': int(picks.d1_up.sum())}
    return {'rows': len(frame), 'positive': int(y.sum()),
            'ap': float(average_precision_score(y, score)),
            'auc': float(roc_auc_score(y, score)), 'top': top,
            'random_top1_expected_hits': float(frame.groupby('d1_date')[target].mean().sum())}


def run(out: Path = OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if PRIOR_ST.exists() and not (out / 'historical_st.csv.gz').exists():
        (out / 'historical_st.csv.gz').write_bytes(PRIOR_ST.read_bytes())
    daily = load_daily()
    calendar = sorted(daily.trade_date.unique())
    panel, quality = build_panel(daily, out)
    panel = panel[panel.d1_date.between('2026-01-05', '2026-07-31')].copy()
    panel['d1_i'] = panel.date_i + 1
    cache = out / 'minute_1430_features.csv.gz'
    if cache.exists():
        intraday = pd.read_csv(cache, dtype={'symbol': str, 'd1_date': str})
    else:
        intraday = scan_local_minutes(panel)
        intraday.to_csv(cache, index=False, compression='gzip')
    panel = panel.merge(intraday, on=['symbol', 'd1_date'], how='inner', validate='one_to_one')
    panel = panel[panel.d1_1430_price.lt(panel.d1_cap - .005)].copy()
    panel = add_labels(panel, daily)
    if panel.empty:
        raise ValueError('No eligible 14:30 decision rows')
    if any(name.startswith('d1_') for name in PRIOR_FEATURES):
        raise AssertionError('D1 feature in prior-day comparator')
    result = {'experiment': 'C1430-A_first_possible_cooling_day_1430',
              'source_quality': quality, 'minute_rows': len(intraday),
              'eligible_rows': len(panel),
              'd1_close_reboard_rows': int(panel.d1_up.sum()),
              'sklearn': sklearn.__version__,
              'cutoff': CUTOFF, 'prior_features': PRIOR_FEATURES,
              'intraday_features': INTRADAY_FEATURES, 'targets': {},
              'source_manifest_sha256': hashlib.sha256(Path('outputs/stock_automl/limit_rebound_2y_r1/manifest.json').read_bytes()).hexdigest(),
              'historical_st_sha256': hashlib.sha256((out / 'historical_st.csv.gz').read_bytes()).hexdigest(),
              'minute_cache_sha256': hashlib.sha256(cache.read_bytes()).hexdigest()}
    for horizon, target in ((1, 'label_next1'), (3, 'label_next3'),
                            (3, 'label_next3_after_cold_close')):
        train, valid, refit, test = split(panel, calendar, horizon)
        target_key = 'next3_after_cold_close' if target.endswith('after_cold_close') else f'next{horizon}'
        result['targets'][target_key] = {'train_rows': len(train), 'refit_rows': len(refit), 'models': {}}
        for name, cols in (('d0_only', PRIOR_FEATURES),
                           ('d0_plus_1430', PRIOR_FEATURES + INTRADAY_FEATURES)):
            model = make_model('shallow_boost')
            model.fit(train[list(cols)], train[target], sample_weight=balanced_weights(train[target]))
            train_score = model.predict_proba(train[list(cols)])[:, 1]
            valid_score = model.predict_proba(valid[list(cols)])[:, 1]
            final = make_model('shallow_boost')
            final.fit(refit[list(cols)], refit[target], sample_weight=balanced_weights(refit[target]))
            test_score = final.predict_proba(test[list(cols)])[:, 1]
            result['targets'][target_key]['models'][name] = {
                'train_fit_in': metrics(train, train_score, target),
                'valid': metrics(valid, valid_score, target),
                'test': metrics(test, test_score, target),
            }
            for part, frame, score in (('valid', valid, valid_score), ('test', test, test_score)):
                x = frame[['symbol', 'd0_date', 'd1_date', 'first_limit_date',
                           'd1_1430_price', 'd1_up', target]].copy()
                x['score'] = score
                x.to_csv(out / f'{target_key}_{name}_{part}.csv.gz', index=False, compression='gzip')
            print(target_key, name, 'valid/test top1',
                  result['targets'][target_key]['models'][name]['valid']['top']['1'],
                  result['targets'][target_key]['models'][name]['test']['top']['1'], flush=True)
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    run()
