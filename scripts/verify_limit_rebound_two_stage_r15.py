"""Independent time, ranking, label, model-reload and result reconciliations."""
import json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from scripts.experiment_limit_rebound_two_stage_r15 import OUT,SOURCE,MONTHS,TEST_MONTHS,window,xmatrix,X2
from scripts.experiment_limit_rebound_short_mixed_r12 import sha,dump
from scripts.report_limit_rebound_two_stage_r15 import pick

def run():
 panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl').set_index(['symbol','d0_date'],drop=False)
 cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
 models=0
 for m in MONTHS:
  cutoff=(pd.Period(window(m)['train'][1],freq='M')+1).start_time.strftime('%Y-%m-%d')
  for family in ['rf','rf_shallow','logistic']:
   root=OUT/m;bundle=joblib.load(root/f'{family}.joblib');keys=[(r['symbol'],r['d0_date']) for r in bundle['training_keys']]
   tr=panel.loc[keys];end_i=np.searchsorted(cal,cutoff)-1
   assert tr.date_i.add(4).le(end_i).all()
   scored=pd.read_pickle(root/f'{family}_forecast_scores.pkl');rows=panel.loc[list(zip(scored.symbol,scored.d0_date))]
   pred=bundle['model'].predict_proba(xmatrix(rows,bundle['features']))[:,1]
   assert np.array_equal(pred,scored.score.to_numpy())
   models+=1
 test=pd.read_pickle(OUT/'test_top5_scored.pkl')
 assert not test.duplicated(['symbol','d0_date']).any()
 assert test.groupby('d0_date').size().le(5).all()
 for m in TEST_MONTHS:
  frozen=json.loads((OUT/m/'risk_frozen.json').read_text());rule=frozen['chosen']
  if rule['name']=='none':continue
  bundle=joblib.load(OUT/m/f'risk_{rule["name"]}.joblib');keys=[(r['symbol'],r['d0_date']) for r in bundle['training_keys']]
  tr=panel.loc[keys];w=window(m)
  assert tr.d0_date.ge(w['train'][0]+'-01').all() and tr.d0_date.le(w['train'][1]+'-31').all()
  f=test[test.evaluation_month.eq(m)]
  assert np.array_equal(bundle['model'].predict_proba(xmatrix(f,X2))[:,1],f.risk_score.to_numpy())
 good=test[test.clean_return&test.fill_proxy.eq(True)]
 expected=~good[['d2_up','d3_up','d4_up']].eq(True).any(axis=1)&good.d4_close.lt(good.buy_price*.95-1e-9)
 assert expected.equals(good.bad)
 for k in (2,3,4):assert np.allclose(good[f'ret{k}'],good[f'd{k}_close']/good.buy_price-1)
 selected=pd.read_pickle(OUT/'selected_trades.pkl');p=selected[selected.part.eq('forecast')]
 for policy,filtered in [('过滤前',False),('过滤后',True)]:
  a=pick(test,filtered);b=p[p.policy.eq(policy)]
  assert set(zip(a.d0_date,a.symbol))==set(zip(b.d0_date,b.symbol))
 results=pd.read_csv(OUT/'account_results.csv');testresults=results[results['集合'].eq('测试')&results['范围'].eq('测试全期')]
 for row in testresults.to_dict('records'):
  q=p[p.policy.eq(row['方案'])];col={'D2收盘':'ret2','D3收盘':'ret3','D4收盘':'ret4','策略卖出':'strategy_return'}[row['卖出']]
  assert len(q)==row['笔数'] and np.isclose(q[col].mean(),row['每笔均涨'])
 value={'first_models_reloaded':models,'test_top5_rows':len(test),'test_unique_days':int(test.d0_date.nunique()),
  'test_valid_returns':len(good),'test_trades_by_policy':p.policy.value_counts().to_dict(),
  'time_isolation':True,'probability_reproduction':True,'bad_label_reconciliation':True,'selection_reconciliation':True,
  'model_manifest_sha256':sha(OUT/'model_manifest.json')}
 dump(OUT/'verification.json',value);print(value)
if __name__=='__main__':run()
