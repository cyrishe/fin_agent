"""Minute audit of R5 D0 scores: open-below-cap and D0 Top5 at 10:00."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_limit_rebound_d0_open_r5 import (
    FEATURES, build_panel, load_daily, split,
)
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.mk_minute_archive import day_file


R5 = Path('outputs/stock_automl/limit_rebound_d0_open_r5_20261014')
OUT = Path('outputs/stock_automl/limit_rebound_d1_minute_r6_20261014')
MINUTES = Path('outputs/stock_automl/mk_1m_compact')


def minute_facts(bars: pd.DataFrame, cap: float, daily_close: float) -> dict | None:
    if len(bars) != 240:
        return None
    bars = bars.sort_values('time')
    clock = pd.to_datetime(bars.time).dt.strftime('%H:%M')
    values = bars[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
    if (clock.nunique() != 240 or clock.iloc[0] != '09:31' or clock.iloc[-1] != '15:00'
            or values.isna().any().any() or values[['open', 'high', 'low', 'close']].le(0).any().any()
            or values.volume.lt(0).any() or not np.isclose(values.close.iloc[-1], daily_close, atol=.005, rtol=0)):
        return None
    below_close = values.close.lt(cap - .005).to_numpy()
    below_low = values.low.lt(cap - .005).to_numpy()
    at_close = values.close.ge(cap - .005).to_numpy()
    first_touch = np.flatnonzero(values.high.ge(cap - .005).to_numpy())
    first_close = np.flatnonzero(at_close)
    # 10:00 is the 30th bar ending at 10:00; act no earlier than 10:01.
    at_1000 = int(np.flatnonzero(clock.eq('10:00'))[0])
    assert at_1000 == 29
    first_below = np.flatnonzero(below_low[:at_1000 + 1])
    last_below = np.flatnonzero(below_low)
    final_seal = (int(last_below[-1] + np.flatnonzero(at_close[last_below[-1]:])[0] + 1)
                  if len(last_below) and at_close[last_below[-1]:].any() else
                  1 if not len(last_below) and at_close.any() else None)
    open_below = bool(values.open.iloc[0] < cap - .005)
    tradable_1001 = bool(values.open.iloc[at_1000 + 1] < cap - .005 and
                         values.volume.iloc[at_1000 + 1] > 0)
    return {
        'minute_open': float(values.open.iloc[0]),
        'minute_total_volume': float(values.volume.sum()),
        'open_below_cap': open_below,
        'first_touch_min': int(first_touch[0] + 1) if len(first_touch) else None,
        'first_close_cap_min': int(first_close[0] + 1) if len(first_close) else None,
        'final_close_cap_min': final_seal,
        'first_below_cap_by_1000_min': int(first_below[0] + 1) if len(first_below) else None,
        'below_cap_at_1000': bool(below_close[at_1000]),
        'can_buy_1001_proxy': tradable_1001,
        'close_cap_after_1000': bool(at_close[at_1000 + 1:].any()),
        'first_close_cap_after_1000_min': int(np.flatnonzero(at_close[at_1000 + 1:])[0] + at_1000 + 2)
            if at_close[at_1000 + 1:].any() else None,
        'minutes_below_cap_before_first_close_cap': int(below_close[:first_close[0]].sum())
            if len(first_close) else None,
    }


def scan_minutes(demand: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    needed = demand.groupby('d1_date').symbol.apply(set).to_dict()
    found = []
    missing_files = 0
    for day, symbols in sorted(needed.items()):
        try:
            path = day_file(MINUTES, day)
        except FileNotFoundError:
            missing_files += len(symbols)
            continue
        chunks = []
        for chunk in pd.read_csv(path, dtype={'symbol': str}, chunksize=150_000):
            take = chunk[chunk.symbol.str[:6].isin(symbols)]
            if not take.empty:
                chunks.append(take)
        if not chunks:
            continue
        for symbol, bars in pd.concat(chunks, ignore_index=True).groupby('symbol'):
            symbol = str(symbol)[:6]
            row = demand[(demand.d1_date == day) & demand.symbol.eq(symbol)].iloc[0]
            facts = minute_facts(bars, float(row.d1_cap), float(row.d1_close))
            if facts is not None:
                found.append({'d1_date': day, 'symbol': symbol, **facts})
    return pd.DataFrame(found), {'requested_stock_days': len(demand),
                                 'missing_local_file_stock_days': missing_files,
                                 'complete_matched_minute_stock_days': len(found)}


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    panel, _ = build_panel(daily, R5, score_all_d0=True)
    calendar = sorted(daily.trade_date.unique())
    _, _, refit, _ = split(panel, calendar, 3)
    model = make_model('shallow_boost')
    model.fit(refit[list(FEATURES)], refit.label_3,
              sample_weight=balanced_weights(refit.label_3))
    july = panel[panel.d0_date.between('2026-07-01', '2026-07-30') &
                 panel.d1_date.between('2026-07-02', '2026-07-31')].copy()
    july['score'] = model.predict_proba(july[list(FEATURES)])[:, 1]
    # R5 must reproduce on its already-activated test rows before studying D0 ranks.
    saved = pd.read_csv(R5 / 'h3_test_scores.csv.gz', dtype={'symbol': str, 'd0_date': str})
    check = july.merge(saved[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'], suffixes=('', '_saved'))
    if not np.allclose(check.score, check.score_saved, atol=1e-12, rtol=0):
        raise AssertionError('R5 D0 score reproduction failed')
    july = july.sort_values(['d0_date', 'score', 'symbol'], ascending=[True, False, True])
    july['d0_rank'] = july.groupby('d0_date').cumcount() + 1
    top5 = july[july.d0_rank.le(5)].copy()
    # Compare also the D1-open eligible universe from the same scored D0 days.
    open_pool = july[july.activated].copy().sort_values(['d1_date', 'score', 'symbol'],
                                                         ascending=[True, False, True])
    open_pool['open_rank'] = open_pool.groupby('d1_date').cumcount() + 1
    focus = pd.concat([top5, open_pool[open_pool.open_rank.le(5)]], ignore_index=True)
    focus = focus.drop_duplicates(['d1_date', 'symbol'])
    focus = focus.drop(columns='open_rank').merge(
        open_pool[['d1_date', 'symbol', 'open_rank']], on=['d1_date', 'symbol'],
        how='left', validate='one_to_one')
    demand = focus[['d1_date', 'symbol', 'd1_cap', 'd1_close', 'd1_open']].copy()
    minute_cache = OUT / 'minute_facts_v2.csv.gz'
    coverage_cache = OUT / 'minute_coverage_v2.json'
    if minute_cache.exists() and coverage_cache.exists():
        minutes = pd.read_csv(minute_cache, dtype={'symbol': str, 'd1_date': str})
        coverage = json.loads(coverage_cache.read_text())
    else:
        minutes, coverage = scan_minutes(demand)
        minutes.to_csv(minute_cache, index=False, compression='gzip')
        coverage_cache.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n')
    open_check = minutes.merge(demand[['d1_date', 'symbol', 'd1_open']],
                               on=['d1_date', 'symbol'], validate='one_to_one')
    if not np.isclose(open_check.minute_open, open_check.d1_open, atol=.005, rtol=0).all():
        raise AssertionError('Minute opening price does not match daily opening price')
    focus = focus.merge(minutes, on=['d1_date', 'symbol'], how='left', validate='many_to_one')
    focus['minute_valid'] = focus.minute_open.notna()
    first = focus[focus.open_rank.eq(1) & focus.minute_valid].copy()
    first_reboard = first[first.d1_up.eq(True) & first.open_below_cap.eq(True)].copy()
    d0_top5 = focus[focus.d0_rank.le(5) & focus.minute_valid].copy()
    open5 = d0_top5[d0_top5.activated.eq(True) & d0_top5.open_below_cap.eq(True)]
    chosen_open5 = open5.sort_values(['d1_date', 'd0_rank']).groupby('d1_date').head(1)
    # Select with information available at 10:00; assess 10:01 fill separately.
    at10 = d0_top5[d0_top5.d1_st.eq(0) & d0_top5.first_below_cap_by_1000_min.notna() &
                   d0_top5.below_cap_at_1000.eq(True)]
    chosen10 = at10.sort_values(['d1_date', 'd0_rank']).groupby('d1_date').head(1)
    filled10 = chosen10[chosen10.can_buy_1001_proxy.eq(True)]
    paired_open = chosen_open5[chosen_open5.d1_date.isin(filled10.d1_date)]
    summary = {'experiment': 'R6_D0_top5_D1_minute_July2026', 'source_model': 'R5 h3',
        'coverage': coverage,
        'd0_test_days': int(july.d0_date.nunique()),
        'd0_top5': {'signals': len(top5), 'activated_open': int(top5.activated.sum()),
                    'minute_valid': int(d0_top5.minute_valid.sum()),
                    'd1_reboard': int(top5.d1_up.sum())},
        'd0_top5_buy_open': {'days_selected': int(chosen_open5.d1_date.nunique()),
            'selected_d1_close_reboard': int(chosen_open5.d1_up.sum()),
            'selected_h3_label_hits': int(chosen_open5.label_3.sum()),
            'selected_first_cap_close_median_min': float(chosen_open5.loc[chosen_open5.d1_up, 'first_close_cap_min'].median()),
            'selected_final_cap_close_median_min': float(chosen_open5.loc[chosen_open5.d1_up, 'final_close_cap_min'].median())},
        'open_rank1': {'minute_valid': len(first), 'd1_open_below_and_reboard': len(first_reboard),
                       'first_cap_close_delay_minutes_median': float(first_reboard.first_close_cap_min.median())
                           if len(first_reboard) else None,
                       'first_cap_close_delay_minutes_p25': float(first_reboard.first_close_cap_min.quantile(.25))
                           if len(first_reboard) else None,
                       'first_cap_close_delay_minutes_p75': float(first_reboard.first_close_cap_min.quantile(.75))
                           if len(first_reboard) else None,
                       'final_cap_close_delay_minutes_median': float(first_reboard.final_close_cap_min.median())
                           if len(first_reboard) else None,
                       'final_cap_close_delay_minutes_p75': float(first_reboard.final_close_cap_min.quantile(.75))
                           if len(first_reboard) else None,
                       'reboard_first_cap_close_after_1000': int(first_reboard.first_close_cap_min.gt(30).sum()),
                       'reboard_final_cap_close_after_1000': int(first_reboard.final_close_cap_min.gt(30).sum())},
        'd0_top5_wait_1000': {'days_with_valid_top5_minutes': int(d0_top5.d1_date.nunique()),
            'candidate_stock_days_1000': len(at10), 'days_selected_1000': int(chosen10.d1_date.nunique()),
            'selected_buy_proxy_1001': len(filled10),
            'filled_d1_close_reboard': int(filled10.d1_up.sum()),
            'filled_after_1000_close_cap': int(filled10.close_cap_after_1000.sum()),
            'filled_h3_label_hits': int(filled10.label_3.sum()),
            'selected_rank_median': float(chosen10.d0_rank.median()) if len(chosen10) else None,
            'same_20_days_open_d1_reboard': int(paired_open.d1_up.sum()),
            'same_20_days_open_h3_hits': int(paired_open.label_3.sum())}}
    columns = ['d1_date', 'symbol', 'd0_date', 'd0_rank', 'open_rank', 'score', 'activated',
               'd1_up', 'label_3', 'minute_valid', 'open_below_cap', 'first_touch_min',
               'first_close_cap_min', 'final_close_cap_min', 'first_below_cap_by_1000_min', 'below_cap_at_1000',
               'can_buy_1001_proxy', 'close_cap_after_1000', 'first_close_cap_after_1000_min']
    focus[columns].to_csv(OUT / 'audited_signal_stock_days.csv.gz', index=False, compression='gzip')
    chosen10[columns].to_csv(OUT / 'top5_wait1000_selected.csv', index=False)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
