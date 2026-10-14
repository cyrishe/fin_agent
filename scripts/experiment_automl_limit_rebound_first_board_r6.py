"""R6: first-board-only D0 score, D1 open activation and forward labels.

This is a separate research cohort from R5. The D1 full-day bar is used only
for outcome labels; activation uses the opening price known at the open.
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
    FEATURES, SOURCE, build_panel, load_daily, make_model, split,
)
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights


OUT = Path('outputs/stock_automl/limit_rebound_first_board_r6_20261014')
FEATURES_R6 = tuple(name for name in FEATURES if name not in (
    'board_streak_days', 'streak_return', 'd0_volume_ratio_first',
    'd0_turnover_ratio_first', 'first_open_gap', 'first_range',
    'first_turnover_pct'))


def rank_metrics(frame: pd.DataFrame, score: np.ndarray, label: str) -> dict:
    rows = frame[['symbol', 'd0_date', 'd1_date', 'd1_up', label]].copy()
    rows['score'] = score
    rows = rows.sort_values(['d1_date', 'score', 'symbol'], ascending=[True, False, True])
    top = {}
    for k in (1, 3, 5):
        picks = rows.groupby('d1_date').head(k)
        top[str(k)] = {'hits': int(picks[label].sum()), 'picks': len(picks),
                       'days': int(picks.d1_date.nunique()),
                       'd1_reboard': int(picks.d1_up.sum())}
    y = frame[label].to_numpy(dtype=int)
    return {'rows': len(frame), 'positive': int(y.sum()),
            'ap': float(average_precision_score(y, score)),
            'auc': float(roc_auc_score(y, score)), 'top': top,
            'random_top1_expected_hits': float(frame.groupby('d1_date')[label].mean().sum())}


def run(out: Path = OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    # Reuse the same historical ST snapshot when available; this avoids another
    # database read while preserving R5's candidate universe.
    st = Path('outputs/stock_automl/limit_rebound_d0_open_r5_20261014/historical_st.csv.gz')
    if st.exists() and not (out / 'historical_st.csv.gz').exists():
        (out / 'historical_st.csv.gz').write_bytes(st.read_bytes())
    daily = load_daily()
    calendar = sorted(daily.trade_date.unique())
    panel, quality = build_panel(daily, out)
    panel = panel[panel.board_streak_days.eq(1)].copy()
    panel['activated'] = panel.d1_open.gt(0) & panel.d1_open.lt(panel.d1_cap - .005)
    if any(name.startswith('d1_') for name in FEATURES_R6):
        raise AssertionError('D1 data in model inputs')
    result = {'experiment': 'R6_first_board_only_D0_score_D1_open',
              'sklearn': sklearn.__version__,
              'features': FEATURES_R6, 'source_quality_before_first_board_filter': quality,
              'first_board_rows': len(panel), 'activated_rows': int(panel.activated.sum()),
              'activation': 'D1 open > 0 and below D1 cap; no D1 volume or close filter',
              'horizons': {},
              'source_manifest_sha256': hashlib.sha256((SOURCE / 'manifest.json').read_bytes()).hexdigest(),
              'historical_st_sha256': hashlib.sha256((out / 'historical_st.csv.gz').read_bytes()).hexdigest()}
    for horizon in (3, 4, 5):
        train, valid, refit, test = split(panel, calendar, horizon)
        target = f'label_{horizon}'
        model = make_model('shallow_boost')
        model.fit(train[list(FEATURES_R6)], train[target],
                  sample_weight=balanced_weights(train[target]))
        train_score = model.predict_proba(train[list(FEATURES_R6)])[:, 1]
        valid_score = model.predict_proba(valid[list(FEATURES_R6)])[:, 1]
        final = make_model('shallow_boost')
        final.fit(refit[list(FEATURES_R6)], refit[target],
                  sample_weight=balanced_weights(refit[target]))
        test_score = final.predict_proba(test[list(FEATURES_R6)])[:, 1]
        # A fixed, model-free comparator tests whether any model signal exceeds
        # selecting the first board with the greatest D0 turnover each morning.
        result['horizons'][str(horizon)] = {
            'train_rows': len(train), 'refit_rows': len(refit),
            'train_fit_in': rank_metrics(train, train_score, target),
            'valid': rank_metrics(valid, valid_score, target),
            'test': rank_metrics(test, test_score, target),
            'turnover_comparator_valid': rank_metrics(valid, valid.d0_turnover_pct.fillna(-1).to_numpy(), target),
            'turnover_comparator_test': rank_metrics(test, test.d0_turnover_pct.fillna(-1).to_numpy(), target),
        }
        for part, frame, score in (('valid', valid, valid_score), ('test', test, test_score)):
            rows = frame[['symbol', 'd0_date', 'd1_date', 'd0_turnover_pct',
                          'd1_up', target]].copy()
            rows['score'] = score
            rows.to_csv(out / f'h{horizon}_{part}_scores.csv.gz', index=False, compression='gzip')
        print(horizon, result['horizons'][str(horizon)]['valid']['top']['1'],
              result['horizons'][str(horizon)]['test']['top']['1'], flush=True)
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    run()
