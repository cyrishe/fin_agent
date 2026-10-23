"""Point-in-time RF score calibration diagnostics; no new fit."""
from __future__ import annotations

from pathlib import Path
import json
import pandas as pd
import joblib
from sklearn.metrics import brier_score_loss

ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT=ROOT/'audit_r91'
p=pd.read_pickle(ROOT/'panel.pkl')
parts=[]
for month in pd.period_range('2018-01','2021-12',freq='M'):
    name=str(month)
    s=pd.read_pickle(ROOT/'daily2'/name/'segments_forecast_scores.pkl')
    parts.append(s[['symbol','d0_date','score']])
score=pd.concat(parts,ignore_index=True)
frame=p[['symbol','d0_date','label_3','activated','observed_3']].merge(score,on=['symbol','d0_date'],validate='one_to_one')
frame=frame[frame.activated&frame.observed_3].copy()
frame['year']=frame.d0_date.str[:4]
summary=[]
for year,g in frame.groupby('year'):
    summary.append(dict(year=year,scope='target_month',n=len(g),realized=g.label_3.mean(),
                        mean_score=g.score.mean(),score_minus_realized=g.score.mean()-g.label_3.mean(),
                        brier=brier_score_loss(g.label_3,g.score)))
    g=g.copy();g['rank_bin']=pd.qcut(g.score,5,labels=False,duplicates='drop')
    for b,h in g.groupby('rank_bin'):
        summary.append(dict(year=year,scope=f'score_quintile_{b+1}',n=len(h),
                            realized=h.label_3.mean(),mean_score=h.score.mean(),
                            score_minus_realized=h.score.mean()-h.label_3.mean(),
                            brier=brier_score_loss(h.label_3,h.score)))
pd.DataFrame(summary).to_csv(OUT/'score_calibration.csv',index=False)

# Current released October 2026 model on its held-out August/September validation.
late=pd.read_pickle(OUT/'later_panel.pkl')
from scripts.experiment_limit_rebound_monthly_r14 import block
late_calendar=json.loads((OUT/'later_calendar.json').read_text())
held=block(late,late_calendar,'2026-08','2026-09',purge_events=False)
val=held[held.activated & held.observed_3]
bundle=joblib.load('/data/cyris/fin_agent_deploy/b2-20261015/assets/months/2026-10/model.joblib')
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
z=bundle['model'].predict_proba(xmatrix(val,bundle['features']))[:,1]
print('current 2026-10 validation',len(val),'realized',val.label_3.mean(),'mean score',z.mean(),
      'brier',brier_score_loss(val.label_3,z))
print(pd.DataFrame(summary).query("scope=='target_month'").to_string(index=False))
