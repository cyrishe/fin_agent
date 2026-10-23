from __future__ import annotations
import json,hashlib,platform
from pathlib import Path
import joblib,numpy as np,pandas as pd,sklearn
from scripts.automl_b2 import evaluate_morning
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.experiment_limit_rebound_short_mixed_r12 import xmatrix
from scripts.experiment_limit_rebound_two_stage_r15 import rank
from scripts.replay_limit_rebound_cash_r23 import dump,sha
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
panel=pd.read_pickle(ROOT/'panel.pkl')
months=[str(m) for m in pd.period_range('2016-09','2021-12',freq='M')]
checks=[]
for month in months:
 root=ROOT/'daily2'/month
 bundle=joblib.load(root/'segments.joblib')
 f=panel[panel.d0_date.str.startswith(month)]
 s=rank(f,bundle['model'].predict_proba(xmatrix(f,FEATURES['segments']))[:,1])
 saved=pd.read_pickle(root/'segments_forecast_scores.pkl')
 join=s[['symbol','d0_date','score','d0_rank']].merge(saved,on=['symbol','d0_date'],validate='one_to_one',suffixes=('_new','_old'))
 diff=float((join.score_new-join.score_old).abs().max()) if len(join) else 0.
 assert diff<1e-12 and join.d0_rank_new.eq(join.d0_rank_old).all(),month
 fit=pd.read_pickle(root/'training_samples.pkl')
 assert fit[['symbol','d0_date','first_limit_date']].to_dict('records')==bundle['training_keys']
 assert fit.activated.all() and fit.observed_3.all()
 assert fit.d0_date.max()<str(pd.Period(month)-2)+'-01',(month,fit.d0_date.max())
 checks.append(dict(month=month,forecast_rows=len(join),training_rows=len(fit),max_score_diff=diff,model_sha256=sha(root/'segments.joblib')))
print('model reload',len(checks),flush=True)
f=pd.read_pickle(ROOT/'enriched.pkl')
first=pd.read_pickle(ROOT/'first10.pkl');first['symbol']=first.symbol.str[:6]
bars={(s,d):g.sort_values('time') for (s,d),g in first.groupby(['symbol','d0_date'])}
rules={x['month']:x['selected'] for x in json.loads((ROOT/'rules_frozen.json').read_text())}
signals=pd.read_pickle(ROOT/'signals.pkl')
selected=[];decisions=[]
for day,g in f[f.part.eq('test')].groupby('d1_date'):
 d0=g.d0_date.iloc[0]
 picks=[dict(symbol6=r.symbol,selection_rank=int(r.d0_rank),preclose=float(r.d1_preclose),cap=float(r.d1_cap),ordinary=bool(r.eligible)) for r in g.itertuples()]
 sources={r.symbol:bars.get((r.symbol,d0),pd.DataFrame()) for r in g.itertuples()}
 states=evaluate_morning(picks,sources,day,pd.Timestamp(day+' 09:40:00').to_pydatetime(),rules[d0[:7]])
 for state in states:
  decisions.append(dict(d0_date=d0,d1_date=day,**state))
  if state['actionable']:
   selected.append((d0,state['symbol6'],state['entry_bar_end'][11:16],float(state['entry_price'])))
expected=[(r.d0_date,r.symbol,r.entry_time,float(r.entry)) for r in signals.itertuples()]
assert sorted(selected)==sorted(expected),(len(selected),len(expected),selected[:3],expected[:3])
pd.DataFrame(decisions).to_pickle(ROOT/'execution_decisions.pkl')
files=['run.py','replay.py','risk.py','prepare_prices.py','fetch_daily.py','fetch_db.py','specification.json','risk_spec.json','daily_sources.json','minute_calendar_coverage.json','panel_manifest.json','panel.pkl','calendar.json','rules_frozen.json','rule_candidates.json','signals.pkl','d2_minute_sources.json','d2_minute_failures.json','d2_exit_facts.pkl','signals_exit.pkl','overall.json','monthly_model.csv','monthly_calendar.csv','yearly_calendar.csv','trade_decisions.csv','risk_audit.json','risk_model.joblib','risk_split_effect.csv','risk_account_comparison.csv','risk_trade_predictions.csv','analyze_primary.py','primary_overall.json','primary_model_year.csv','primary_calendar_year.csv','primary_monthly_model.csv']
manifest={name:sha(ROOT/name) for name in files if (ROOT/name).exists()}
dump(ROOT/'audit.json',dict(model_reload_count=len(checks),model_max_score_diff=max(x['max_score_diff'] for x in checks),production_evaluator_same_signals=len(selected),candidate_decisions=len(decisions),months_with_rules=len(rules),files_sha256=manifest,python=platform.python_version(),sklearn=sklearn.__version__,research_only=True,production_changed=False))
dump(ROOT/'model_reload_checks.json',checks)
print('audit complete',len(selected),len(decisions),len(manifest),flush=True)
