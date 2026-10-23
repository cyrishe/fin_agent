"""R5: score a sealed board at D0 close, rank stocks activatable at D1 open.

Targets are a new sealed close within 3/4/5 market days beginning with D1.
Only D0-and-earlier data enter the model; D1 open controls activation only.
"""

from __future__ import annotations

import hashlib
import json
import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_2y import SOURCE
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import historical_status, balanced_weights


OUT = Path('outputs/stock_automl/limit_rebound_d0_open_r5_20261014')
FEATURES = (
    'board_streak_days', 'streak_return', 'd0_return', 'd0_open_gap',
    'd0_range', 'd0_lower_shadow', 'd0_close_position',
    'first_open_gap', 'first_range', 'first_one_price',
    'd0_volume_ratio_pre5', 'd0_amount_ratio_pre5', 'd0_turnover_pct',
    'd0_amount_100m', 'd0_volume_ratio_first', 'd0_turnover_ratio_first',
    'first_turnover_pct', 'pre5_return', 'pre5_mean_turnover_pct',
    'market_limit_count', 'market_prev_limit_count', 'market_up_fraction',
    'market_median_return',
)


def cap_price(preclose: float, rate: float) -> float:
    return float((Decimal(str(preclose)) * (1 + Decimal(str(rate)))).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP))


def normal_limit_rate(symbol: str) -> float:
    if symbol.startswith(('300', '301', '688', '689')):
        return .20
    if symbol.startswith('92'):
        return .30
    if symbol.startswith(('0', '6')):
        return .10
    return np.nan


def load_daily() -> pd.DataFrame:
    paths = sorted((SOURCE / 'price_months').glob('price_*.csv.gz'))
    raw = pd.concat((pd.read_csv(path, dtype={'stk_code': str, 'trade_date': str})
                     for path in paths), ignore_index=True)
    daily = prepare_daily(raw)
    daily['date_i'] = daily.trade_date.map(
        {day: i for i, day in enumerate(sorted(daily.trade_date.unique()))}).astype(int)
    return daily


