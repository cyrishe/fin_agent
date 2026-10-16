"""R15: six train months, two validation months, monthly D0 rank + small risk filter.

Base-family causal monthly forecasts create out-of-fit second-stage training rows.
Never fit the risk filter on stage-one resubstitution Top5 selections.
"""
from __future__ import annotations
import argparse,json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import joblib,numpy as np,pandas as pd,sklearn
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier,export_text
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score,roc_auc_score
from scripts.experiment_limit_rebound_short_mixed_r12 import FEATURES,EXTRA,xmatrix,dump,sha,make_model
from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights

OUT=Path('outputs/stock_automl/limit_rebound_two_stage_r15_20261015')
SOURCE=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
MONTHS=[str(p) for p in pd.period_range('2025-01','2026-10',freq='M')]
TEST_MONTHS=MONTHS[8:]
FAMILIES=['rf','rf_shallow','logistic']
X1=list(FEATURES)+list(EXTRA)
X2=['board_streak_days','d0_turnover_pct','log_float_mv_proxy','market_median_return',
    'open_gap','early_return','early_pullback','early_volume_ratio']


def window(month):
 m=pd.Period(month,freq='M')
 return {'train':[str(m-8),str(m-3)],'valid':[str(m-2),str(m-1)],'test':month}


def spec():
 OUT.mkdir(parents=True,exist_ok=True)
 value={'name':'R15_6m_2m_monthly_two_stage','months':MONTHS,'test_months':TEST_MONTHS,
  'families':FAMILIES,'first_features':X1,'risk_features':X2,'sklearn':'1.7.1',
  'first_target':'same R14: D1-D3 any sealed close; fit historical non-ST open-below-cap rows, score all standard D0 sealed candidates',
  'first_selection':'mean Top3/Top5 open-activated hit rates, half causal rolling training OOF and half 2-month validation; subtract 0.25 times their absolute gap. RF reference retained unless challenger improves both segments.',
  'oof':'each historical month forecast by fixed-family model fitted on its own preceding 6 train months with 2-month gap; all labels and active events purged at fit cutoff; no in-fit Top5 trains risk model',
  'risk_candidates':'D0 Top5 first, D1 historical non-ST and 09:40 close below cap, valid first 10 bars; no D0 seal-time filter',
  'risk_models':['ridge_logistic_C0.1','tree_depth2_minleaf40'],
  'risk_thresholds':'OOF tail fractions 10%,20%,30%; freeze training quantile; validation chooses gain in bad precision and return with >=65% retention; no-filter fallback',
  'badcase':'D2-D4 no sealed close AND D4 close / D1 09:41 open - 1 < -0.05',
  'entry':'decision at end 09:40; execution proxy next 09:41 open below cap, positive volume. No later facts used by filter.',
  'exit':'D2/D3/D4 close and first sealed D2-D4 close else D4; gross and illustrative 20bp roundtrip net',
  'first_stage_refit_on_validation':False,'second_stage_refit_on_validation':False,
  'sample_scope':'daily snapshot through 2026-10-14 outcomes, no live production claim; previously researched history',
  'source_sha256':sha(SOURCE/'panel_replay_through_20261014.pkl')}
 p=OUT/'specification.json'
 if p.exists(): assert json.loads(p.read_text())==value
 else:dump(p,value)
 return value


def rank(frame,scores):
 f=frame.copy();f['score']=scores
 f=f.sort_values(['d0_date','score','symbol'],ascending=[True,False,True])
 f['d0_rank']=f.groupby('d0_date').cumcount()+1
 return f


def classify(f):
 result={}
 for scope,g in [('all',f),('open',f[f.activated])]:
  known=g[g.observed_3];ys=known.label_3
  result[scope]={'n':len(g),'known':len(known),'rate':float(ys.mean()) if len(ys) else None,
   'ap':float(average_precision_score(ys,known.score)) if len(ys) and ys.nunique()>1 else None}
  for k in (1,3,5):
   # Rank is fixed before the future activation check. No replacement outside D0 TopK.
   picked=known[known.d0_rank.le(k)]
   rates=known.groupby('d0_date').label_3.mean()
   expected=float(picked.d0_date.map(rates).mean()) if len(picked) else None
   result[scope][f'top{k}']={'n':len(picked),'hits':int(picked.label_3.sum()),
    'rate':float(picked.label_3.mean()) if len(picked) else None,'same_day_base':expected}
 return result


