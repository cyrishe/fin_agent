"""Reconcile frozen search, sampled keys, chronological cutoffs and saved forecasts."""
from __future__ import annotations
import json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from scripts.experiment_limit_rebound_search_r16 import (
 OUT,SOURCE,PREV,MONTHS,TEST_MONTHS,ALL_GROUPS,window,predict,sample_train,scored,rank,classify,
)
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha,xmatrix
from scripts.report_limit_rebound_search_r16 import configure,DOC
from scripts.report_limit_rebound_two_stage_r15 import pick

def run():
 configure();panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
 indexed=panel.set_index(['symbol','d0_date'],drop=False)
 cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
 count=0;bundles=0;sampled=0
 for month in MONTHS:
  w=window(month);tr=block(panel,cal,*w['train']);fit=tr[tr.activated&tr.observed_3].sort_values(['d0_date','symbol'])
  cutoff=(pd.Period(w['train'][1],freq='M')+1).start_time.strftime('%Y-%m-%d');end_i=np.searchsorted(cal,cutoff)-1
  for g in ALL_GROUPS:
   gid=g['id'];bundle=joblib.load(OUT/month/f'{gid}.joblib')
   assert bundle['spec_sha256']==sha(OUT/'specification.json') and bundle['group']==g
   for sample in bundle['sampling']:
    keys=[(r['symbol'],r['d0_date']) for r in sample['training_keys']];actual=indexed.loc[keys]
    assert actual.date_i.add(4).le(end_i).all() and actual.d0_date.ge(w['train'][0]+'-01').all()
    assert not actual.duplicated(['symbol','d0_date']).any()
    if not bundle['reference']:
     expected,weight=sample_train(fit,g['scheme'],sample['seed'])
     assert keys==list(zip(expected.symbol,expected.d0_date))
     assert sample['sampled']==len(expected)
     for y in (0,1):assert np.isclose(sample['class_weights'][str(y)],weight[expected.label_3.eq(y)][0])
     sampled+=1
   s=pd.read_pickle(OUT/month/f'{gid}_forecast_scores.pkl');rows=indexed.loc[list(zip(s.symbol,s.d0_date))]
   assert np.array_equal(predict(bundle,rows),s.score.to_numpy())
   count+=len(bundle['models']);bundles+=1
  print('verified models',month,count,flush=True)
 for m in TEST_MONTHS:
  frozen=json.loads((OUT/m/'stage1_frozen.json').read_text());results=frozen['candidates'];base=results[0]
  eligible=[r for r in results if r['oof_quality']>=base['oof_quality'] and r['valid_quality']>=base['valid_quality']]
  expected=max(eligible,key=lambda r:r['selection_score'])
  assert expected['family']==frozen['family'] and frozen['test_results_seen'] is False
  for c in results:
   expected=.5*(c['oof_quality']+c['valid_quality'])-.25*abs(c['oof_quality']-c['valid_quality'])-.25*c['monthly_std']-.25*c['seed_gap']
   assert np.isclose(expected,c['selection_score'])
 test=pd.read_pickle(OUT/'test_top5_scored.pkl');assert not test.duplicated(['symbol','d0_date']).any()
 assert test.groupby('d0_date').size().le(5).all()
 for m in TEST_MONTHS:
  rule=json.loads((OUT/m/'risk_frozen.json').read_text())['chosen']
  if rule['name']=='none':continue
  bundle=joblib.load(OUT/m/f'risk_{rule["name"]}.joblib');f=test[test.evaluation_month.eq(m)]
  assert np.array_equal(bundle['model'].predict_proba(xmatrix(f,bundle['features']))[:,1],f.risk_score.to_numpy())
 valid=test[test.clean_return&test.fill_proxy.eq(True)]
 expected=~valid[['d2_up','d3_up','d4_up']].eq(True).any(axis=1)&valid.d4_close.lt(valid.buy_price*.95-1e-9)
 assert expected.equals(valid.bad)
 selected=pd.read_pickle(OUT/'selected_trades.pkl');sp=selected[selected.part.eq('forecast')]
 for name,flag in [('过滤前',False),('过滤后',True)]:
  a=pick(test,flag);b=sp[sp.policy.eq(name)]
  assert set(zip(a.symbol,a.d0_date))==set(zip(b.symbol,b.d0_date))
 for k in (2,3,4):assert np.allclose(valid[f'ret{k}'],valid[f'd{k}_close']/valid.buy_price-1)
 stats=pd.read_csv(OUT/'account_results.csv').query('集合 == "测试" and 范围 == "测试全期"')
 for r in stats.to_dict('records'):
  f=sp[sp.policy.eq(r['方案'])];col={'D2收盘':'ret2','D3收盘':'ret3','D4收盘':'ret4','策略卖出':'strategy_return'}[r['卖出']]
  assert len(f)==r['笔数'] and np.isclose(f[col].mean(),r['每笔均涨'])
 # First-stage reference must be identical to the previous frozen experiment.
 for m in TEST_MONTHS:
  a=pd.read_pickle(OUT/m/'rf_reference_forecast_scores.pkl').set_index(['symbol','d0_date']).score.sort_index()
  b=pd.read_pickle(PREV/m/'rf_forecast_scores.pkl').set_index(['symbol','d0_date']).score.sort_index()
  assert a.equals(b)
 # Independently cross-check new selected minute prices against archived daily OHLC.
 entries=sp[['symbol','d1_date','buy_price','d1_open','d1_close']].drop_duplicates(['symbol','d1_date'])
 keys=set(zip(entries.symbol,entries.d1_date));quotes=[];sources=[]
 for p in sorted(Path('outputs/stock_automl/limit_rebound_2y_r1/price_months').glob('price_*.csv.gz'))+[SOURCE/'price_months/price_2026-10-14_extension.csv.gz']:
  if p.name[6:13] not in set(entries.d1_date.str[:7]):continue
  raw=pd.read_csv(p,usecols=['trade_date','stk_code','open','high','low','close'],dtype={'stk_code':str,'trade_date':str})
  raw['trade_date']=pd.to_datetime(raw.trade_date).dt.strftime('%Y-%m-%d')
  raw['symbol']=raw.stk_code.str[:6]
  keep=[k in keys for k in zip(raw.symbol,raw.trade_date)]
  quotes.append(raw[keep]);sources.append({'path':str(p),'sha256':sha(p)})
 daily=pd.concat(quotes).drop_duplicates(['symbol','trade_date'],keep='last')
 q=entries.merge(daily,left_on=['symbol','d1_date'],right_on=['symbol','trade_date'],how='left',validate='one_to_one')
 assert q.low.notna().all() and q.buy_price.ge(q.low-.005).all() and q.buy_price.le(q.high+.005).all()
 assert np.allclose(q.d1_open,q.open,atol=.005,rtol=0) and np.allclose(q.d1_close,q.close,atol=.005,rtol=0)
 q.to_pickle(OUT/'price_check_inputs.pkl')
 pricecheck={'unique_selected_stock_days':len(q),'all_entry_prices_inside_daily_range':True,'daily_open_close_match':True,'sources':sources}
 dump(OUT/'price_crosscheck.json',pricecheck);dump(DOC/'price_crosscheck.json',pricecheck)
 v={'first_stage_bundles':bundles,'first_stage_models':count,'sample_draws_verified':sampled,
  'all_forecast_probabilities_exact':True,'reference_matches_R15':True,'time_cutoffs_verified':True,
  'all_choices_reconstructed_without_test':True,'second_stage_reload_exact':True,'labels_and_returns_reconciled':True,
  'independent_daily_price_checks':len(q),
  'test_top5_rows':len(test),'test_days':int(test.d0_date.nunique()),'selected_trades':sp.policy.value_counts().to_dict(),
  'manifest_sha256':sha(OUT/'model_manifest.json')}
 dump(OUT/'verification.json',v);dump(DOC/'verification.json',v);print(v)

if __name__=='__main__':run()
