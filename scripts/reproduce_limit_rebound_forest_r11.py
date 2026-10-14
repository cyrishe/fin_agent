"""Restore the R8 forest artifacts and gate a historical replay on exact reproduction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_limit_rebound_version_robust_r8 import (
    feature_matrix, fit_variant, selected,
)
from scripts.analyze_limit_rebound_effect_r9 import rank_picks


R8 = Path('outputs/stock_automl/limit_rebound_version_robust_r8_20261014')
R9 = Path('outputs/stock_automl/limit_rebound_effect_r9_20261014')
OUT = Path('outputs/stock_automl/limit_rebound_forest_restored_r11_20261014')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(part: str, fit_name: str) -> dict:
    training = pd.read_pickle(R8 / f'{fit_name}.pkl')
    panel = pd.read_pickle(R8 / f'{part}_all.pkl')
    activated = panel[panel.activated].copy()
    model = fit_variant('forest', training)
    model_path = OUT / f'forest_{fit_name}_sk171.joblib'
    joblib.dump(model, model_path, compress=3)
    restored = joblib.load(model_path)
    score = restored.predict_proba(feature_matrix(panel, 'forest'))[:, 1]
    in_memory = model.predict_proba(feature_matrix(panel, 'forest'))[:, 1]
    assert np.array_equal(score, in_memory), 'Model changed across save/load'

    saved_path = R8 / f'{part}_scores_forest_sk171.csv.gz'
    old = pd.read_csv(saved_path, dtype={'symbol': str, 'd0_date': str})
    keyed = panel[['symbol', 'd0_date']].copy()
    keyed['score_new'] = score
    joined = keyed.merge(old[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'],
                         how='outer', validate='one_to_one', indicator=True)
    assert joined._merge.eq('both').all(), 'Score row keys differ'
    diff = (joined.score_new - joined.score).abs()
    assert diff.le(1e-12).all(), f'{part}: score reproduction failed: {diff.max()}'

    old_scores = panel[['symbol', 'd0_date']].merge(
        old[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'],
        how='left', validate='one_to_one').score
    old_scores.index = panel.index
    new_scores = pd.Series(score, index=panel.index)
    new_picks = rank_picks(panel, new_scores, 'forest_sk171', 'd0_top5')
    old_picks = rank_picks(panel, old_scores, 'forest_sk171', 'd0_top5')
    new_top = new_picks[new_picks['rank'].eq(1)].reset_index(drop=True)
    old_top = old_picks[old_picks['rank'].eq(1)].reset_index(drop=True)
    assert new_top[['d1_date', 'symbol']].equals(old_top[['d1_date', 'symbol']]), \
        f'{part}: Top1 picks differ'
    old_summary = json.loads((R9 / 'summary.json').read_text())['parts'][part]['d0_top5']['forest_sk171']['1']
    assert len(new_top) == old_summary['picks']
    assert int(new_top.label_3.sum()) == old_summary['h3_hits']
    assert int(new_top.d1_up.sum()) == old_summary['d1_reboard']

    active_scores = pd.Series(score, index=panel.index).loc[activated.index]
    result = {
        'training_rows': len(training), 'activated_eval_rows': len(activated),
        'all_d0_eval_rows': len(panel),
        'score_max_abs_diff': float(diff.max()),
        'score_rows_over_1e_12': int(diff.gt(1e-12).sum()),
        'top1_same': len(new_top), 'top1_total': len(old_top),
        'top1_h3_hits': int(new_top.label_3.sum()),
        'top1_d1_reboard': int(new_top.d1_up.sum()),
        'ap': float(average_precision_score(activated.label_3, active_scores)),
        'auc': float(roc_auc_score(activated.label_3, active_scores)),
        'source_panel_sha256': sha256(R8 / f'{part}_all.pkl'),
        'source_training_sha256': sha256(R8 / f'{fit_name}.pkl'),
        'reference_scores_sha256': sha256(saved_path),
        'model_path': str(model_path), 'model_sha256': sha256(model_path),
    }
    (OUT / f'{part}_reproduction.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main() -> None:
    if sklearn.__version__ != '1.7.1':
        raise RuntimeError(f'Requires original sklearn 1.7.1, got {sklearn.__version__}')
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'experiment': 'R11 exact restoration of R8 forest',
              'sklearn': sklearn.__version__, 'validation': verify('valid', 'train'),
              'test': verify('test', 'refit'), 'gate_passed': True}
    (OUT / 'reproduction_gate.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