def fit_family(family,train,path):
 fit=train[train.activated&train.observed_3]
 if path.exists(): return joblib.load(path)
 model=(make_pipeline(SimpleImputer(strategy='median',add_indicator=True),
  RandomForestClassifier(n_estimators=240,max_depth=4,min_samples_leaf=100,max_features=.8,n_jobs=2,random_state=20261013))
  if family=='rf_shallow' else make_model(family))
 kw={model.steps[-1][0]+'__sample_weight':balanced_weights(fit.label_3)}
 model.fit(xmatrix(fit,X1),fit.label_3,**kw)
 if family.startswith('rf'):model.steps[-1][1].n_jobs=1
 before=model.predict_proba(xmatrix(fit.iloc[:40],X1))
 bundle={'model':model,'features':X1,'family':family,'sklearn':sklearn.__version__,
  'training_keys':fit[['symbol','d0_date','first_limit_date']].to_dict('records'),'spec_sha256':sha(OUT/'specification.json')}
 joblib.dump(bundle,path,compress=3);bundle=joblib.load(path)
 assert np.array_equal(before,bundle['model'].predict_proba(xmatrix(fit.iloc[:40],X1)))
 return bundle


def stage1(shard=0,shards=1):
 assert sklearn.__version__=='1.7.1';spec()
 panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
 cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
 for i,month in enumerate(MONTHS):
  if i%shards!=shard:continue
  w=window(month);root=OUT/month;root.mkdir(exist_ok=True)
  tr=block(panel,cal,*w['train']);va=block(panel,cal,*w['valid'],purge_events=False)
  te=panel[panel.d0_date.str.startswith(month)].copy()
  for family in FAMILIES:
   if (root/f'{family}_metrics.json').exists():continue
   bundle=fit_family(family,tr,root/f'{family}.joblib');stats={}
   for part,f in [('train',tr),('valid',va),('forecast',te)]:
    scored=rank(f,bundle['model'].predict_proba(xmatrix(f,X1))[:,1])
    scored[['symbol','d0_date','score','d0_rank']].to_pickle(root/f'{family}_{part}_scores.pkl')
    if part!='forecast':stats[part]=classify(scored)
   dump(root/f'{family}_metrics.json',{'window':w,**stats,'model_sha256':sha(root/f'{family}.joblib')})
   print('stage1',month,family,flush=True)


def score_frame(panel,month,family,part):
 scores=pd.read_pickle(OUT/month/f'{family}_{part}_scores.pkl')
 return scores.merge(panel,on=['symbol','d0_date'],validate='one_to_one')


def rank_quality(m):
 return np.mean([m['open'][f'top{k}']['rate'] or 0 for k in (3,5)])


