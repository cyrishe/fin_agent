"""Replay a fixed D0 Top5 opening rule on R5 train and validation dates."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_limit_rebound_d0_open_r5 import FEATURES, build_panel, load_daily, split
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.research_limit_rebound_d1_minute_r6 import R5, scan_minutes


OUT = Path('outputs/stock_automl/limit_rebound_train_valid_r7_20261014')


def pick_daily(frame: pd.DataFrame, daily: pd.DataFrame, part: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    f = frame.sort_values(['d0_date', 'score', 'symbol'], ascending=[True, False, True]).copy()
    f['d0_rank'] = f.groupby('d0_date').cumcount() + 1
    eligible = f[f.activated].copy()
    eligible['open_rank'] = eligible.groupby('d1_date').cumcount() + 1
    top1 = eligible[eligible.open_rank.eq(1)].copy()
    top5 = eligible[eligible.d0_rank.le(5)].sort_values(['d1_date', 'd0_rank']).groupby('d1_date').head(1).copy()
    if not top1.d1_date.equals(top5.d1_date):
        assert set(top1.d1_date) == set(top5.d1_date)
    raw_count = daily[daily.trade_date.isin(f.d0_date.unique()) & daily.up].groupby('trade_date').size()
    counts = f.groupby('d0_date').agg(d0_supported=('symbol', 'size'),
                                      d1_open_eligible=('activated', 'sum')).reset_index()
    counts['top5_open_eligible'] = counts.d0_date.map(
        f[f.d0_rank.le(5)].groupby('d0_date').activated.sum()).fillna(0).astype(int)
    counts['d0_all_sealed'] = counts.d0_date.map(raw_count).astype(int)
    counts['d1_date'] = counts.d0_date.map(f.groupby('d0_date').d1_date.first())
    columns = ['d1_date', 'symbol', 'd0_rank', 'score', 'd1_up', 'label_3']
    first = top1[columns].rename(columns={c: f'open1_{c}' for c in columns if c not in ('d1_date', 'symbol')})
    first = first.rename(columns={'symbol': 'open1_symbol'})
    five = top5[columns].rename(columns={c: f'top5_{c}' for c in columns if c not in ('d1_date', 'symbol')})
    five = five.rename(columns={'symbol': 'top5_symbol'})
    days = counts.merge(first, on='d1_date', how='left', validate='one_to_one').merge(
        five, on='d1_date', how='left', validate='one_to_one')
    days.insert(0, 'part', part)
    days['open1_later_only'] = days.open1_label_3.sub(days.open1_d1_up.astype(int))
    days['top5_later_only'] = days.top5_label_3.sub(days.top5_d1_up.astype(int))
    assert days[['open1_later_only', 'top5_later_only']].ge(0).all().all()
    return days, f


def summarize(days: pd.DataFrame, f: pd.DataFrame, minutes: pd.DataFrame) -> dict:
    top = f[f.d0_rank.le(5)].merge(minutes, on=['d1_date', 'symbol'], how='left', validate='one_to_one')
    top['minute_valid'] = top.minute_open.notna()
    open_top = top[top.activated & top.minute_valid & top.open_below_cap.eq(True)]
    open_pick = open_top.sort_values(['d1_date', 'd0_rank']).groupby('d1_date').head(1)
    at10 = top[top.minute_valid & top.d1_st.eq(0) &
               top.first_below_cap_by_1000_min.notna() & top.below_cap_at_1000.eq(True)]
    ten_pick = at10.sort_values(['d1_date', 'd0_rank']).groupby('d1_date').head(1)
    ten_proxy = ten_pick[ten_pick.can_buy_1001_proxy.eq(True)]
    reboard = open_pick[open_pick.d1_up.eq(True)]
    rank = days.open1_d0_rank.dropna()
    return {
        'days': len(days), 'd0_all_sealed_stock_days': int(days.d0_all_sealed.sum()),
        'd0_supported_stock_days': int(days.d0_supported.sum()),
        'd1_open_eligible_stock_days': int(days.d1_open_eligible.sum()),
        'open_top1': {'days': int(days.open1_symbol.notna().sum()),
            'd0_rank_median': float(rank.median()), 'd0_rank_p25': float(rank.quantile(.25)),
            'd0_rank_p75': float(rank.quantile(.75)), 'within_d0_top5_days': int(rank.le(5).sum()),
            'd1_reboard': int(days.open1_d1_up.sum()), 'd1_to_d3_any': int(days.open1_label_3.sum()),
            'd2_or_d3_only': int(days.open1_later_only.sum())},
        'd0_top5_open': {'days_with_minute_verified_selection': len(open_pick),
            'd1_reboard': int(open_pick.d1_up.sum()), 'd1_to_d3_any': int(open_pick.label_3.sum()),
            'd2_or_d3_only': int(open_pick.label_3.sum() - open_pick.d1_up.sum()),
            'first_cap_close_median_min': float(reboard.first_close_cap_min.median()) if len(reboard) else None,
            'first_cap_close_by_3min': int(reboard.first_close_cap_min.le(3).sum()),
            'first_cap_close_by_10min': int(reboard.first_close_cap_min.le(10).sum())},
        'd0_top5_wait_1000': {'days_selected': len(ten_pick), 'eligible_stock_days': len(at10),
            'days_1001_fill_opportunity_proxy': len(ten_proxy),
            'proxy_d1_reboard': int(ten_proxy.d1_up.sum()),
            'proxy_d1_to_d3_any': int(ten_proxy.label_3.sum())},
        'minute_coverage': {'d0_top5_complete': int(top.minute_valid.sum()),
                            'd0_top5_requested': int(top.shape[0])},
    }


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    panel, _ = build_panel(daily, R5, score_all_d0=True)
    calendar = sorted(daily.trade_date.unique())
    train, valid, _, _ = split(panel, calendar, 3)
    model = make_model('shallow_boost')
    model.fit(train[list(FEATURES)], train.label_3, sample_weight=balanced_weights(train.label_3))
    valid_check = valid[['symbol', 'd0_date']].copy()
    valid_check['score'] = model.predict_proba(valid[list(FEATURES)])[:, 1]
    saved = pd.read_csv(R5 / 'h3_valid_scores.csv.gz', dtype={'symbol': str, 'd0_date': str})
    chk = valid_check.merge(saved[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'],
                            suffixes=('', '_saved'), validate='one_to_one')
    assert len(chk) == len(valid) == len(saved)
    assert np.allclose(chk.score, chk.score_saved, atol=1e-12, rtol=0)
    print('R5 validation scores reproduced', len(chk), flush=True)

    batches = {}
    train_events = set(zip(train.symbol, train.first_limit_date))
    valid_events = set(zip(valid.symbol, valid.first_limit_date))
    for name, lo, hi in [('train', '2026-01-05', train.d0_date.max()),
                         ('valid', valid.d0_date.min(), valid.d0_date.max())]:
        batch = panel[panel.d0_date.between(lo, hi) & panel.observed_3].copy()
        # Use exactly the event embargo of the R5 model's train/validation split.
        events = valid_events if name == 'train' else set()
        batch = batch[[key not in events for key in zip(batch.symbol, batch.first_limit_date)]]
        batch['score'] = model.predict_proba(batch[list(FEATURES)])[:, 1]
        batches[name] = pick_daily(batch, daily, name)
        print(name, 'days', len(batches[name][0]), 'D0 stock-days', len(batch), flush=True)

    demand = pd.concat([f[f.d0_rank.le(5)] for _, f in batches.values()], ignore_index=True)
    demand = demand[['d1_date', 'symbol', 'd1_cap', 'd1_close', 'd1_open']].drop_duplicates(['d1_date', 'symbol'])
    cache = OUT / 'minute_facts.csv.gz'
    cov_file = OUT / 'minute_coverage.json'
    if cache.exists() and cov_file.exists():
        minute = pd.read_csv(cache, dtype={'symbol': str, 'd1_date': str})
        coverage = json.loads(cov_file.read_text())
    else:
        minute, coverage = scan_minutes(demand)
        minute.to_csv(cache, index=False, compression='gzip')
        cov_file.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n')
    check = minute.merge(demand, on=['d1_date', 'symbol'], validate='one_to_one')
    assert np.isclose(check.minute_open, check.d1_open, atol=.005, rtol=0).all()
    assert check.open_below_cap.eq(check.d1_open.lt(check.d1_cap - .005)).all()
    summary = {'experiment': 'R7_train_valid_fixed_D0_top5_open_and_1000',
               'model': 'R5 h3 trained only on R5 train rows',
               'validation_score_reproduction_rows': len(chk),
               'minute_coverage': coverage, 'parts': {}}
    details = []
    for name, (days, f) in batches.items():
        summary['parts'][name] = summarize(days, f, minute)
        chosen_minute = minute.rename(columns={'symbol': 'top5_symbol'})
        days = days.merge(chosen_minute, on=['d1_date', 'top5_symbol'], how='left', validate='one_to_one')
        details.append(days)
    pd.concat(details, ignore_index=True).to_csv(OUT / 'daily_audit.csv', index=False)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
