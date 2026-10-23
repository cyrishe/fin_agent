"""Same 9-factor ablation on six already observed 2026 target months."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import joblib
import pandas as pd
from sklearn.metrics import average_precision_score

from scripts.experiment_limit_rebound_short_mixed_r12 import make_model, xmatrix

ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT=ROOT/'audit_r91'/'absolute_ablation_late'
OUT.mkdir(parents=True,exist_ok=True)
ASSETS=Path('/data/cyris/fin_agent_deploy/b2-20261015/assets/months')
panel=pd.read_pickle(ROOT/'audit_r91/later_panel.pkl')
DROP={'d0_amount_100m','d0_turnover_pct','first_turnover_pct','normal_turnover5',
      'board_turnover_sum','log_float_mv_proxy','log_price',
      'market_limit_count','market_prev_limit_count'}


def run(month):
    folder=ASSETS/month
    bundle=joblib.load(folder/'model.joblib')
    fit=pd.read_pickle(folder/'training.pkl')
    cols=[c for c in bundle['features'] if c not in DROP]
    assert len(cols)==42 and len(fit)==len(bundle['training_keys'])
    model=make_model('rf')
    model.steps[-1][1].n_jobs=1
    model.fit(xmatrix(fit,cols),fit.label_3,
              randomforestclassifier__sample_weight=fit.sample_weight)
    dest=OUT/month;dest.mkdir(exist_ok=True)
    joblib.dump(dict(month=month,features=cols,drop=sorted(DROP),model=model,
                     source_training=str(folder/'training.pkl')),
                dest/'no_absolute.joblib',compress=3)
    p=panel[panel.d0_date.str.startswith(month)].copy()
    p['old_score']=bundle['model'].predict_proba(xmatrix(p,bundle['features']))[:,1]
    p['new_score']=model.predict_proba(xmatrix(p,cols))[:,1]
    for label in ('old','new'):
        p=p.sort_values(['d0_date',label+'_score','symbol'],ascending=[True,False,True])
        p[label+'_rank']=p.groupby('d0_date').cumcount()+1
    q=p[p.activated&p.observed_3]
    result=dict(month=month,n=len(q),base_rate=q.label_3.mean())
    for label in ('old','new'):
        result[label+'_ap']=average_precision_score(q.label_3,q[label+'_score'])
        for k in (1,3):
            z=q[q[label+'_rank'].le(k)]
            result[f'{label}_top{k}_n']=len(z)
            result[f'{label}_top{k}_hits']=int(z.label_3.sum())
    a=set(zip(p.loc[p.old_rank.le(3),'symbol'],p.loc[p.old_rank.le(3),'d0_date']))
    b=set(zip(p.loc[p.new_rank.le(3),'symbol'],p.loc[p.new_rank.le(3),'d0_date']))
    result['top3_overlap']=len(a&b)/len(a)
    return result

months=[f'2026-{m:02}' for m in range(4,10)]
with ThreadPoolExecutor(max_workers=2) as pool:
    results=[f.result() for f in as_completed([pool.submit(run,m) for m in months])]
out=pd.DataFrame(results).sort_values('month')
out.to_csv(OUT/'monthly.csv',index=False)
print(out.to_string(index=False))
