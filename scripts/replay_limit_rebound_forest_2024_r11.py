"""Replay the restored R8/R9 D0 Top5 -> D1 open rule on 2024 May-September."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_limit_rebound_version_robust_r8 import feature_matrix
from scripts.export_limit_rebound_d0_top5_r9_detail import records


RESTORED = Path('outputs/stock_automl/limit_rebound_forest_restored_r11_20261014')
OUT = Path('outputs/stock_automl/limit_rebound_2024_may_sep_r11_20261014')
START, END = '2024-05-01', '2024-09-30'
SOURCE_START, SOURCE_END = '2024-04-01', '2024-10-18'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def price_history() -> tuple[pd.DataFrame, list[dict]]:
    root = OUT / 'price_months'
    root.mkdir(parents=True, exist_ok=True)
    evidence = []
    paths = []
    load_dotenv('.env')
    with db_connection() as conn:
        for month in pd.period_range(SOURCE_START[:7], SOURCE_END[:7], freq='M'):
            lo = max(SOURCE_START, str(month.start_time.date()))
            hi = min(SOURCE_END, str(month.end_time.date()))
            path = root / f'price_{month}.csv.gz'
            if not path.exists():
                frame = query(conn,
                    'SELECT trade_date,stk_code,preclose,open,high,low,close,'
                    'volume,amount,turn_ratio,is_limit_price,update_time '
                    'FROM kcrp_stock_price WHERE trade_date BETWEEN %s AND %s', (lo, hi))
                if frame.empty:
                    raise ValueError(f'No prices in {month}')
                frame.to_csv(path, index=False, compression='gzip')
                print(f'prices {month}: {len(frame)}', flush=True)
            paths.append(path)
            evidence.append({'month': str(month), 'sha256': sha256(path), 'rows':
                             int(len(pd.read_csv(path, usecols=['trade_date'])))})
        conn.rollback()
    raw = pd.concat([pd.read_csv(p, dtype={'trade_date': str, 'stk_code': str})
                     for p in paths], ignore_index=True)
    daily = prepare_daily(raw)
    daily['date_i'] = daily.trade_date.map(
        {day: i for i, day in enumerate(sorted(daily.trade_date.unique()))}).astype(int)
    return daily, evidence


def add_market_cap(top: pd.DataFrame) -> pd.DataFrame:
    cache = OUT / 'top5_market_cap_source.csv.gz'
    if cache.exists():
        raw = pd.read_csv(cache, dtype={'trade_date': str, 'stk_code': str})
    else:
        with db_connection() as conn:
            raw = query(conn, 'SELECT trade_date,stk_code,total_mv FROM kcrp_stock_pricevaluate '
                        'WHERE trade_date BETWEEN %s AND %s', (START, END))
            conn.rollback()
        raw.to_csv(cache, index=False, compression='gzip')
    raw['d0_date'] = pd.to_datetime(raw.trade_date).dt.strftime('%Y-%m-%d')
    raw['symbol'] = raw.stk_code.astype(str).str[:6]
    cap = raw[['symbol', 'd0_date', 'total_mv']].copy()
    if cap.duplicated(['symbol', 'd0_date']).any():
        raise ValueError('Duplicate total market capitalization keys')
    top = top.merge(cap, on=['symbol', 'd0_date'], how='left', validate='one_to_one')
    top['market_cap_100m'] = pd.to_numeric(top.total_mv, errors='coerce') / 1e8
    top['mv_estimated'] = False
    return top


def run() -> None:
    gate = json.loads((RESTORED / 'reproduction_gate.json').read_text())
    if not gate.get('gate_passed'):
        raise RuntimeError('R8 reproduction gate did not pass')
    OUT.mkdir(parents=True, exist_ok=True)
    daily, price_sources = price_history()
    panel, quality = build_panel(daily, OUT, score_all_d0=True, d0_start=START)
    panel = panel[panel.d0_date.between(START, END) & panel.observed_3].copy()
    model_file = RESTORED / 'forest_refit_sk171.joblib'
    if sha256(model_file) != gate['test']['model_sha256']:
        raise RuntimeError('Saved model hash differs from verified model')
    model = joblib.load(model_file)
    panel['score'] = model.predict_proba(feature_matrix(panel, 'forest'))[:, 1]
    panel = panel.sort_values(['d0_date', 'score', 'symbol'],
                              ascending=[True, False, True])
    panel['d0_rank'] = panel.groupby('d0_date').cumcount() + 1
    top = panel[panel.d0_rank.le(5)].copy()
    top['day_seq'] = pd.factorize(top.d0_date, sort=True)[0] + 1
    top['part'] = 'historical'
    top['selected'] = False
    active = top[top.activated].sort_values(['d1_date', 'd0_rank'])
    winner = active.groupby('d1_date').head(1).index
    top.loc[winner, 'selected'] = True
    quotes = daily.set_index(['symbol', 'date_i'])[['close', 'preclose', 'up']]
    original_d1 = top.d1_close.to_numpy(copy=True)
    for gap in range(1, 5):
        key = pd.MultiIndex.from_arrays([top.symbol.to_numpy(), top.date_i.to_numpy() + gap])
        q = quotes.reindex(key)
        top[f'd{gap}_close'] = q.close.to_numpy()
        top[f'd{gap}_preclose'] = q.preclose.to_numpy()
        top[f'd{gap}_up'] = q.up.to_numpy()
    assert np.isclose(original_d1, top.d1_close, equal_nan=True).all()
    # An ex-rights/dividend price discontinuity makes raw open-to-close
    # returns misleading. Flag it after picking; never replace the pick.
    top['price_anomaly'] = False
    for gap in (2, 3, 4):
        prior = top['d1_close'] if gap == 2 else top[f'd{gap-1}_close']
        discontinuity = top[f'd{gap}_preclose'].div(prior).sub(1).abs().gt(.02)
        top['price_anomaly'] |= discontinuity.fillna(False)
    top = add_market_cap(top)
    top['first_close_cap_min'] = np.nan
    top['final_close_cap_min'] = np.nan
    rows = records(top)
    for row, anomaly in zip(rows, top.price_anomaly, strict=True):
        row['集合'] = '2024年5—9月'
        row['价格异常'] = int(bool(anomaly))
        if anomaly:
            for field in ('冷静末涨', 'D2收涨', 'D3收涨', 'D4收涨', '策略退出涨'):
                row[field] = None
    (OUT / 'd0_top5_2024_may_sep_rows.json').write_text(
        json.dumps(rows, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    top[['d0_date', 'd1_date', 'symbol', 'd0_rank', 'score', 'activated',
         'selected', 'price_anomaly', 'label_3', 'd1_up', 'd2_up', 'd3_up', 'd4_up',
         'd1_open', 'd2_close', 'd3_close', 'd4_close']].to_csv(
             OUT / 'internal_audit.csv.gz', index=False, compression='gzip')
    picked = top[top.selected]
    clean = picked[~picked.price_anomaly]
    picked_rows = [r for r in rows if r['D1入选'] == 1 and r['价格异常'] == 0]
    by_month = []
    for month, group in picked.groupby(picked.d0_date.str[:7]):
        matching = [r for r, p in zip(rows, top.itertuples())
                    if p.d0_date.startswith(month) and r['D1入选'] and not r['价格异常']]
        clean_group = group[~group.price_anomaly]
        by_month.append({'month': month, 'picks': len(group),
                         'h3_hits': int(group.label_3.sum()),
                         'd1_reboard': int(group.d1_up.sum()),
                         'price_anomaly_excluded': int(group.price_anomaly.sum()),
                         'd3_mean_gross_clean': float((clean_group.d3_close / clean_group.d1_open - 1).mean()),
                         'strategy_exit_mean_gross': float(np.mean([r['策略退出涨'] for r in matching]))})
    calendar_days = int(panel.d0_date.nunique())
    summary = {'experiment': 'R11 frozen R8 refit forest replay',
               'period': [START, END], 'note': 'Backward historical replay with a future-trained model; not chronological OOS',
               'model_sha256': sha256(model_file), 'model_gate': str(RESTORED / 'reproduction_gate.json'),
               'price_sources': price_sources, 'quality': quality,
               'd0_days': calendar_days, 'd0_rows': len(panel), 'd0_top5_rows': len(top),
               'selected_days': len(picked), 'no_candidate_days': calendar_days - len(picked),
               'price_anomaly_excluded_from_returns': int(picked.price_anomaly.sum()),
               'h3_hits': int(picked.label_3.sum()),
               'h3_hit_rate': float(picked.label_3.mean()) if len(picked) else None,
               'd1_reboard': int(picked.d1_up.sum()),
               'd3_mean_gross_clean': float((clean.d3_close / clean.d1_open - 1).mean()),
               'strategy_exit_mean_gross': float(np.mean([r['策略退出涨'] for r in picked_rows])),
               'market_cap_coverage_top5': int(top.market_cap_100m.notna().sum()),
               'minute_coverage_top5': 0, 'by_month': by_month}
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    run()
