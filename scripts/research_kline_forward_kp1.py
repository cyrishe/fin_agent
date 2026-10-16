"""KP1: frozen daily-pattern prevalence and forward outcomes; no model fitting.

Run as a module. Input SQL is read-only; only this experiment's local files change.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
import pandas as pd

from src.experiments.kline_patterns.catalog import CATALOG
from src.experiments.kline_patterns.engine import prepare, detect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/stock_automl/kline_survey_kp1_20261016'
REPORT = ROOT / 'docs/stock_automl_runs/20261016_kline_survey_kp1'
SOURCE_START, SOURCE_END = '2025-07-01', '2026-10-15'
START, END = '2026-04-01', '2026-09-30'
BULL = set('engulf_bull hammer_down star_down harami_bull three_bull three_hold_bull three_methods_bull morning_star morning_doji tweezer_bull piercing bull_swallow_three stick_sandwich ma5_10_up ma10_20_up ma20_60_up double_bottom volume_up_expand volume_up_contract ma20_retest ma_bull_stack macd_cross_bull macd_zero_bull macd_hist_bull rsi_reentry_bull rsi_mid_bull macd_div_bull rsi_div_bull rsi_failure_bull boll_squeeze_bull inside_break_bull'.split())
CONTEXT = set('doji spinning ma_compression volume_down_contract'.split())


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def fetch():
    from scripts.benchmark_automl_1440_inference import db_connection, query
    source = OUT / 'source'
    source.mkdir(parents=True, exist_ok=True)
    manifest = []
    with db_connection() as conn:
        for month in pd.period_range(SOURCE_START[:7], SOURCE_END[:7], freq='M'):
            path = source / f'daily_{month}.pkl.gz'
            if not path.exists():
                lo = max(SOURCE_START, str(month.start_time.date()))
                hi = min(SOURCE_END, str(month.end_time.date()))
                b = query(conn, 'SELECT trade_date,stk_code,open,high,low,close,preclose,'
                          'adjopen,adjhigh,adjlow,adjclose,volume,amount,turn_ratio,is_limit_price '
                          'FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s', (lo, hi))
                b['trade_date'] = pd.to_datetime(b.trade_date)
                for col in b.columns.difference(['trade_date', 'stk_code']):
                    b[col] = pd.to_numeric(b[col], errors='coerce').astype(float)
                b.to_pickle(path)
                print('fetched', str(month), len(b), flush=True)
            manifest.append({'path': str(path.relative_to(ROOT)), 'sha256': sha(path)})
        conn.rollback()
    dump(OUT / 'input_manifest.json', {'source': 'kingdomai.kcrp_stock_price; SELECT only',
         'fetched_at': datetime.now().astimezone().isoformat(), 'files': manifest,
         'price_basis': 'provider adj OHLC (hfq); current snapshot, not historical vintage'})


def board(code):
    if code.startswith('688') and code.endswith('.SH'): return 'STAR'
    if code.startswith('30') and code.endswith('.SZ'): return 'ChiNext'
    if code.startswith('60') and code.endswith('.SH'): return 'SH_main'
    if code.startswith('00') and code.endswith('.SZ'): return 'SZ_main'
    return None


def outcomes(f):
    """Labels use next MARKET sessions, never next available observations.

    Missing/suspended intermediate sessions invalidate that horizon. Intraday
    extrema describe opportunity/risk only, never a realizable exit strategy.
    """
    out = pd.DataFrame(index=f.index)
    complete = f.valid.eq(1)
    for h in (1, 2, 3):
        complete = complete & f.valid.shift(-h).eq(1)
        out[f'r{h}'] = (100 * (f.c.shift(-h) / f.c - 1)).where(complete)
        highs = pd.concat([f.h.shift(-j) for j in range(1, h + 1)], axis=1)
        lows = pd.concat([f.l.shift(-j) for j in range(1, h + 1)], axis=1)
        out[f'mfe{h}'] = (100 * (highs.max(axis=1, skipna=False) / f.c - 1)).where(complete)
        out[f'mae{h}'] = (100 * (lows.min(axis=1, skipna=False) / f.c - 1)).where(complete)
    # Confirmation-at-close sensitivity: buy at next open, earliest sell t+2.
    for h in (2, 3):
        out[f'nextopen_r{h}'] = (100 * (f.c.shift(-h) / f.o.shift(-1) - 1)).where(out[f'r{h}'].notna())
    return out


def chained_bars(b):
    """Causal price index from raw OHLC and the source's daily reference close.

    Avoid discontinuous provider adj-factor snapshots. A uniform scale for each
    candle preserves its geometry; consecutive close ratios equal close/preclose.
    Missing reference prices remain gaps. No source table is modified.
    """
    ratio = b.close.div(b.preclose).where(b.preclose.gt(0) & b.close.gt(0))
    index_close = 100 * ratio.cumprod()
    scale = index_close.div(b.close)
    result = b[['open', 'high', 'low', 'close']].mul(scale, axis=0)
    result['volume'] = b.volume
    result['turn_ratio'] = b.turn_ratio
    return result


def scan_one(args):
    code, b, calendar = args
    b = b.set_index('trade_date').reindex(calendar).sort_index()
    adj = chained_bars(b)
    f = prepare(adj)
    eligible = f.valid.eq(1) & f.valid.cumsum().ge(160)
    selected = eligible & (f.index >= START) & (f.index <= END)
    y = outcomes(f)
    y['code'] = code
    y['board'] = board(code)
    y['date'] = f.index
    y['limit_up'] = b.is_limit_price.eq(1) & b.close.gt(b.preclose)
    y['one_price'] = b.high.eq(b.low)
    y['amount'] = b.amount
    y['ret20'] = f.c / f.c.shift(20) - 1
    signals = {}
    for p in CATALOG:
        d = detect(p, f)
        hit = d.fillna(False).astype(bool)
        signals['hit_' + p['id']] = hit
        signals['known_' + p['id']] = d.notna()
        signals['start_' + p['id']] = hit & ~hit.shift(1, fill_value=False)
    y = pd.concat([y, pd.DataFrame(signals, index=f.index)], axis=1)
    discrepancy = ((b.adjclose / b.adjclose.shift(1) - b.close / b.preclose).abs() * 100)
    quality = {'code': code, 'source_rows': int(b.close.notna().sum()),
               'signal_rows': int(selected.sum()),
               'invalid_ohlcv_rows': int((b.close.notna() & ~f.valid.eq(1)).sum()),
               'adjusted_return_discrepancy_gt_0_1pp': int((discrepancy > .1).sum()),
               'max_adjusted_return_discrepancy_pp': float(discrepancy.max())}
    return y.loc[selected].reset_index(drop=True), quality


def scan(workers=4):
    cache = OUT/'cache_manifest.json'
    if cache.exists():
        saved = json.loads(cache.read_text())
        for name, expected in saved['file_sha256'].items():
            if sha(ROOT/name) != expected:
                raise ValueError('Frozen cache changed; use a new experiment directory: '+name)
        print('Reusing verified saved pattern matches and labels; no rescan.', flush=True)
        return
    raw = pd.concat([pd.read_pickle(p) for p in sorted((OUT / 'source').glob('*.pkl.gz'))], ignore_index=True)
    raw = raw[raw.stk_code.map(board).notna()]
    assert not raw.duplicated(['stk_code', 'trade_date']).any()
    calendar = pd.DatetimeIndex(sorted(raw.loc[raw.volume.gt(0), 'trade_date'].unique()))
    groups = [(code, b, calendar) for code, b in raw.groupby('stk_code', sort=True)]
    target = OUT / 'parts'
    target.mkdir(exist_ok=True)
    frames, quality = [], []
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, (frame, q) in enumerate(pool.map(scan_one, groups, chunksize=8), 1):
            frames.append(frame); quality.append(q)
            if i % 100 == 0 or i == len(groups):
                pd.concat(frames, ignore_index=True).to_pickle(target / f'part_{i:05d}.pkl.gz')
                frames = []
                print('scanned', i, '/', len(groups), 'seconds', round(time.perf_counter()-started), flush=True)
    pd.DataFrame(quality).to_csv(OUT / 'quality.csv', index=False)
    dump(OUT / 'scan_manifest.json', {'stocks_in_source': len(groups), 'calendar_days': len(calendar),
         'signal_start': START, 'signal_end': END, 'source_start': SOURCE_START,
         'source_end': SOURCE_END, 'seconds': time.perf_counter()-started, 'model_calls': 0,
         'calculation_price_basis': 'causal raw OHLC index chained by close/preclose; provider adj fields audited only',
         'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__,
         'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()})


def summarize():
    panel = pd.concat([pd.read_pickle(p) for p in sorted((OUT / 'parts').glob('*.pkl.gz'))], ignore_index=True)
    assert not panel.duplicated(['code', 'date']).any()
    panel['month'] = panel.date.dt.strftime('%Y-%m')
    panel['proxy_buyable'] = ~panel.limit_up & ~panel.one_price
    discrepancies = pd.read_csv(OUT/'adjustment_discrepancies.csv')
    discontinuous = set(discrepancies.loc[discrepancies.diff_pp.gt(.5), 'stk_code'])
    rows, daily, monthly, baseline = [], [], [], []
    for scope, sub in [('all', panel), ('exclude_close_limit_oneprice', panel[panel.proxy_buyable]),
                       ('exclude_adj_discontinuity_stocks', panel[~panel.code.isin(discontinuous)])]:
        for h in (1, 2, 3):
            sub = sub.copy()
            sub[f'base_r{h}'] = sub.groupby(['date', 'board'])[f'r{h}'].transform('mean')
            sub[f'excess{h}'] = sub[f'r{h}'] - sub[f'base_r{h}']
        for window, b in [('6m', sub), ('3m', sub[sub.date.ge('2026-07-01')])]:
            for h in (1, 2, 3):
                r = b[f'r{h}'].dropna()
                baseline.append({'scope': scope, 'window': window, 'horizon': h, 'n': len(r),
                                 'mean_pct': r.mean(), 'median_pct': r.median(), 'win_pct': 100*r.gt(1e-9).mean()})
            for p in CATALOG:
                pid = p['id']
                hits = b[b['hit_' + pid]]
                for mode, events in [('all_hits', hits), ('episode_starts', hits[hits['start_' + pid]])]:
                    common = {'scope': scope, 'window': window, 'mode': mode,
                              'id': pid, 'name': p['name'], 'family': p['family'],
                              'role': 'bull' if pid in BULL else 'context' if pid in CONTEXT else 'bear_control',
                              'hits': len(events), 'stocks': events.code.nunique(),
                              'days': events.date.nunique(), 'known_stock_days': int(b['known_'+pid].sum()),
                              'limit_up_pct': 100*events.limit_up.mean()}
                    for h in (1, 2, 3):
                        e = events[events[f'r{h}'].notna()]
                        r = e[f'r{h}']
                        day = e.groupby('date').agg(excess=(f'excess{h}', 'mean'), n=(f'r{h}', 'size'))
                        # Moving-block bootstrap keeps common dates and short-horizon serial dependence.
                        calendar = pd.DatetimeIndex(sorted(b.date.unique()))
                        day = day.reindex(calendar).fillna(0)
                        num = (day.excess * day.n).to_numpy(); den = day.n.to_numpy()
                        rng = np.random.default_rng(20261016)
                        starts = rng.integers(0, len(day), size=(1000, (len(day)+4)//5))
                        idx = ((starts[..., None] + np.arange(5)) % len(day)).reshape(1000, -1)[:, :len(day)]
                        draws = np.divide(num[idx].sum(axis=1), den[idx].sum(axis=1),
                                          out=np.full(1000, np.nan), where=den[idx].sum(axis=1)>0)
                        ci = np.nanquantile(draws, [.025, .975]) if len(r) else [np.nan]*2
                        row = {**common, 'horizon': h, 'n': len(r), 'missing_labels': len(events)-len(r),
                               'mean_pct': r.mean(), 'median_pct': r.median(), 'win_pct': 100*r.gt(1e-9).mean(),
                               'p10_pct': r.quantile(.1), 'p90_pct': r.quantile(.9),
                               'loss3_pct': 100*r.le(-3).mean(), 'gain3_pct': 100*r.ge(3).mean(),
                               'excess_pp': e[f'excess{h}'].mean(), 'excess_ci_low': ci[0], 'excess_ci_high': ci[1],
                               'mfe_mean_pct': e[f'mfe{h}'].mean(), 'mae_mean_pct': e[f'mae{h}'].mean(),
                               'touch_plus3_pct': 100*e[f'mfe{h}'].ge(3).mean(),
                               'touch_minus3_pct': 100*e[f'mae{h}'].le(-3).mean(),
                               'nextopen_mean_pct': e[f'nextopen_r{h}'].mean() if h>=2 else np.nan}
                        rows.append(row)
                        if scope=='all' and window=='6m' and mode=='all_hits':
                            for month, g in e.groupby('month'):
                                monthly.append({'id': pid, 'name': p['name'], 'horizon': h, 'month': month,
                                    'n': len(g), 'mean_pct': g[f'r{h}'].mean(), 'win_pct': 100*g[f'r{h}'].gt(1e-9).mean(),
                                    'excess_pp': g[f'excess{h}'].mean()})
                            for date, g in e.groupby('date'):
                                daily.append({'id': pid, 'horizon': h, 'date': str(date.date()),
                                    'n': len(g), 'mean_pct': g[f'r{h}'].mean(), 'excess_pp': g[f'excess{h}'].mean()})
    REPORT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(REPORT/'summary.csv', index=False)
    pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv', index=False)
    pd.DataFrame(daily).to_csv(REPORT/'daily.csv.gz', index=False)
    pd.DataFrame(baseline).to_csv(REPORT/'baseline.csv', index=False)
    dump(REPORT/'definitions.json', CATALOG)
    dump(REPORT/'coverage.json', {'eligible_stock_days': len(panel), 'stocks': panel.code.nunique(),
         'dates': panel.date.nunique(), 'start': panel.date.min(), 'end': panel.date.max(),
         'complete_d3': int(panel.r3.notna().sum()), 'close_limit_up': int(panel.limit_up.sum()),
         'one_price': int(panel.one_price.sum()), 'board_counts': panel.groupby('board').size().to_dict(),
         'adj_discontinuity_stocks_excluded_in_sensitivity': sorted(discontinuous),
         'month_counts': panel.groupby('month').size().to_dict()})
    print('summary complete', len(rows), flush=True)


def snapshot():
    paths = [Path(__file__), ROOT/'src/experiments/kline_patterns/catalog.py',
             ROOT/'src/experiments/kline_patterns/engine.py', ROOT/'src/services/technical_indicator_calculator.py',
             ROOT/'scripts/select_kline_candidates_kp1.py', ROOT/'scripts/verify_kline_forward_kp1.py',
             ROOT/'tests/automl/test_kline_forward_kp1.py']
    for path in paths:
        target = REPORT/'source_snapshot'/path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    dump(REPORT/'source_hashes.json', {str(p.relative_to(ROOT)): sha(p) for p in paths})


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['fetch', 'scan', 'summarize', 'snapshot'])
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()
    if args.action == 'scan': scan(args.workers)
    else: globals()[args.action]()