def build_panel(daily: pd.DataFrame, out: Path, require_day_volume: bool = False,
                score_all_d0: bool = False, d0_start: str = '2024-10-01') -> tuple[pd.DataFrame, dict]:
    daily = daily.copy()
    g = daily.groupby('symbol', sort=False)
    prior_date_i = g.date_i.shift(1)
    prior_up = g.up.shift(1).eq(True)
    daily['run_start'] = daily.up & ~(prior_up & daily.date_i.sub(prior_date_i).eq(1))
    daily['run_id'] = daily.run_start.groupby(daily.symbol).cumsum()
    daily['ret'] = daily.close.div(daily.preclose).sub(1)
    daily['pre5_mean_volume'] = g.volume.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    daily['pre5_mean_amount'] = g.amount.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    daily['pre5_mean_turnover_pct'] = g.turn_ratio.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    daily['pre5_return'] = g.ret.transform(lambda s: (s.add(1).shift(1).rolling(5, min_periods=5).apply(np.prod, raw=True) - 1))
    daily['prior5_contiguous'] = daily.date_i.sub(g.date_i.shift(5)).eq(5)
    for col in ('pre5_mean_volume', 'pre5_mean_amount', 'pre5_mean_turnover_pct', 'pre5_return'):
        daily.loc[~daily.prior5_contiguous, col] = np.nan

    market = daily.groupby('trade_date').agg(market_limit_count=('up', 'sum'),
        market_up_fraction=('ret', lambda s: float(s.gt(0).mean())),
        market_median_return=('ret', 'median')).reset_index()
    market['market_prev_limit_count'] = market.market_limit_count.shift(1)
    board = daily[daily.up].copy()
    board['board_streak_days'] = board.groupby(['symbol', 'run_id']).cumcount() + 1
    first = board.groupby(['symbol', 'run_id'], sort=False)
    for col in ('trade_date', 'preclose', 'open', 'high', 'low', 'close', 'volume', 'turn_ratio'):
        board[f'first_{col}'] = first[col].transform('first')
    board['first_limit_date'] = board.first_trade_date
    board['streak_return'] = board.close.div(board.first_preclose).sub(1)
    board['d0_return'] = board.ret
    board['d0_open_gap'] = board.open.div(board.preclose).sub(1)
    board['d0_range'] = board.high.sub(board.low).div(board.preclose)
    board['d0_lower_shadow'] = np.minimum(board.open, board.close).sub(board.low).div(board.preclose)
    board['d0_close_position'] = board.close.sub(board.low).div(board.high.sub(board.low).replace(0, np.nan))
    board['first_open_gap'] = board.first_open.div(board.first_preclose).sub(1)
    board['first_range'] = board.first_high.sub(board.first_low).div(board.first_preclose)
    board['first_one_price'] = ((board.first_open == board.first_high) &
                                (board.first_high == board.first_low) &
                                (board.first_low == board.first_close)).astype(int)
    board['d0_volume_ratio_pre5'] = board.volume.div(board.pre5_mean_volume)
    board['d0_amount_ratio_pre5'] = board.amount.div(board.pre5_mean_amount)
    board['d0_turnover_pct'] = board.turn_ratio
    board['d0_amount_100m'] = board.amount.div(1e8)
    board['d0_volume_ratio_first'] = board.volume.div(board.first_volume.replace(0, np.nan))
    board['d0_turnover_ratio_first'] = board.turn_ratio.div(board.first_turn_ratio.replace(0, np.nan))
    board['first_turnover_pct'] = board.first_turn_ratio
    board = board.merge(market, on='trade_date', validate='many_to_one')
    board = board.rename(columns={'trade_date': 'd0_date'})
    board = board[board.d0_date.ge(d0_start)].copy()
    board['d1_i'] = board.date_i + 1
    next_bar = daily[['symbol', 'date_i', 'trade_date', 'preclose', 'open', 'close', 'volume', 'up']].copy()
    next_bar.columns = ['symbol', 'd1_i', 'd1_date', 'd1_preclose', 'd1_open', 'd1_close', 'd1_volume', 'd1_up']
    panel = board.merge(next_bar, on=['symbol', 'd1_i'], how='left', validate='one_to_one')

    # D0 status is known when scoring; D1 status is checked before open activation.
    status = historical_status(pd.DataFrame({'symbol': pd.concat([panel.symbol, panel.symbol]),
        'asof_date': pd.concat([panel.d0_date, panel.d1_date])}).dropna().drop_duplicates(), out)
    d0_st = status.rename(columns={'asof_date': 'd0_date', 'is_st': 'd0_st'})[['symbol', 'd0_date', 'd0_st']]
    d1_st = status.rename(columns={'asof_date': 'd1_date', 'is_st': 'd1_st'})[['symbol', 'd1_date', 'd1_st']]
    panel = panel.merge(d0_st, on=['symbol', 'd0_date'], how='left', validate='many_to_one')
    panel = panel.merge(d1_st, on=['symbol', 'd1_date'], how='left', validate='many_to_one')
    panel['rate'] = panel.symbol.map(normal_limit_rate)
    legacy_growth = panel.symbol.str.startswith(('300', '301'))
    panel.loc[legacy_growth & panel.d0_date.lt('2020-08-24'), 'rate'] = .10
    supported = panel.rate.notna() & panel.d0_st.eq(0)
    if not score_all_d0:
        supported &= panel.d1_st.eq(0) & panel.d1_date.notna()
    panel = panel[supported].copy()
    panel['d0_cap'] = [cap_price(a, b) for a, b in zip(panel.preclose, panel.rate)]
    panel['d1_rate'] = panel.rate
    panel.loc[panel.symbol.str.startswith(('300', '301')) & panel.d1_date.ge('2020-08-24'), 'd1_rate'] = .20
    panel['d1_cap'] = [cap_price(a, b) if pd.notna(a) else np.nan
                       for a, b in zip(panel.d1_preclose, panel.d1_rate)]
    standard_board = panel.close.sub(panel.d0_cap).abs().le(.021)
    panel = panel[standard_board].copy()
    panel['activated'] = (panel.d1_st.eq(0) & panel.d1_open.gt(0) &
                          panel.d1_open.lt(panel.d1_cap - .005))
    open_but_no_day_volume = int((panel.activated & panel.d1_volume.le(0)).sum())
    if require_day_volume:
        panel['activated'] &= panel.d1_volume.gt(0)

    # Future labels are sourced solely from subsequent closes, in market-day order.
    quote = daily.set_index(['symbol', 'date_i'])['up']
    for horizon in (3, 4, 5):
        future = [quote.reindex(pd.MultiIndex.from_arrays(
            [panel.symbol.to_numpy(), panel.date_i.to_numpy() + gap])).to_numpy()
            for gap in range(1, horizon + 1)]
        all_observed = np.logical_and.reduce([pd.notna(x) for x in future])
        panel[f'observed_{horizon}'] = all_observed
        panel[f'label_{horizon}'] = np.logical_or.reduce(
            [pd.Series(x).eq(True).to_numpy(dtype=bool) for x in future]).astype(int)
    quality = {'d0_board_stock_days': int(board.shape[0]),
               'nonst_supported_standard_d0_with_d1': int(panel.shape[0]),
               'activated_rows': int(panel.activated.sum()),
               'open_below_cap_but_zero_day_volume': open_but_no_day_volume,
               'd1_close_reboard_activated': int((panel.activated & panel.d1_up).sum()),
               'open_at_or_above_cap_or_no_open': int((~panel.activated).sum())}
    return panel, quality