def choose():
 panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
 cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
 demands=[]
 for month in TEST_MONTHS:
  w=window(month);results=[];oofs={}
  trainmonths=[str(p) for p in pd.period_range(*w['train'],freq='M')]
  allowed=block(panel,cal,*w['train'])[['symbol','d0_date']]
  for family in FAMILIES:
   oof=pd.concat([score_frame(panel,m,family,'forecast') for m in trainmonths],ignore_index=True)
   oof=oof.merge(allowed,on=['symbol','d0_date'],validate='one_to_one');oofs[family]=oof
   metrics=json.loads((OUT/month/f'{family}_metrics.json').read_text())
   a=classify(oof);b=metrics['valid'];qa,qb=rank_quality(a),rank_quality(b)
   results.append({'family':family,'train_infit':metrics['train'],'train_oof':a,'valid':b,
    'selection_score':float((qa+qb)/2-.25*abs(qa-qb)),'oof_quality':qa,'valid_quality':qb})
  base=results[0];eligible=[r for r in results if r['oof_quality']>=base['oof_quality'] and r['valid_quality']>=base['valid_quality']]
  chosen=max(eligible,key=lambda r:r['selection_score']);family=chosen['family']
  dump(OUT/month/'stage1_frozen.json',{'month':month,'window':w,'family':family,'candidates':results,
   'spec_sha256':sha(OUT/'specification.json')})
  for part in ('train_oof','valid','forecast'):
   f=oofs[family] if part=='train_oof' else score_frame(panel,month,family,part)
   top=f[f.d0_rank.le(5)].copy();top['evaluation_month']=month;top['part']=part;top['family']=family
   top.to_pickle(OUT/month/f'chosen_{part}_top5.pkl');demands.append(top)
  print('selected',month,family,flush=True)
 demand=pd.concat(demands,ignore_index=True)
 demand.to_pickle(OUT/'all_top5.pkl')
 demand[['symbol','d1_date','d1_cap','d1_preclose','d1_open','d1_close','d1_volume']].dropna(subset=['d1_date']).drop_duplicates(['symbol','d1_date']).to_pickle(OUT/'minute_demand.pkl')


def early_facts(bars,r):
 bars=bars.sort_values('time').copy();clock=pd.to_datetime(bars.time).dt.strftime('%H:%M')
 early=bars[clock.between('09:31','09:40')].copy()
 expected=[f'09:{i:02}' for i in range(31,41)]
 if pd.to_datetime(early.time).dt.strftime('%H:%M').tolist()!=expected:return None
 cols=['open','high','low','close','volume']
 v=early[cols].apply(pd.to_numeric,errors='coerce')
 if not np.isfinite(v.to_numpy()).all() or v[cols[:4]].le(0).any().any() or v.volume.lt(0).any():return None
 if (v.high.lt(v[['open','low','close']].max(axis=1)-.005).any()
     or v.low.gt(v[['open','high','close']].min(axis=1)+.005).any()):return None
 if not np.isclose(v.open.iloc[0],r.d1_open,atol=.005,rtol=0):return None
 close=float(v.close.iloc[-1]);high=float(v.high.max());low=float(v.low.min())
 nextbar=bars[clock.eq('09:41')]
 buy=float(nextbar.open.iloc[0]) if len(nextbar)==1 else np.nan
 canbuy=bool(len(nextbar)==1 and float(nextbar.volume.iloc[0])>0 and 0<buy<r.d1_cap-.005)
 full=bars[cols].apply(pd.to_numeric,errors='coerce')
 audit=bool(len(bars)==240 and clock.nunique()==240 and np.isclose(float(full.close.iloc[-1]),r.d1_close,atol=.005,rtol=0) and np.isclose(float(full.volume.sum()),r.d1_volume,atol=1,rtol=.001))
 return {'early_valid':True,'close0940':close,'open_gap':float(v.open.iloc[0]/r.d1_preclose-1),
  'early_return':float(close/v.open.iloc[0]-1),'early_pullback':float(close/high-1),
  'early_volume':float(v.volume.sum()),'early_range':float((high-low)/r.d1_preclose),
  'below0940':bool(close<r.d1_cap-.005),'buy_price':buy,'fill_proxy':canbuy,'full_day_audit':audit}


def bad_labels(frame):
 return (~frame[['d2_up','d3_up','d4_up']].eq(True).any(axis=1)
         & frame.d4_close.lt(frame.buy_price*.95-1e-9))


