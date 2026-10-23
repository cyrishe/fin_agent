"""Read-only audit of R90 model chronology, drift, and source units.

Run on the research server with the R90 venv. No model fit or database writes.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT = ROOT / 'audit_r91'
OUT.mkdir(exist_ok=True)
panel = pd.read_pickle(ROOT / 'panel.pkl')
panel['year'] = panel.d0_date.str[:4]
features = json.loads((ROOT / 'specification.json').read_text())
assert features['daily_training_months'] == 6 and features['daily_validation_months'] == 2

absolute = ['d0_amount_100m', 'd0_turnover_pct', 'first_turnover_pct',
            'normal_turnover5', 'board_turnover_sum', 'log_float_mv_proxy',
            'log_price', 'market_limit_count', 'market_prev_limit_count']
drift = []
for year, g in panel.groupby('year'):
    for col in absolute:
        x = pd.to_numeric(g[col], errors='coerce').replace([np.inf, -np.inf], np.nan).dropna()
        drift.append(dict(year=year, feature=col, n=len(x), missing_pct=100*(1-len(x)/len(g)),
                          p10=float(x.quantile(.1)) if len(x) else None,
                          median=float(x.median()) if len(x) else None,
                          p90=float(x.quantile(.9)) if len(x) else None))
pd.DataFrame(drift).to_csv(OUT / 'feature_drift.csv', index=False)

# Source unit diagnostic: turnover amount should be roughly price x share volume.
unit = []
for year, g in panel.groupby('year'):
    ratio = (g.amount / (g.close * g.volume)).replace([np.inf, -np.inf], np.nan).dropna()
    unit.append(dict(year=year, n=len(ratio), median=float(ratio.median()),
                     p01=float(ratio.quantile(.01)), p99=float(ratio.quantile(.99)),
                     outside_0p5_2=int(((ratio < .5) | (ratio > 2)).sum())))
pd.DataFrame(unit).to_csv(OUT / 'source_units.csv', index=False)

month_rows = []
imps = []
for month_dir in sorted((ROOT / 'daily2').iterdir()):
    if not month_dir.is_dir() or not (month_dir / 'metrics.json').exists():
        continue
    month = month_dir.name
    m = json.loads((month_dir / 'metrics.json').read_text())
    score_path = month_dir / 'segments_forecast_scores.pkl'
    scored = pd.read_pickle(score_path).merge(panel[['symbol', 'd0_date', 'activated', 'observed_3', 'label_3']],
                                               on=['symbol', 'd0_date'], validate='one_to_one')
    for phase in ['train', 'valid', 'forecast']:
        info = m['metrics'][phase]['open']
        row = dict(month=month, year=month[:4], phase=phase, fit_rows=m['fit_rows'],
                   open_n=info['known'], base_rate=info['rate'], ap=info['ap'])
        for k in [1, 3, 5]:
            row[f'top{k}_n'] = info[f'top{k}']['n']
            row[f'top{k}_hits'] = info[f'top{k}']['hits']
        if phase == 'forecast':
            f = scored[scored.activated & scored.observed_3]
            row['auc'] = (float(roc_auc_score(f.label_3, f.score)) if f.label_3.nunique() == 2 else np.nan)
            row['ap_independent'] = (float(average_precision_score(f.label_3, f.score)) if f.label_3.nunique() == 2 else np.nan)
            row['mean_score'] = float(f.score.mean()) if len(f) else np.nan
            row['score_p90'] = float(f.score.quantile(.9)) if len(f) else np.nan
            row['label_rate_independent'] = float(f.label_3.mean()) if len(f) else np.nan
        month_rows.append(row)
    bundle = joblib.load(month_dir / 'segments.joblib')
    assert bundle['month'] == month and bundle['window'] == m['window']
    assert len(bundle['training_keys']) == m['fit_rows']
    assert max(pd.Period(k['d0_date'][:7]) for k in bundle['training_keys']) < pd.Period(month)
    model = bundle['model']
    tree = model.steps[-1][1]
    names = model.steps[0][1].get_feature_names_out(bundle['features'])
    for name, importance in zip(names, tree.feature_importances_):
        imps.append(dict(month=month, year=month[:4], feature=name, importance=float(importance)))

pd.DataFrame(month_rows).to_csv(OUT / 'model_months.csv', index=False)
pd.DataFrame(imps).to_csv(OUT / 'feature_importance.csv', index=False)
summary = []
for (year, phase), g in pd.DataFrame(month_rows).groupby(['year', 'phase']):
    if year not in ['2017', '2018', '2019', '2020', '2021']:
        continue
    summary.append(dict(year=year, phase=phase, months=len(g), open_n=int(g.open_n.sum()),
                        base_rate=float(np.average(g.base_rate.dropna(), weights=g.loc[g.base_rate.notna(), 'open_n'])) if g.base_rate.notna().any() else np.nan,
                        ap_month_mean=float(g.ap.mean()), auc_month_mean=float(g.auc.mean()),
                        top1_n=int(g.top1_n.sum()), top1_hits=int(g.top1_hits.sum()),
                        top1_hit=float(g.top1_hits.sum()/g.top1_n.sum()) if g.top1_n.sum() else np.nan,
                        top3_n=int(g.top3_n.sum()), top3_hits=int(g.top3_hits.sum()),
                        top3_hit=float(g.top3_hits.sum()/g.top3_n.sum()) if g.top3_n.sum() else np.nan))
pd.DataFrame(summary).to_csv(OUT / 'model_years.csv', index=False)
print('model_months', len(month_rows), 'model_files', len(imps) // len(names))
print(pd.DataFrame(summary).to_string(index=False))
print('units')
print(pd.DataFrame(unit).to_string(index=False))