def split(panel: pd.DataFrame, calendar: list[str], horizon: int):
    date_i = {day: i for i, day in enumerate(calendar)}
    valid_start, test_start = date_i['2026-04-01'], date_i['2026-07-01']
    rows = panel[panel.activated & panel[f'observed_{horizon}']].copy()
    train = rows[(rows.date_i + horizon < valid_start)].copy()
    valid = rows[(rows.date_i >= valid_start) & (rows.date_i + horizon < test_start)].copy()
    test = rows[rows.date_i >= test_start].copy()
    # Long limit streaks spanning split boundaries must not occur on both sides.
    valid_events = set(zip(valid.symbol, valid.first_limit_date))
    test_events = set(zip(test.symbol, test.first_limit_date))
    train = train[[key not in valid_events for key in zip(train.symbol, train.first_limit_date)]]
    refit = rows[(rows.date_i + horizon < test_start)].copy()
    refit = refit[[key not in test_events for key in zip(refit.symbol, refit.first_limit_date)]]
    if set(zip(train.symbol, train.first_limit_date)) & valid_events or set(zip(refit.symbol, refit.first_limit_date)) & test_events:
        raise AssertionError('Limit run crosses split')
    return train, valid, refit, test


def evaluate(frame: pd.DataFrame, score: np.ndarray, target: str) -> dict:
    y = frame[target].to_numpy(dtype=int)
    order = frame[['symbol', 'd0_date', 'd1_date', 'first_limit_date',
                   'board_streak_days', 'd1_up', target]].copy()
    order['score'] = score
    order = order.sort_values(['d1_date', 'score', 'symbol', 'd0_date'], ascending=[True, False, True, True])
    tops = {}
    for k in (1, 3, 5):
        picks = order.groupby('d1_date').head(k)
        tops[str(k)] = {'days': int(picks.d1_date.nunique()), 'picks': len(picks),
                        'hits': int(picks[target].sum()),
                        'd1_close_reboard': int(picks.d1_up.sum()),
                        'hit_rate': float(picks[target].mean())}
    longest = order.sort_values(['d1_date', 'board_streak_days', 'symbol'],
                                ascending=[True, False, True]).groupby('d1_date').head(1)
    return {'n': len(frame), 'positive': int(y.sum()), 'base_rate': float(y.mean()),
            'ap': float(average_precision_score(y, score)),
            'auc': float(roc_auc_score(y, score)), 'top': tops,
            'daily_random_top1_expected_hits': float(frame.groupby('d1_date')[target].mean().sum()),
            'longest_streak_top1_hits': int(longest[target].sum())}


def run(out: Path = OUT, require_day_volume: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    calendar = sorted(daily.trade_date.unique())
    panel, quality = build_panel(daily, out, require_day_volume=require_day_volume)
    if not panel.d0_date.lt(panel.d1_date).all():
        raise AssertionError('D1 does not follow D0')
    if any(name.startswith('d1_') for name in FEATURES):
        raise AssertionError('Future D1 field included in D0 features')
    result = {'experiment': 'R5_D0_score_D1_open_activation', 'sklearn': sklearn.__version__,
              'target': 'D1 through D3/D4/D5 inclusive sealed close after D1 open activation',
              'source': str(SOURCE), 'data_quality': quality, 'features': FEATURES,
              'require_day_volume_audit': require_day_volume,
              'preprocessing': 'exclude historical ST at D0 and D1; balanced training sample weights',
              'horizons': {}, 'generated_at_utc': datetime.now(timezone.utc).isoformat()}
    for horizon in (3, 4, 5):
        train, valid, refit, test = split(panel, calendar, horizon)
        target = f'label_{horizon}'
        model = make_model('shallow_boost')
        model.fit(train[list(FEATURES)], train[target], sample_weight=balanced_weights(train[target]))
        train_score = model.predict_proba(train[list(FEATURES)])[:, 1]
        valid_score = model.predict_proba(valid[list(FEATURES)])[:, 1]
        final = make_model('shallow_boost')
        final.fit(refit[list(FEATURES)], refit[target], sample_weight=balanced_weights(refit[target]))
        test_score = final.predict_proba(test[list(FEATURES)])[:, 1]
        result['horizons'][str(horizon)] = {
            'train': evaluate(train, train_score, target),
            'valid': evaluate(valid, valid_score, target),
            'test': evaluate(test, test_score, target),
            'dates': {'train': [train.d0_date.min(), train.d0_date.max()],
                      'valid': [valid.d0_date.min(), valid.d0_date.max()],
                      'test': [test.d0_date.min(), test.d0_date.max()]}}
        for part, frame, scores in [('valid', valid, valid_score), ('test', test, test_score)]:
            preds = frame[['symbol', 'd0_date', 'd1_date', 'first_limit_date',
                           'board_streak_days', target, 'd1_open', 'd1_close', 'd1_up']].copy()
            preds['score'] = scores
            preds.to_csv(out / f'h{horizon}_{part}_scores.csv.gz', index=False, compression='gzip')
        print('horizon', horizon, 'valid/test top1',
              result['horizons'][str(horizon)]['valid']['top']['1'],
              result['horizons'][str(horizon)]['test']['top']['1'], flush=True)
    result['source_manifest_sha256'] = hashlib.sha256((SOURCE / 'manifest.json').read_bytes()).hexdigest()
    result['historical_st_sha256'] = hashlib.sha256((out / 'historical_st.csv.gz').read_bytes()).hexdigest()
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--volume-audit', action='store_true')
    args = parser.parse_args()
    run(OUT.parent / 'limit_rebound_d0_open_r5_volume_audit_20261014'
        if args.volume_audit else OUT, require_day_volume=args.volume_audit)