def scan_day(day,demand):
 from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
 from scripts.mk_minute_archive import day_file,COMPACT_ROOT,COMPACT_ROOT_2025
 from scripts.benchmark_automl_1440_inference import db_connection,query
 cache=OUT/'minute_days'/f'{day}.json';cache.parent.mkdir(exist_ok=True)
 if cache.exists():return json.loads(cache.read_text())
 try:bars=filtered_local(day_file(COMPACT_ROOT_2025 if day.startswith('2025') else COMPACT_ROOT,day),set(demand.symbol))
 except FileNotFoundError:bars=pd.DataFrame()
 rows={}
 def collect(bars,source):
  if bars.empty:return
  bars=bars.copy();bars['symbol']=bars.symbol.str[:6]
  for sym,g in bars.groupby('symbol'):
   match=demand[demand.symbol.eq(sym)]
   if match.empty:continue
   facts=early_facts(g,match.iloc[0])
   if facts:rows[sym]={'symbol':sym,'d1_date':day,'source':source,**facts}
 collect(bars,'local_unadjusted')
 missing=sorted(set(demand.symbol)-set(rows))
 if missing and day>='2026-01-01':
  with db_connection() as conn:
   marks=','.join(['%s']*len(missing))
   bars=query(conn,'SELECT stk_code AS symbol,bar_end_time AS time,open_price AS open,high_price AS high,low_price AS low,latest_price AS close,volume FROM aiia_stock_realtime_minute_snapshot_full WHERE trade_date=%s AND kline_type=\'1m\' AND period_minutes=1 AND is_finalized=1 AND is_fallback=0 AND source_snapshot_time=bar_end_time AND stk_code IN ('+marks+')',(day,*missing));conn.rollback()
  if not bars.empty:
   for c in ['open','high','low','close','volume']:bars[c]=pd.to_numeric(bars[c],errors='coerce')
   bars['volume']*=100
  collect(bars,'database_finalized')
 result=[rows.get(sym,{'symbol':sym,'d1_date':day,'early_valid':False,'source':'missing_or_mismatch'}) for sym in sorted(demand.symbol)]
 # Pandas numeric scalars are normalized by the JSON roundtrip.
 cache.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=True)+'\n')
 return result


def minutes():
 demand=pd.read_pickle(OUT/'minute_demand.pkl');rows=[];jobs=list(demand.groupby('d1_date'))
 with ThreadPoolExecutor(max_workers=3) as pool:
  futures=[pool.submit(scan_day,d,g) for d,g in jobs]
  for i,f in enumerate(as_completed(futures),1):
   rows+=f.result()
   if i%25==0:print('minutes',i,'/',len(jobs),flush=True)
 facts=pd.DataFrame(rows);facts.to_pickle(OUT/'minute_facts.pkl')
 f=pd.read_pickle(OUT/'all_top5.pkl').merge(facts,on=['symbol','d1_date'],how='left',validate='many_to_one')
 f['early_volume_ratio']=f.early_volume/f.volume
 f['eligible']=f.early_valid.eq(True)&f.below0940.eq(True)&f.d1_st.eq(0)
 f['clean_return']=f.full_day_audit.eq(True)&~f.price_anomaly&f.d1_preclose.div(f.close).sub(1).abs().le(.02)&f.d4_close.gt(0)&f.buy_price.gt(0)
 f['future_seal']=f[['d2_up','d3_up','d4_up']].eq(True).any(axis=1)
 for k in (2,3,4):f[f'ret{k}']=f[f'd{k}_close']/f.buy_price-1
 f['strategy_return']=np.nan
 for k in (2,3,4):f.loc[f.exit_gap.eq(k),'strategy_return']=f.loc[f.exit_gap.eq(k),f'ret{k}']
 f['bad']=bad_labels(f)
 f.to_pickle(OUT/'enriched_top5.pkl')
 dump(OUT/'minute_coverage.json',{'requested':len(demand),'valid':int(facts.early_valid.eq(True).sum()),
  'full_day_audit':int(facts.full_day_audit.eq(True).sum()),'sources':facts.source.value_counts().to_dict()})
 print('minutes done',len(facts),flush=True)


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['spec','stage1','choose','minutes']);p.add_argument('--shard',type=int,default=0);p.add_argument('--shards',type=int,default=1);a=p.parse_args()
 if a.stage=='stage1':stage1(a.shard,a.shards)
 else:globals()[a.stage]()
