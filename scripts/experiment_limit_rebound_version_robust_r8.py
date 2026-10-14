"""Compare R5 D0 model decisions under sklearn 1.7.1 and 1.9.1.

Prepare the same source frames once, then fit each model in both isolated runtimes.
Only training and validation dates are read; the test period is not evaluated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.experiment_automl_limit_rebound_d0_open_r5 import FEATURES, build_panel, load_daily, split
from scripts.experiment_automl_limit_rebound_model import make_model
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.research_limit_rebound_d1_minute_r6 import R5


OUT = Path('outputs/stock_automl/limit_rebound_version_robust_r8_20261014')
MODELS = ('hgb255', 'hgb32', 'forest', 'logistic')


def make_variant(name: str):
    if name == 'hgb255':
        return make_model('shallow_boost')
    if name == 'hgb32':
        return HistGradientBoostingClassifier(max_iter=120, learning_rate=.05,
            max_leaf_nodes=15, max_depth=3, min_samples_leaf=100,
            l2_regularization=5, max_bins=32, early_stopping=False,
            random_state=20261013)
    if name == 'forest':
        return make_pipeline(SimpleImputer(strategy='median', add_indicator=True),
            RandomForestClassifier(n_estimators=240, max_depth=7,
                min_samples_leaf=50, max_features=.8, n_jobs=1,
                random_state=20261013))
    if name == 'logistic':
        return make_model('logistic')
    raise ValueError(name)


def fit_variant(name: str, train: pd.DataFrame):
    model = make_variant(name)
    weights = balanced_weights(train.label_3)
    x = feature_matrix(train, name)
    if name == 'logistic':
        model.fit(x, train.label_3,
                  logisticregression__sample_weight=weights)
    elif name == 'forest':
        model.fit(x, train.label_3,
                  randomforestclassifier__sample_weight=weights)
    else:
        model.fit(x, train.label_3, sample_weight=weights)
    return model


def feature_matrix(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    x = frame[list(FEATURES)]
    # Keep the R5 histogram baseline byte-for-byte. The two imputer-based
    # alternatives require the same explicit infinity-to-missing conversion.
    return x.replace([np.inf, -np.inf], np.nan) if name in ('forest', 'logistic') else x


def selected(frame: pd.DataFrame, score: np.ndarray, top5_only: bool = False) -> pd.DataFrame:
    f = frame[['symbol', 'd0_date', 'd1_date', 'activated', 'd1_up', 'label_3']].copy()
    f['score'] = score
    f = f.sort_values(['d0_date', 'score', 'symbol'], ascending=[True, False, True])
    f['d0_rank'] = f.groupby('d0_date').cumcount() + 1
    eligible = f[f.activated].copy()
    eligible['open_rank'] = eligible.groupby('d1_date').cumcount() + 1
    if top5_only:
        return eligible[eligible.d0_rank.le(5)].sort_values(
            ['d1_date', 'd0_rank']).groupby('d1_date').head(1)
    return eligible[eligible.open_rank.eq(1)].copy()


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    panel, _ = build_panel(daily, R5, score_all_d0=True)
    train, valid, _, _ = split(panel, sorted(daily.trade_date.unique()), 3)
    valid_all = panel[panel.d0_date.between(valid.d0_date.min(), valid.d0_date.max()) &
                      panel.observed_3].copy()
    assert len(valid_all[valid_all.activated]) == len(valid)
    train.to_pickle(OUT / 'train.pkl')
    valid.to_pickle(OUT / 'valid.pkl')
    valid_all.to_pickle(OUT / 'valid_all.pkl')
    metadata = {'train_rows': len(train), 'valid_rows': len(valid),
                'valid_all_rows': len(valid_all),
                'train_dates': [train.d0_date.min(), train.d0_date.max()],
                'valid_dates': [valid.d0_date.min(), valid.d0_date.max()]}
    (OUT / 'data.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


def prepare_test():
    """One frozen follow-up after the forest was chosen from train/validation."""
    daily = load_daily()
    panel, _ = build_panel(daily, R5, score_all_d0=True)
    _, _, refit, test = split(panel, sorted(daily.trade_date.unique()), 3)
    test_all = panel[panel.d0_date.between(test.d0_date.min(), test.d0_date.max()) &
                     panel.observed_3].copy()
    assert len(test_all[test_all.activated]) == len(test)
    refit.to_pickle(OUT / 'refit.pkl')
    test.to_pickle(OUT / 'test.pkl')
    test_all.to_pickle(OUT / 'test_all.pkl')
    meta = {'refit_rows': len(refit), 'test_rows': len(test),
            'test_all_rows': len(test_all),
            'test_dates': [test.d0_date.min(), test.d0_date.max()]}
    (OUT / 'test_data.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(meta, ensure_ascii=False), flush=True)


def fit():
    version = sklearn.__version__
    if version not in ('1.7.1', '1.9.1'):
        raise RuntimeError(f'Unexpected sklearn version {version}')
    train = pd.read_pickle(OUT / 'train.pkl')
    valid = pd.read_pickle(OUT / 'valid.pkl')
    valid_all = pd.read_pickle(OUT / 'valid_all.pkl')
    metrics = {}
    for name in MODELS:
        model = fit_variant(name, train)
        train_score = model.predict_proba(feature_matrix(train, name))[:, 1]
        valid_score = model.predict_proba(feature_matrix(valid, name))[:, 1]
        all_score = model.predict_proba(feature_matrix(valid_all, name))[:, 1]
        if name == 'hgb255' and version == '1.7.1':
            saved = pd.read_csv(R5 / 'h3_valid_scores.csv.gz', dtype={'symbol': str, 'd0_date': str})
            check = valid[['symbol', 'd0_date']].copy()
            check['score'] = valid_score
            check = check.merge(saved[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'],
                                suffixes=('', '_saved'), validate='one_to_one')
            assert len(check) == len(valid)
            assert np.allclose(check.score, check.score_saved, rtol=0, atol=1e-12)
        train_top = selected(train, train_score)
        valid_top = selected(valid_all, all_score)
        metrics[name] = {
            'train_ap': float(average_precision_score(train.label_3, train_score)),
            'valid_ap': float(average_precision_score(valid.label_3, valid_score)),
            'valid_auc': float(roc_auc_score(valid.label_3, valid_score)),
            'train_top1_days': len(train_top), 'train_top1_h3_hits': int(train_top.label_3.sum()),
            'valid_top1_days': len(valid_top), 'valid_top1_h3_hits': int(valid_top.label_3.sum()),
            'valid_top1_d1_hits': int(valid_top.d1_up.sum()),
            'valid_top1_within_d0_top5': int(valid_top.d0_rank.le(5).sum()),
        }
        scores = valid_all[['symbol', 'd0_date', 'd1_date', 'activated', 'd1_up', 'label_3']].copy()
        scores['score'] = all_score
        scores.to_csv(OUT / f'valid_scores_{name}_sk{version.replace(".", "")}.csv.gz',
                      index=False, compression='gzip')
        train_top[['d1_date', 'symbol', 'score', 'label_3']].to_csv(
            OUT / f'train_top1_{name}_sk{version.replace(".", "")}.csv', index=False)
        print(version, name, metrics[name], flush=True)
    (OUT / f'metrics_sk{version.replace(".", "")}.json').write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + '\n')


def compare():
    m171 = json.loads((OUT / 'metrics_sk171.json').read_text())
    m191 = json.loads((OUT / 'metrics_sk191.json').read_text())
    rows = []
    for name in MODELS:
        a = pd.read_csv(OUT / f'valid_scores_{name}_sk171.csv.gz', dtype={'symbol': str})
        b = pd.read_csv(OUT / f'valid_scores_{name}_sk191.csv.gz', dtype={'symbol': str})
        assert a[['symbol', 'd0_date']].equals(b[['symbol', 'd0_date']])
        diff = (a.score - b.score).abs()
        pa = selected(a, a.score.to_numpy())
        pb = selected(b, b.score.to_numpy())
        pair = pa[['d1_date', 'symbol']].merge(pb[['d1_date', 'symbol']], on='d1_date',
                                               suffixes=('_171', '_191'), validate='one_to_one')
        ta = pd.read_csv(OUT / f'train_top1_{name}_sk171.csv', dtype={'symbol': str})
        tb = pd.read_csv(OUT / f'train_top1_{name}_sk191.csv', dtype={'symbol': str})
        train_pair = ta[['d1_date', 'symbol']].merge(tb[['d1_date', 'symbol']], on='d1_date',
                                                    suffixes=('_171', '_191'), validate='one_to_one')
        rows.append({'model': name,
            'train_top1_same': int(train_pair.symbol_171.eq(train_pair.symbol_191).sum()),
            'train_top1_days': len(train_pair),
            'valid_top1_same': int(pair.symbol_171.eq(pair.symbol_191).sum()),
            'valid_top1_days': len(pair),
            'valid_score_abs_median': float(diff.median()),
            'valid_score_abs_p90': float(diff.quantile(.9)),
            'valid_score_abs_max': float(diff.max()),
            'sk171': m171[name], 'sk191': m191[name]})
    out = {'experiment': 'R8_sklearn_171_191_stability', 'train_valid_only': True,
           'models': rows}
    (OUT / 'summary.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(out, ensure_ascii=False, indent=2))


def fit_test():
    version = sklearn.__version__
    if version not in ('1.7.1', '1.9.1'):
        raise RuntimeError(f'Unexpected sklearn version {version}')
    refit = pd.read_pickle(OUT / 'refit.pkl')
    test = pd.read_pickle(OUT / 'test.pkl')
    test_all = pd.read_pickle(OUT / 'test_all.pkl')
    model = fit_variant('forest', refit)
    activated_score = model.predict_proba(feature_matrix(test, 'forest'))[:, 1]
    all_score = model.predict_proba(feature_matrix(test_all, 'forest'))[:, 1]
    top = selected(test_all, all_score)
    top5 = selected(test_all, all_score, top5_only=True)
    result = {'sklearn': version, 'model': 'forest',
              'ap': float(average_precision_score(test.label_3, activated_score)),
              'auc': float(roc_auc_score(test.label_3, activated_score)),
              'top1_days': len(top), 'top1_h3_hits': int(top.label_3.sum()),
              'top1_d1_hits': int(top.d1_up.sum()),
              'top1_within_d0_top5': int(top.d0_rank.le(5).sum()),
              'top5_open_days': len(top5), 'top5_open_h3_hits': int(top5.label_3.sum()),
              'top5_open_d1_hits': int(top5.d1_up.sum())}
    frame = test_all[['symbol', 'd0_date', 'd1_date', 'activated', 'd1_up', 'label_3']].copy()
    frame['score'] = all_score
    frame.to_csv(OUT / f'test_scores_forest_sk{version.replace(".", "")}.csv.gz',
                 index=False, compression='gzip')
    (OUT / f'test_metrics_forest_sk{version.replace(".", "")}.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)


def compare_test():
    a = pd.read_csv(OUT / 'test_scores_forest_sk171.csv.gz', dtype={'symbol': str})
    b = pd.read_csv(OUT / 'test_scores_forest_sk191.csv.gz', dtype={'symbol': str})
    assert a[['symbol', 'd0_date']].equals(b[['symbol', 'd0_date']])
    pa = selected(a, a.score.to_numpy())
    pb = selected(b, b.score.to_numpy())
    qa = selected(a, a.score.to_numpy(), top5_only=True)
    qb = selected(b, b.score.to_numpy(), top5_only=True)
    pair = pa[['d1_date', 'symbol']].merge(pb[['d1_date', 'symbol']], on='d1_date',
                                           suffixes=('_171', '_191'), validate='one_to_one')
    qpair = qa[['d1_date', 'symbol']].merge(qb[['d1_date', 'symbol']], on='d1_date',
                                           suffixes=('_171', '_191'), validate='one_to_one')
    diff = (a.score - b.score).abs()
    result = {'same_top1_days': int(pair.symbol_171.eq(pair.symbol_191).sum()),
              'top1_days': len(pair), 'score_abs_median': float(diff.median()),
              'score_abs_p90': float(diff.quantile(.9)),
              'same_top5_open_days': int(qpair.symbol_171.eq(qpair.symbol_191).sum()),
              'shared_top5_open_days': len(qpair),
              'sk171': json.loads((OUT / 'test_metrics_forest_sk171.json').read_text()),
              'sk191': json.loads((OUT / 'test_metrics_forest_sk191.json').read_text())}
    (OUT / 'test_comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


def fit_test_baseline():
    version = sklearn.__version__
    refit = pd.read_pickle(OUT / 'refit.pkl')
    test = pd.read_pickle(OUT / 'test.pkl')
    test_all = pd.read_pickle(OUT / 'test_all.pkl')
    model = fit_variant('hgb255', refit)
    activated_score = model.predict_proba(feature_matrix(test, 'hgb255'))[:, 1]
    all_score = model.predict_proba(feature_matrix(test_all, 'hgb255'))[:, 1]
    if version == '1.7.1':
        saved = pd.read_csv(R5 / 'h3_test_scores.csv.gz', dtype={'symbol': str, 'd0_date': str})
        chk = test[['symbol', 'd0_date']].copy()
        chk['score'] = activated_score
        chk = chk.merge(saved[['symbol', 'd0_date', 'score']], on=['symbol', 'd0_date'],
                        suffixes=('', '_saved'), validate='one_to_one')
        assert len(chk) == len(test)
        assert np.allclose(chk.score, chk.score_saved, rtol=0, atol=1e-12)
    top = selected(test_all, all_score)
    top5 = selected(test_all, all_score, top5_only=True)
    result = {'sklearn': version, 'model': 'hgb255',
              'ap': float(average_precision_score(test.label_3, activated_score)),
              'top1_days': len(top), 'top1_h3_hits': int(top.label_3.sum()),
              'top1_d1_hits': int(top.d1_up.sum()),
              'top5_open_days': len(top5), 'top5_open_h3_hits': int(top5.label_3.sum())}
    frame = test_all[['symbol', 'd0_date', 'd1_date', 'activated', 'd1_up', 'label_3']].copy()
    frame['score'] = all_score
    frame.to_csv(OUT / f'test_scores_hgb255_sk{version.replace(".", "")}.csv.gz',
                 index=False, compression='gzip')
    (OUT / f'test_metrics_hgb255_sk{version.replace(".", "")}.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)


def compare_test_baseline():
    a = pd.read_csv(OUT / 'test_scores_hgb255_sk171.csv.gz', dtype={'symbol': str})
    b = pd.read_csv(OUT / 'test_scores_hgb255_sk191.csv.gz', dtype={'symbol': str})
    assert a[['symbol', 'd0_date']].equals(b[['symbol', 'd0_date']])
    pa = selected(a, a.score.to_numpy())
    pb = selected(b, b.score.to_numpy())
    qa = selected(a, a.score.to_numpy(), top5_only=True)
    qb = selected(b, b.score.to_numpy(), top5_only=True)
    pair = pa[['d1_date', 'symbol']].merge(pb[['d1_date', 'symbol']], on='d1_date',
                                           suffixes=('_171', '_191'), validate='one_to_one')
    qpair = qa[['d1_date', 'symbol']].merge(qb[['d1_date', 'symbol']], on='d1_date',
                                           suffixes=('_171', '_191'), validate='one_to_one')
    result = {'same_top1_days': int(pair.symbol_171.eq(pair.symbol_191).sum()),
              'top1_days': len(pair),
              'same_top5_open_days': int(qpair.symbol_171.eq(qpair.symbol_191).sum()),
              'shared_top5_open_days': len(qpair),
              'sk171': json.loads((OUT / 'test_metrics_hgb255_sk171.json').read_text()),
              'sk191': json.loads((OUT / 'test_metrics_hgb255_sk191.json').read_text())}
    (OUT / 'test_comparison_baseline.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'fit', 'compare', 'prepare_test',
                                           'fit_test', 'compare_test', 'fit_test_baseline',
                                           'compare_test_baseline'))
    args = parser.parse_args()
    {'prepare': prepare, 'fit': fit, 'compare': compare,
     'prepare_test': prepare_test, 'fit_test': fit_test,
     'compare_test': compare_test, 'fit_test_baseline': fit_test_baseline,
     'compare_test_baseline': compare_test_baseline}[args.action]()
