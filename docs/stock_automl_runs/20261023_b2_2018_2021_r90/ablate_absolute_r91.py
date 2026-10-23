"""Controlled R90 RF51 ablation: remove nine absolute-scale inputs.

Same monthly fit samples, labels, weights and RF seed. Research only.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

ROOT = Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT = ROOT / 'audit_r91' / 'absolute_ablation'
OUT.mkdir(parents=True, exist_ok=True)
DROP = {'d0_amount_100m','d0_turnover_pct','first_turnover_pct','normal_turnover5',
        'board_turnover_sum','log_float_mv_proxy','log_price',
        'market_limit_count','market_prev_limit_count'}
panel = pd.read_pickle(ROOT/'panel.pkl')


def test_month(month):
    source = ROOT/'daily2'/month
    dest = OUT/month
    dest.mkdir(exist_ok=True)
    original = joblib.load(source/'segments.joblib')
    cols = [c for c in original['features'] if c not in DROP]
    assert len(original['features']) == 51 and len(cols) == 42
    fit = pd.read_pickle(source/'training_samples.pkl')
    sample = panel[panel.d0_date.str.startswith(month)].copy()
    model = make_model('rf')
    model.steps[-1][1].n_jobs = 1
    model.fit(xmatrix(fit,cols),fit.label_3,
              randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
    joblib.dump(dict(month=month, features=cols, drop=sorted(DROP), model=model,
                     source_training_samples=str(source/'training_samples.pkl'),
                     parent_model_sha256=__import__('hashlib').sha256((source/'segments.joblib').read_bytes()).hexdigest()),
                dest/'no_absolute.joblib',compress=3)
    restored = joblib.load(dest/'no_absolute.joblib')['model']
    assert np.array_equal(model.predict_proba(xmatrix(fit.iloc[:30],cols)),
                          restored.predict_proba(xmatrix(fit.iloc[:30],cols)))
    no_score = restored.predict_proba(xmatrix(sample,cols))[:,1]
    old_score = pd.read_pickle(source/'segments_forecast_scores.pkl')
    sample['new_score'] = no_score
    sample = sample.merge(old_score[['symbol','d0_date','score']],on=['symbol','d0_date'],validate='one_to_one')
    sample = sample.sort_values(['d0_date','new_score','symbol'],ascending=[True,False,True])
    sample['new_rank'] = sample.groupby('d0_date').cumcount()+1
    known = sample[sample.activated & sample.observed_3]
    metric = json.loads((source/'metrics.json').read_text())['metrics']['forecast']['open']
    result = dict(month=month,year=month[:4],open_n=len(known),base_rate=float(known.label_3.mean()),
                  old_ap=metric['ap'],new_ap=float(average_precision_score(known.label_3,known.new_score)) if known.label_3.nunique()==2 else None,
                  old_top1_n=metric['top1']['n'],old_top1_hits=metric['top1']['hits'],
                  old_top3_n=metric['top3']['n'],old_top3_hits=metric['top3']['hits'])
    for k in (1,3):
        q=known[known.new_rank.le(k)]
        result[f'new_top{k}_n']=len(q)
        result[f'new_top{k}_hits']=int(q.label_3.sum())
    # Rank overlap is measured only within the original all-candidate D0 pool.
    old = pd.read_pickle(source/'segments_forecast_scores.pkl')
    new = sample[['symbol','d0_date','new_rank']].merge(old[['symbol','d0_date','d0_rank']],
                                                       on=['symbol','d0_date'],validate='one_to_one')
    old_top=set(zip(new.loc[new.d0_rank.le(3),'symbol'],new.loc[new.d0_rank.le(3),'d0_date']))
    new_top=set(zip(new.loc[new.new_rank.le(3),'symbol'],new.loc[new.new_rank.le(3),'d0_date']))
    result['top3_overlap']=len(old_top&new_top)/len(old_top) if old_top else None
    return result


months=[str(m) for m in pd.period_range('2018-01','2021-12',freq='M')]
with ThreadPoolExecutor(max_workers=3) as pool:
    futures=[pool.submit(test_month,m) for m in months]
    results=[f.result() for f in as_completed(futures)]
result=pd.DataFrame(results).sort_values('month')
result.to_csv(OUT/'monthly.csv',index=False)
summary=[]
for year,g in result.groupby('year'):
    summary.append(dict(year=year,months=len(g),mean_old_ap=g.old_ap.mean(),mean_new_ap=g.new_ap.mean(),
                        old_top3_n=int(g.old_top3_n.sum()),old_top3_hits=int(g.old_top3_hits.sum()),
                        new_top3_n=int(g.new_top3_n.sum()),new_top3_hits=int(g.new_top3_hits.sum()),
                        mean_top3_overlap=float(g.top3_overlap.mean())))
pd.DataFrame(summary).to_csv(OUT/'yearly.csv',index=False)
print(pd.DataFrame(summary).to_string(index=False))
