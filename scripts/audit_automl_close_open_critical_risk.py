"""Audit CO6 time boundaries, exact fitting membership and saved predictions."""
import json
from importlib.metadata import version
from pathlib import Path
import platform
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from scripts.experiment_automl_close_open_critical_risk import (
    OUT, REPORT, SOURCE, CO5, FACTORS, PANEL_SHA, CLASSES,
    sha, mature, net_values, day_weights, predict, decision_scores,
)

KEY = ['signal_date', 'symbol6']


def read(path):
    return pd.read_csv(path, dtype={'symbol6': str}, float_precision='round_trip')


def run():
    assert sha(SOURCE/'factor_panel.csv.gz') == PANEL_SHA
    panel = read(SOURCE/'factor_panel.csv.gz').sort_values(KEY).reset_index(drop=True)
    folds = sorted((OUT/'folds').glob('*/complete.json'))
    assert len(folds) == 16
    n_models = n_estimators = n_candidates = n_risk = 0
    union = set()
    for f in folds:
        meta = json.loads(f.read_text())
        first = meta['test_dates'][0]
        assert len(meta['discovery_dates']) == 80 and len(meta['validation_dates']) == 20
        assert meta['discovery_dates'][-1] < meta['validation_dates'][0]
        assert meta['discovery_label_max'] <= meta['validation_dates'][0]
        assert meta['validation_dates'][-1] < first
        assert meta['validation_label_max'] <= first
        members = {}
        for phase, dates, cutoff in [
            ('discovery', meta['discovery_dates'], meta['validation_dates'][0]),
            ('validation', meta['validation_dates'], first),
            ('refit', meta['discovery_dates']+meta['validation_dates'], first),
        ]:
            rows = mature(panel, dates, cutoff)
            member = read(f.parent/f'{phase}_membership.csv.gz')
            assert member.sample_id.tolist() == rows.index.tolist()
            pd.testing.assert_frame_equal(member[KEY+['next_date']], rows[KEY+['next_date']].reset_index(drop=True))
            w = day_weights(rows)
            cw = w*np.where(net_values(rows)<-.02, 3., 1.)
            cw *= len(cw)/cw.sum()
            assert np.allclose(member.day_weight, w, rtol=0, atol=1e-14)
            assert np.allclose(member.critical_weight, cw, rtol=0, atol=1e-14)
            assert rows.next_date.max() <= cutoff
            members[phase] = rows
            if phase != 'validation': union.update(rows.index)
        assert not set(members['discovery'].index) & set(members['validation'].index)
        saved = read(f.parent/'predictions.csv.gz')
        assert saved.model.nunique() == 6
        assert not saved.duplicated(['model']+KEY).any()
        assert saved.groupby('model').size().eq(meta['test_rows']).all()
        mass = saved[[f'mass_{c}' for c in CLASSES]].to_numpy()
        assert np.isfinite(mass).all() and (mass >= 0).all()
        assert np.allclose(mass.sum(axis=1), 1, rtol=0, atol=1e-14)
        risk = read(f.parent/'risk_scores.csv.gz')
        assert len(risk) == meta['test_rows'] and not risk.duplicated(KEY).any()
        assert risk.p_critical.between(0, 1).all()
        val_risk = read(f.parent/'validation_risk.csv.gz')
        pd.testing.assert_frame_equal(val_risk[KEY], members['validation'][KEY].reset_index(drop=True))
        assert len(meta['models']) == 10
        for entry in meta['models']:
            rows = members[entry['phase']]
            cutoff = meta['validation_dates'][0] if entry['phase']=='discovery' else first
            assert entry['last'] < cutoff and entry['label_max'] <= cutoff
            assert entry['rows'] == len(rows) and entry['features'] == list(FACTORS)
            assert sha(Path(entry['file'])) == entry['sha256']
            fitted = joblib.load(entry['file'])
            assert fitted['kind'] == entry['kind'] and fitted['C'] == .01
            n_estimators += len(fitted['estimators'])
            n_models += 1
        n_candidates += len(saved)
        n_risk += len(risk)

    # Independent reload: every October candidate, five fitted model kinds.
    folder = OUT/'folds'/'2026-10'
    saved = read(folder/'predictions.csv.gz').set_index(['model']+KEY)
    risk = read(folder/'risk_scores.csv.gz').set_index(KEY)
    test = panel[panel.signal_date.str.startswith('2026-10')]
    reproduced = []
    for kind in ('risk_binary','nominal','ordinal','ordinal_critical','ordinal_critical_fuzzy'):
        fitted = joblib.load(folder/f'{kind}_refit.joblib')
        actual = predict(fitted, test)
        keys = pd.MultiIndex.from_frame(test[KEY])
        if kind == 'risk_binary':
            expected = risk.reindex(keys).p_critical.to_numpy()
        else:
            name = 'net5_'+kind
            expected = saved.loc[name].reindex(keys)[[f'mass_{c}' for c in CLASSES]].to_numpy()
            rules = ('nominal','cost_decision','cost_matrix') if kind=='nominal' else (kind,)
            for rule in rules:
                score = decision_scores(actual, rule, test.index).score.to_numpy()
                recorded = saved.loc['net5_'+rule].reindex(keys).score.to_numpy()
                assert np.allclose(score, recorded, rtol=0, atol=1e-14)
        assert np.allclose(actual, expected, rtol=0, atol=1e-14)
        reproduced.append({'kind':kind,'rows':len(test),'max_probability_error':float(np.abs(actual-expected).max())})

    # Original decisions and uncertain gates must remain unchanged.
    old = read(CO5/'daily_picks_with_gate.csv')
    old = old[old.model.eq('B1_frozen_original171')].sort_values('signal_date').reset_index(drop=True)
    daily = read(OUT/'daily_execution.csv.gz')
    b = daily[daily.model.eq('B1_frozen_original171')].sort_values('signal_date').reset_index(drop=True)
    pd.testing.assert_frame_equal(b[KEY+['amount_gate','net_return','selected']],old[KEY+['amount_gate','net_return','selected']])
    assert len(b)==118 and b.selected.sum()==115 and b.trade.sum()==91
    assert b.loc[b.unknown_gate,'daily_net'].isna().all() and b.unknown_gate.sum()==4
    for name, g in daily[daily.model.str.contains('_risk10|_risk20')].groupby('model'):
        base_name = g.original_model.iloc[0]
        base = daily[daily.model.eq(base_name)].set_index('signal_date')
        g = g.set_index('signal_date').reindex(base.index)
        assert g.symbol6.fillna('CASH').equals(base.symbol6.fillna('CASH'))
        assert not (g.trade & ~base.trade).any()
        assert np.allclose(g.raw_amount_gate,base.amount_gate,rtol=0,atol=0,equal_nan=True)
        threshold = .10 if name.endswith('10') else .20
        assert g.loc[g.trade,'p_critical'].le(threshold).all()
        assert g.loc[g.unknown_gate,'daily_net'].isna().all()
    summary = read(REPORT/'summary.csv')
    empty = summary.trades.eq(0)
    assert summary.loc[empty,['net_trade_mean_pct','critical_pct','strong_pct']].isna().all().all()
    result = {
        'folds':len(folds),'saved_model_bundles_checked':n_models,'estimators_checked':n_estimators,
        'memberships_checked':len(folds)*3,'unique_fitting_rows':len(union),
        'candidate_prediction_rows':n_candidates,'candidate_risk_rows':n_risk,
        'representative_prediction_reproduction':reproduced,
        'B1_frozen_days':len(b),'B1_selected':int(b.selected.sum()),
        'B1_trades':int(b.trade.sum()),'B1_unknown_gates':int(b.unknown_gate.sum()),
        'python':platform.python_version(),
        'dependencies':{n:version(n) for n in ('numpy','pandas','scikit-learn','scipy','joblib','threadpoolctl')},
        'training_script_matches_manifest':sha(Path('scripts/experiment_automl_close_open_critical_risk.py'))==json.loads((OUT/'run_manifest.json').read_text())['script_sha256'],
    }
    assert result['training_script_matches_manifest']
    (REPORT/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    with threadpool_limits(limits=1):run()
