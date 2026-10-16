"""Evaluate R5/R8 D1-open classification and fixed-exit returns on matched dates."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_automl_limit_rebound_d0_open_r5 import load_daily


R8 = Path('outputs/stock_automl/limit_rebound_version_robust_r8_20261014')
OUT = Path('outputs/stock_automl/limit_rebound_effect_r9_20261014')
MODELS = ('hgb255_sk171', 'hgb255_sk191', 'forest_sk171', 'forest_sk191', 'streak')


def price_enriched(frame: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    quotes = daily.set_index(['symbol', 'date_i'])[['close', 'preclose']]
    for gap in (2, 3):
        keys = pd.MultiIndex.from_arrays([f.symbol.to_numpy(), f.date_i.to_numpy() + gap])
        f[f'd{gap}_close'] = quotes.close.reindex(keys).to_numpy()
        f[f'd{gap}_preclose'] = quotes.preclose.reindex(keys).to_numpy()
        f[f'd{gap}_gross'] = f[f'd{gap}_close'].div(f.d1_open).sub(1)
    # Cross-check raw-price results against daily preclose-based price changes.
    f['d3_chain'] = (f.d1_close.div(f.d1_open) *
                     f.d2_close.div(f.d2_preclose) *
                     f.d3_close.div(f.d3_preclose) - 1)
    f['corporate_action_gap'] = f.d3_chain.sub(f.d3_gross).abs()
    return f


def rank_picks(frame: pd.DataFrame, score: pd.Series | None, model: str,
               scope: str) -> pd.DataFrame:
    f = frame.copy()
    if model == 'streak':
        f = f.sort_values(['d0_date', 'board_streak_days', 'symbol'],
                          ascending=[True, False, True])
    else:
        assert score is not None
        f['score'] = score.loc[f.index]
        f = f.sort_values(['d0_date', 'score', 'symbol'], ascending=[True, False, True])
    f['d0_rank'] = f.groupby('d0_date').cumcount() + 1
    f = f[f.activated].copy()
    if scope == 'd0_top5':
        f = f[f.d0_rank.le(5)].copy()
    if model == 'streak':
        f = f.sort_values(['d1_date', 'board_streak_days', 'symbol'],
                          ascending=[True, False, True])
    else:
        f = f.sort_values(['d1_date', 'score', 'symbol'], ascending=[True, False, True])
    f['rank'] = f.groupby('d1_date').cumcount() + 1
    f['model'] = model
    f['scope'] = scope
    return f[f['rank'].le(3)].copy()


def metrics(frame: pd.DataFrame, k: int, total_days: int) -> dict:
    f = frame[frame['rank'].le(k)]
    by_day = f.groupby('d1_date')
    return {'days': int(f.d1_date.nunique()), 'signal_days': total_days,
            'no_candidate_days': total_days - int(f.d1_date.nunique()), 'picks': len(f),
            'h3_hits': int(f.label_3.sum()), 'h3_rate': float(f.label_3.mean()),
            'd1_reboard': int(f.d1_up.sum()),
            'd2_price_coverage': int(f.d2_gross.notna().sum()),
            'd3_price_coverage': int(f.d3_gross.notna().sum()),
            'd2_mean_gross': float(f.d2_gross.mean()),
            'd3_mean_gross': float(f.d3_gross.mean()),
            'd3_median_gross': float(f.d3_gross.median()),
            'd3_win_rate': float(f.d3_gross.gt(0).mean()),
            'd3_day_equal_mean': float(by_day.d3_gross.mean().mean()),
            'd3_cash_on_no_candidate_mean': float(by_day.d3_gross.mean().sum() / total_days),
            'zero_d1_daily_volume_picks': int(f.d1_volume.le(0).sum()),
            'raw_chain_gap_gt_2pp': int(f.corporate_action_gap.gt(.02).sum())}


def paired_delta(picks: pd.DataFrame, part: str, scope: str, left: str, right: str) -> dict:
    a = picks[(picks.part.eq(part)) & picks.scope.eq(scope) &
              picks.model.eq(left) & picks['rank'].eq(1)]
    b = picks[(picks.part.eq(part)) & picks.scope.eq(scope) &
              picks.model.eq(right) & picks['rank'].eq(1)]
    p = a[['d1_date', 'symbol', 'label_3', 'd3_gross']].merge(
        b[['d1_date', 'symbol', 'label_3', 'd3_gross']], on='d1_date',
        suffixes=('_a', '_b'), validate='one_to_one')
    assert p.d3_gross_a.notna().all() and p.d3_gross_b.notna().all()
    delta = p.d3_gross_a.sub(p.d3_gross_b).to_numpy()
    rng = np.random.default_rng(20261014)
    indices = rng.integers(0, len(delta), size=(5000, len(delta)))
    interval = np.quantile(delta[indices].mean(axis=1), [.025, .975])
    return {'scope': scope, 'left': left, 'right': right, 'common_days': len(p),
            'left_signal_days': len(a), 'right_signal_days': len(b),
            'same_stock_days': int(p.symbol_a.eq(p.symbol_b).sum()),
            'h3_hit_difference': int(p.label_3_a.sum() - p.label_3_b.sum()),
            'mean_d3_gross_difference': float(delta.mean()),
            'mean_d3_gross_difference_bootstrap_95': interval.tolist()}


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    picks_all = []
    result = {'experiment': 'R9_R5_R8_effect_and_fixed_exit',
              'entry': 'D1 daily open; activated only if below limit price',
              'exit': 'D2 or D3 daily close; price-only gross, before friction',
              'parts': {}, 'paired': []}
    for part in ('valid', 'test'):
        panel = pd.read_pickle(R8 / f'{part}_all.pkl')
        panel = price_enriched(panel, daily)
        assert panel[['symbol', 'd0_date']].duplicated().sum() == 0
        all_picks = []
        for model in MODELS:
            if model == 'streak':
                score = None
            else:
                path = R8 / f'{part}_scores_{model}.csv.gz'
                saved = pd.read_csv(path, dtype={'symbol': str, 'd0_date': str})
                merged = panel[['symbol', 'd0_date']].merge(
                    saved[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'],
                    how='left', validate='one_to_one')
                assert len(merged) == len(panel) and merged.score.notna().all()
                score = merged.score
                score.index = panel.index
            for scope in ('all_open', 'd0_top5'):
                picks = rank_picks(panel, score, model, scope)
                picks['part'] = part
                all_picks.append(picks)
        part_picks = pd.concat(all_picks, ignore_index=True)
        picks_all.append(part_picks)
        total_days = int(panel.d1_date.nunique())
        result['parts'][part] = {scope: {
            model: {str(k): metrics(part_picks[(part_picks.model.eq(model)) &
                                              (part_picks.scope.eq(scope))], k, total_days)
                    for k in (1, 2, 3)} for model in MODELS}
            for scope in ('all_open', 'd0_top5')}
        for scope in ('all_open', 'd0_top5'):
            for version in ('sk171', 'sk191'):
                result['paired'].append({'part': part, **paired_delta(
                    part_picks, part, scope, f'forest_{version}', f'hgb255_{version}')})
                result['paired'].append({'part': part, **paired_delta(
                    part_picks, part, scope, f'forest_{version}', 'streak')})
    audit = pd.concat(picks_all, ignore_index=True)
    audit[['part', 'scope', 'model', 'd1_date', 'symbol', 'rank', 'd0_rank', 'label_3', 'd1_up',
           'd1_open', 'd1_volume', 'd2_close', 'd3_close', 'd2_gross', 'd3_gross',
           'corporate_action_gap']].to_csv(OUT / 'daily_top3_audit.csv.gz',
                                           index=False, compression='gzip')
    (OUT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    run()
