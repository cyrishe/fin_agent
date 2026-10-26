"""Audit saved original rolling B1 models without fitting or selecting new rules."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'docs/stock_automl_runs/20261026_b1_model_diagnostic_co22'
OUT = ROOT / 'outputs/stock_automl/b1_model_diagnostic_co22_20261026'
LABELS = ('lt0', '0to1', '1to3', 'ge3')
PROBS = [f'p_{c}' for c in LABELS]
FEATURES = ['signal_return', 'volume_ratio', 'turnover_so_far_pct',
            'float_mv_100m_cny', 'volume_4of5_increasing', 'ma_bull_5_10_20',
            'all_intraday_lows_above_ma']
PERIODS = {
    '2025_early': ('2025-02-07', '2025-07-31'),
    '2025_late': ('2025-08-01', '2025-12-31'),
    '2026_H1': ('2026-01-01', '2026-06-30'),
    '2026_Q3': ('2026-07-01', '2026-09-30'),
    'original_2026_Feb_Jul': ('2026-02-02', '2026-07-30'),
    '2026_Oct_partial': ('2026-10-01', '2026-10-13'),
    'all_through_Sep': ('2025-02-07', '2026-09-30'),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            h.update(part)
    return h.hexdigest()


def read(path, **kwargs):
    return pd.read_csv(path, dtype={'symbol6': str}, float_precision='round_trip', **kwargs)


def classify(ret):
    return np.select([ret < 0, ret < .01, ret < .03], [0, 1, 2], default=3).astype(int)


def metrics(y, p, prior):
    y = np.asarray(y, int)
    p = np.asarray(p, float)
    if len(y) == 0:
        return {'rows': 0}
    prior = np.broadcast_to(prior, p.shape)
    pred = p.argmax(axis=1)
    positive = y == 3
    ge1 = y >= 2
    low = y == 0
    ll = lambda z: float(-np.log(np.clip(z[np.arange(len(y)), y], 1e-15, 1)).mean())
    recall = [np.mean(pred[y == k] == k) for k in range(4) if np.any(y == k)]
    return dict(rows=len(y), base_ge3=float(positive.mean()),
                auc_ge3=float(roc_auc_score(positive, p[:, 3])) if positive.min() != positive.max() else np.nan,
                ap_ge3=float(average_precision_score(positive, p[:, 3])) if positive.any() else np.nan,
                auc_ge1=float(roc_auc_score(ge1, p[:, 2:].sum(axis=1))) if ge1.min() != ge1.max() else np.nan,
                ap_ge1=float(average_precision_score(ge1, p[:, 2:].sum(axis=1))) if ge1.any() else np.nan,
                base_ge1=float(ge1.mean()),
                auc_lt0=float(roc_auc_score(low, p[:, 0])) if low.min() != low.max() else np.nan,
                accuracy=float(np.mean(pred == y)), balanced_accuracy=float(np.mean(recall)),
                prior_accuracy=float(np.mean(prior.argmax(axis=1) == y)),
                logloss=ll(p), prior_logloss=ll(prior),
                brier_ge3=float(np.mean((p[:, 3] - positive) ** 2)),
                prior_brier_ge3=float(np.mean((prior[:, 3] - positive) ** 2)),
                predicted_ge3=int(np.sum(pred == 3)),
                recall_ge3=float(np.mean(pred[positive] == 3)) if positive.any() else np.nan,
                precision_ge3=float(np.mean(y[pred == 3] == 3)) if np.any(pred == 3) else np.nan,
                mean_p_ge3=float(p[:, 3].mean()))


def groups(d, folds):
    groups = [(f'month_{m}', g, folds[folds.day.str.startswith(m)])
              for m, g in d.groupby('month')]
    for name, (a, b) in PERIODS.items():
        groups.append((name, d[d.signal_date.between(a, b)], folds[folds.day.between(a, b)]))
    return groups


def analyze(d, folds):
    summaries, top_rows, deciles, confusion, bridges = [], [], [], [], []
    for name, frame, fs in groups(d, folds):
        known = frame[frame.target.notna()]
        y = classify(known.target.to_numpy())
        met = metrics(y, known[PROBS], known[[f'q_{c}' for c in LABELS]])
        met.update(scope=name, days=len(fs), missing_target=int(frame.target.isna().sum()),
                   mean_daily_auc=float(fs.test_auc_ge3.mean()),
                   mean_daily_auc_ge1=float(fs.test_auc_ge1.mean()),
                   mean_train_auc_ge1=float(fs.train_auc_ge1.mean()),
                   mean_daily_balanced_accuracy=float(fs.test_balanced_accuracy.mean()),
                   mean_train_auc=float(fs.train_auc_ge3.mean()),
                   mean_train_balanced_accuracy=float(fs.train_balanced_accuracy.mean()),
                   mean_train_logloss=float(fs.train_logloss.mean()),
                   mean_train_prior_logloss=float(fs.train_prior_logloss.mean()),
                   mean_daily_logloss_delta=float((fs.test_logloss - fs.test_prior_logloss).mean()))
        summaries.append(met)
        for actual in range(4):
            for predicted in range(4):
                confusion.append(dict(scope=name, actual=LABELS[actual], predicted=LABELS[predicted],
                                      rows=int(np.sum((y == actual) & (known[PROBS].to_numpy().argmax(1) == predicted)))))
        for decile, x in known.groupby('within_day_decile'):
            deciles.append(dict(scope=name, decile=int(decile), rows=len(x),
                                predicted_ge3=float(x.p_ge3.mean()), actual_ge3=float(x.target.ge(.03).mean()),
                                target_lt0=float(x.target.lt(0).mean()),
                                mean_net=float(x.net.mean()), critical_net=float(x.loc[x.net.notna(), 'net'].lt(-.02).mean())))
        for selected_only in (False, True):
            source = known[known.top1] if selected_only else known
            for label in range(4):
                x = source[classify(source.target.to_numpy()) == label]
                sell = x[x.net.notna()]
                bridges.append(dict(scope=name, population='top1' if selected_only else 'all_candidates',
                                    actual_class=LABELS[label], target_rows=len(x), sell_rows=len(sell),
                                    mean_target=float(x.target.mean()), mean_net=float(sell.net.mean()),
                                    net_loss_rate=float(sell.net.lt(0).mean()),
                                    net_critical_rate=float(sell.net.lt(-.02).mean())))
        for gate in ('before_amount', 'after_amount'):
            selected = frame[frame.top1 & (frame.amount_gate.eq(1) if gate == 'after_amount' else True)].copy()
            labeled = selected[selected.target.notna()]
            sold = selected[selected.net.notna()]
            strong = sold[sold.target.ge(.03)]
            cash = np.r_[1., np.cumprod(1 + sold.net)]
            top_rows.append(dict(scope=name, gate=gate, selected=len(selected), labeled=len(labeled),
                strong_count=int(labeled.target.ge(.03).sum()), strong_rate=float(labeled.target.ge(.03).mean()),
                acceptable_rate=float(labeled.target.ge(.01).mean()), predicted_p_ge3=float(labeled.p_ge3.mean()),
                same_day_pool_ge3=float(labeled.pool_ge3.mean()),
                same_day_pool_ge1=float(labeled.pool_ge1.mean()),
                same_day_eligible_ge3=float(labeled.eligible_ge3.mean()),
                hit_lift_pp=float(100 * (labeled.target.ge(.03).mean() - labeled.pool_ge3.mean())),
                predicted_top1_ge3=int(selected.predicted_class.eq('ge3').sum()),
                sold=len(sold), mean_net=float(sold.net.mean()), net_compound=float(cash[-1] - 1),
                gross_compound=float(np.prod(sold.close40 / sold.signal_price) - 1),
                max_settled_drawdown=float(np.max(1 - cash / np.maximum.accumulate(cash))),
                net_critical_count=int(sold.net.lt(-.02).sum()),
                strong_but_net_loss=int(strong.net.lt(0).sum()), strong_with_sell=len(strong),
                strong_but_net_loss_rate=float(strong.net.lt(0).mean()),
                target_price_mean=float(labeled.target.mean())))
    for file, rows in [('classification.csv', summaries), ('top1.csv', top_rows),
                       ('score_deciles.csv', deciles), ('confusion.csv', confusion),
                       ('target_bridge.csv', bridges)]:
        pd.DataFrame(rows).to_csv(REPORT / file, index=False)


def supplement(d, folds):
    branches, attribution = [], []
    for name, frame, _ in groups(d, folds):
        for amount in (False, True):
            selected = frame[frame.top1 & (frame.amount_gate.eq(1) if amount else True)]
            for branch, rows in selected.groupby('predicted_class'):
                x = rows[rows.target.notna()]
                branches.append(dict(scope=name, amount_gate=amount, branch=branch, rows=len(x),
                    hit_ge3=float(x.target.ge(.03).mean()), hit_ge1=float(x.target.ge(.01).mean()),
                    target_lt0=float(x.target.lt(0).mean()), mean_p_ge3=float(x.p_ge3.mean()),
                    mean_p_ge1=float((x.p_ge3+x.p_1to3).mean()), mean_net=float(x.net.mean()),
                    critical_count=int(x.net.lt(-.02).sum())))
        selected = frame[frame.top1 & frame.amount_gate.eq(1) & frame.target.notna()]
        for k, label in enumerate(LABELS):
            x = selected[classify(selected.target.to_numpy()) == k]
            attribution.append(dict(scope=name, actual_class=label, rows=len(x),
                mean_net=float(x.net.mean()), sum_net=float(x.net.sum()),
                negative_count=int(x.net.lt(0).sum()), critical_count=int(x.net.lt(-.02).sum())))
    pd.DataFrame(branches).to_csv(REPORT / 'selection_branches.csv', index=False)
    pd.DataFrame(attribution).to_csv(REPORT / 'loss_attribution.csv', index=False)
    rng = np.random.default_rng(20261026)

    def interval(values, months):
        v = pd.DataFrame({'value': np.asarray(values, float), 'month': np.asarray(months)}).dropna()
        agg = v.groupby('month').value.agg(['sum', 'count'])
        chosen = rng.integers(len(agg), size=(10000, len(agg)))
        means = agg['sum'].to_numpy()[chosen].sum(axis=1) / agg['count'].to_numpy()[chosen].sum(axis=1)
        return dict(mean=float(v.value.mean()), low95=float(np.quantile(means, .025)),
                    high95=float(np.quantile(means, .975)), months=len(agg), rows=len(v))

    f = folds[folds.day.le('2026-09-30')]
    result = {'method': '10000 calendar-month cluster resamples; descriptive historical intervals, no correction for prior research selection',
        'day_mean_auc_ge3': interval(f.test_auc_ge3, f.day.str[:7]),
        'day_mean_logloss_delta': interval(f.test_logloss - f.test_prior_logloss, f.day.str[:7])}
    for name, condition in [('top1', d.top1), ('traded_top1', d.top1 & d.amount_gate.eq(1))]:
        selected = d[condition & d.signal_date.le('2026-09-30') & d.target.notna()]
        result[name + '_hit_lift'] = interval(selected.target.ge(.03).astype(float) - selected.pool_ge3, selected.month)
        result[name + '_mean_net'] = interval(selected.net, selected.month)
    (REPORT / 'uncertainty.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


def run(source):
    if sklearn.__version__ != '1.7.1':
        raise ValueError('Saved B1 models require scikit-learn 1.7.1')
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    cols = ['sample_id', 'signal_date', 'symbol6', 'next_date', *FEATURES,
            'signal_price', 'stage1_second_high', 'second_high', 'close40', 'input_source']
    panel = read(source / 'b1_candidates.csv.gz', usecols=cols).set_index('sample_id', drop=False)
    fold_records = json.loads((source / 'stage1_folds.json').read_text())
    top = read(source / 'oof_with_context.csv.gz')
    top = top[top.selection_rank.eq(1)].set_index(['signal_date', 'symbol6'])
    results, summaries, verification = [], [], []
    for number, fold in enumerate(fold_records):
        day = fold['day']
        folder = source / 'stage1' / day
        model_path, scores_path = folder / 'model.joblib', folder / 'scores.csv.gz'
        assert sha(model_path) == fold['model_sha256']
        assert sha(scores_path) == fold['scores_sha256']
        membership = read(folder / 'membership.csv.gz')
        train = panel.loc[membership.sample_id].copy()
        assert len(train) == fold['train_rows']
        assert set(train.signal_date) == set(fold['train_dates']) and train.signal_date.nunique() == 20
        assert train.signal_date.max() < day and train.next_date.max() <= day
        assert train.stage1_second_high.notna().all()
        model = joblib.load(model_path)
        assert list(model.feature_names_in_) == FEATURES
        order = [list(model.classes_).index(c) for c in LABELS]
        ty = classify(train.stage1_second_high.to_numpy() / train.signal_price.to_numpy() - 1)
        prior = np.bincount(ty, minlength=4) / len(ty)
        tp = model.predict_proba(train[FEATURES])[:, order]
        train_metrics = metrics(ty, tp, prior)
        scored = read(scores_path)
        predicted = model.predict_proba(scored[FEATURES])[:, order] if len(scored) else np.empty((0, 4))
        np.testing.assert_allclose(predicted, scored[PROBS], rtol=0, atol=1e-14)
        test = panel[panel.signal_date.eq(day)]
        assert len(test) == len(scored) == fold['test_rows']
        labels = test[['symbol6', 'stage1_second_high', 'second_high', 'close40', 'input_source']]
        scored = scored.merge(labels, on='symbol6', how='left', validate='one_to_one')
        scored['target'] = scored.stage1_second_high / scored.signal_price - 1
        scored['independent_target'] = scored.second_high / scored.signal_price - 1
        scored['net'] = scored.close40 * .999 / (scored.signal_price * 1.001) - 1
        for i, label in enumerate(LABELS):
            scored[f'q_{label}'] = prior[i]
        known = scored[scored.target.notna()]
        y = classify(known.target.to_numpy())
        test_metrics = metrics(y, known[PROBS], prior)
        scored['pool_ge3'] = float(known.target.ge(.03).mean())
        scored['pool_ge1'] = float(known.target.ge(.01).mean())
        eligible = known[known.predicted_class.isin(('ge3', '1to3'))]
        scored['eligible_ge3'] = float(eligible.target.ge(.03).mean())
        keys = pd.MultiIndex.from_frame(scored[['signal_date', 'symbol6']])
        scored['top1'] = keys.isin(top.index)
        scored['amount_gate'] = top.amount_gate.reindex(keys).to_numpy()
        if scored.top1.any():
            x = scored[scored.top1]
            saved = top.loc[pd.MultiIndex.from_frame(x[['signal_date', 'symbol6']])]
            np.testing.assert_allclose(x[PROBS], saved[PROBS], rtol=0, atol=1e-14)
        assert scored.top1.sum() <= 1
        scored['within_day_decile'] = np.ceil(scored.p_ge3.rank(method='first', pct=True) * 10).astype(int)
        scored['month'] = day[:7]
        results.append(scored)
        summaries.append(dict(day=day, **{f'train_{k}': v for k, v in train_metrics.items()},
                              **{f'test_{k}': v for k, v in test_metrics.items()}))
        verification.append(dict(day=day, model_sha256=sha(model_path), scores_sha256=sha(scores_path),
                                 membership_sha256=sha(folder / 'membership.csv.gz'),
                                 rows=len(scored), max_prediction_error=float(np.max(np.abs(predicted - scored[PROBS].to_numpy()))) if len(scored) else 0))
        if number % 40 == 0:
            print(f'checked {number+1}/{len(fold_records)} {day}', flush=True)
    d = pd.concat(results, ignore_index=True)
    assert not d.duplicated(['signal_date', 'symbol6']).any()
    both = d.target.notna() & d.independent_target.notna()
    np.testing.assert_allclose(d.loc[both, 'target'], d.loc[both, 'independent_target'], rtol=0, atol=0)
    folds = pd.DataFrame(summaries)
    folds.to_csv(REPORT / 'fold_diagnostics.csv', index=False)
    d.to_csv(OUT / 'oof_predictions.csv.gz', index=False, float_format='%.17g',
             compression={'method': 'gzip', 'mtime': 0})
    pd.DataFrame(verification).to_csv(REPORT / 'model_verification.csv', index=False)
    analyze(d, folds)
    supplement(d, folds)
    # Same original selections and fee basis as CO21, including the early frozen period.
    previous = read(ROOT / 'docs/stock_automl_runs/20261026_b1_early_extension_co21/oof_factor_panel.csv.gz')
    trades = previous[previous.executed]
    current = d[d.top1 & d.amount_gate.eq(1) & d.signal_date.le('2026-09-30')]
    shared = trades.merge(current, on=['signal_date', 'symbol6'], validate='one_to_one', suffixes=('_old', '_new'))
    assert len(shared) == len(trades) == len(current) == 308
    np.testing.assert_allclose(shared.net_old, shared.net_new, rtol=0, atol=1e-14)
    manifest = dict(experiment='CO22_20261026_B1_model_value_audit', source=str(source),
        sklearn=sklearn.__version__, folds=len(folds), oof_rows=len(d),
        mature_target_rows=int(d.target.notna().sum()), independent_label_matches=int(both.sum()),
        co21_trade_matches=len(shared), max_probability_error=max(x['max_prediction_error'] for x in verification),
        inputs={name: sha(source / name) for name in ('b1_candidates.csv.gz', 'oof_with_context.csv.gz', 'stage1_folds.json')},
        code_sha256=sha(Path(__file__)), predictions_sha256=sha(OUT / 'oof_predictions.csv.gz'))
    (REPORT / 'verification.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('/Users/cyrcyrisis/work/fin_agent/outputs/stock_automl/close_open_conditional_b1_r1'))
    args = p.parse_args()
    with threadpool_limits(limits=1):
        run(args.source)
