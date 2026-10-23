"""Score frozen 2025-2026 monthly release models on their target months."""
from __future__ import annotations

from pathlib import Path
import joblib
import pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix

ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT=ROOT/'audit_r91'
ASSETS=Path('/data/cyris/fin_agent_deploy/b2-20261015/assets/months')
panel=pd.read_pickle(OUT/'later_panel.pkl')
records=[]
for month in pd.period_range('2025-01','2026-09',freq='M'):
    name=str(month)
    f=panel[panel.d0_date.str.startswith(name)].copy()
    bundle=joblib.load(ASSETS/name/'model.joblib')
    assert len(bundle['features'])==51 and bundle['month']==name
    f['score']=bundle['model'].predict_proba(xmatrix(f,bundle['features']))[:,1]
    f=f.sort_values(['d0_date','score','symbol'],ascending=[True,False,True])
    f['d0_rank']=f.groupby('d0_date').cumcount()+1
    q=f[f.activated&f.observed_3]
    t=q[q.d0_rank.le(3)]
    records.append(dict(month=name,year=name[:4],open_n=len(q),base_rate=q.label_3.mean(),
                        ap=average_precision_score(q.label_3,q.score) if q.label_3.nunique()==2 else None,
                        auc=roc_auc_score(q.label_3,q.score) if q.label_3.nunique()==2 else None,
                        top3_n=len(t),top3_hits=int(t.label_3.sum())))
monthly=pd.DataFrame(records)
monthly.to_csv(OUT/'later_model_months.csv',index=False)
years=[]
for year,g in monthly.groupby('year'):
    years.append(dict(year=year,months=len(g),open_n=int(g.open_n.sum()),
                      base_rate=(g.base_rate*g.open_n).sum()/g.open_n.sum(),
                      ap_month_mean=g.ap.mean(),auc_month_mean=g.auc.mean(),
                      top3_n=int(g.top3_n.sum()),top3_hits=int(g.top3_hits.sum()),
                      top3_hit=g.top3_hits.sum()/g.top3_n.sum()))
pd.DataFrame(years).to_csv(OUT/'later_model_years.csv',index=False)
print(pd.DataFrame(years).to_string(index=False))
