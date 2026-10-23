from __future__ import annotations
import argparse,json,sys,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import numpy as np,pandas as pd,joblib,sklearn
from scripts import experiment_b2_validation_r31 as r
from scripts import experiment_b2_full_2024_r34 as r34
from scripts import experiment_limit_rebound_shape_r19 as shape
from scripts import experiment_limit_rebound_four_minutes_r24 as four
from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_limit_rebound_short_mixed_r12 import add_features,enrich_outcomes,make_model,xmatrix
from scripts.experiment_limit_rebound_segments_r18 import FEATURES,segment_one,RAW
from scripts.experiment_limit_rebound_two_stage_r15 import rank,classify
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.replay_limit_rebound_cash_r23 import replay,dump,sha
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
MINUTE=Path('/data/cyris/tushare_market_data')
KEY=['symbol','d0_date']
TEST=[str(m) for m in pd.period_range('2017-05','2021-12',freq='M')]
MODELS=[str(m) for m in pd.period_range('2016-09','2021-12',freq='M')]
def source(dv,month,part):
 assert dv==2
 return ROOT/'daily2'/month/f'segments_{part}_scores.pkl'
def spec():
 p=ROOT/'specification.json'
 if not p.exists():
  value=dict(experiment='R90 B2 V2 2016-2021 historical extension',parent_strategy='imac/B2_V2',parent_definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',daily_training_months=6,daily_validation_months=2,shape_training_months=6,shape_validation_months=2,first_daily_forecast='2016-09',first_full_test='2017-05',last_full_test='2021-12',daily_snapshot_sha256=sha(ROOT/'daily_sources.json'),model_family='R18 RF51 240 trees same parameters/seed',execution='Original fourK79 and Top3 no substitute after lock; D2 morning >7% next traded minute open else close; fees 0.1% each side',historical_market_rule='ChiNext 10% before 2020-08-24 and 20% from that date, original cap rounding',production_changed=False)
  dump(p,value)
 return json.loads(p.read_text())
def panel():
 records=json.loads((ROOT/'daily_sources.json').read_text())
 cols=['trade_date','stk_code','preclose','open','high','low','close','volume','amount','turn_ratio','is_limit_price']
 raw=pd.concat([pd.read_csv(x['path'],usecols=cols,dtype={'trade_date':str,'stk_code':str}) for x in records],ignore_index=True)
 assert all(sha(Path(x['path']))==x['sha256'] for x in records)
 print('raw',len(raw),flush=True)
 daily=prepare_daily(raw)
 del raw
 cal=sorted(daily.trade_date.unique())
 required_minutes=[d for d in cal if '2016-09-01'<=d<='2022-01-31']
 missing_minutes=[d for d in required_minutes if not (MINUTE/d[:4]/f'{d.replace("-","")}.csv.gz').exists()]
 assert not missing_minutes,missing_minutes[:10]
 dump(ROOT/'minute_calendar_coverage.json',dict(required_days=len(required_minutes),first=required_minutes[0],last=required_minutes[-1],missing=missing_minutes))
 daily['date_i']=daily.trade_date.map({d:i for i,d in enumerate(cal)})
 q,quality=build_panel(daily,ROOT,score_all_d0=True,d0_start='2016-01-01')
 q=q[q.d0_date.lt('2022-01-01')].copy()
 print('basic panel',len(q),quality,flush=True)
 q=add_features(q,daily)
 q=enrich_outcomes(q,daily)
 q['first_i']=q.first_limit_date.map({d:i for i,d in enumerate(cal)})
 targets={s:g for s,g in q.groupby('symbol',sort=False)}
 z=[];audit=[]
 for j,(sym,g) in enumerate(daily.groupby('symbol',sort=False),1):
  if sym not in targets:continue
  pos={d:i for i,d in enumerate(g.trade_date)}
  for row in targets[sym].itertuples():
   feat,meta,_=segment_one(g,pos[row.first_limit_date],pos[row.d0_date])
   assert meta['raw_board_days']==row.board_streak_days,(sym,row.d0_date)
   z.append(dict(symbol=sym,d0_date=row.d0_date,**feat))
   audit.append(dict(symbol=sym,d0_date=row.d0_date,**meta))
  if j%1000==0:print('segments',j,len(z),flush=True)
 q=q.merge(pd.DataFrame(z),on=KEY,validate='one_to_one')
 q.to_pickle(ROOT/'panel.pkl')
 pd.DataFrame(audit).to_pickle(ROOT/'segments_audit.pkl')
 dump(ROOT/'calendar.json',cal)
 dump(ROOT/'panel_manifest.json',dict(rows=len(q),min_d0=q.d0_date.min(),max_d0=q.d0_date.max(),quality=quality,panel_sha256=sha(ROOT/'panel.pkl'),calendar_sha256=sha(ROOT/'calendar.json')))
 print('panel ready',len(q),flush=True)
def train_one(month,panel,cal):
 w=r.window(month,2)
 tr=r.block(panel,cal,*w['train'])
 va=r.block(panel,cal,*w['valid'],purge_events=False)
 te=panel[panel.d0_date.str.startswith(month)]
 fit=tr[tr.activated&tr.observed_3].copy()
 assert len(fit)>100,(month,len(fit))
 assert not set(zip(tr.symbol,tr.first_limit_date))&set(zip(va.symbol,va.first_limit_date))
 dest=ROOT/'daily2'/month;dest.mkdir(parents=True,exist_ok=True)
 if (dest/'metrics.json').exists():return month
 model=make_model('rf');model.steps[-1][1].n_jobs=1
 model.fit(xmatrix(fit,FEATURES['segments']),fit.label_3,randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
 fit.to_pickle(dest/'training_samples.pkl')
 bundle=dict(model=model,features=FEATURES['segments'],month=month,window=w,sklearn=sklearn.__version__,training_keys=fit[KEY+['first_limit_date']].to_dict('records'),spec_sha256=sha(ROOT/'specification.json'))
 joblib.dump(bundle,dest/'segments.joblib',compress=3)
 restored=joblib.load(dest/'segments.joblib')['model']
 assert np.array_equal(model.predict_proba(xmatrix(fit.iloc[:50],FEATURES['segments'])),restored.predict_proba(xmatrix(fit.iloc[:50],FEATURES['segments'])))
 metrics={}
 for part,frame in [('train',tr),('valid',va),('forecast',te)]:
  scored=rank(frame,restored.predict_proba(xmatrix(frame,FEATURES['segments']))[:,1])
  scored[KEY+['score','d0_rank']].to_pickle(source(2,month,part))
  metrics[part]=classify(scored)
 dump(dest/'metrics.json',dict(window=w,fit_rows=len(fit),metrics=metrics,model_sha256=sha(dest/'segments.joblib'),sample_sha256=sha(dest/'training_samples.pkl')))
 return month
def train():
 assert sklearn.__version__=='1.7.1'
 spec()
 p=pd.read_pickle(ROOT/'panel.pkl');cal=json.loads((ROOT/'calendar.json').read_text())
 with ThreadPoolExecutor(max_workers=3) as pool:
  futures=[pool.submit(train_one,m,p,cal) for m in MODELS]
  for f in as_completed(futures):print('trained',f.result(),flush=True)
def enrich_raw_day_r90(day,demand):
 return r34.enrich_raw_day(day,demand,r)

def configure():
 r.OUT=ROOT;r.BASE=ROOT;r.MONTHS=TEST;r.HISTORY=MODELS;r.VARIANTS={'D2S2':(2,2)}
 r.spec=spec;r.source=source
 def hilo(demand):
  wanted=set(demand.symbol);months=set(demand.d1_date.dropna().str[:7])
  frames=[]
  for month in sorted(months):
   path=ROOT/'price_months'/f'price_{month}.csv.gz'
   f=pd.read_csv(path,usecols=['stk_code','trade_date','high','low'],dtype={'stk_code':str,'trade_date':str})
   f['symbol']=f.stk_code.str[:6]
   frames.append(f[f.symbol.isin(wanted)][['symbol','trade_date','high','low']].rename(columns={'trade_date':'d1_date','high':'d1_high','low':'d1_low'}))
  out=pd.concat(frames,ignore_index=True)
  assert not out.duplicated(['symbol','d1_date']).any()
  return out
 r.extension.daily_high_low=hilo
 r.extension.with_status=r34.historical_states
 r34.raw_minute_path=lambda day:MINUTE/day[:4]/f'{day.replace("-","")}.csv.gz'
 r.enrich_day=enrich_raw_day_r90
def prepare():
 configure()
 assert all((ROOT/'daily2'/m/'metrics.json').exists() for m in MODELS)
 r.prepare()
 print('coverage',json.loads((ROOT/'coverage.json').read_text()),flush=True)
def select():
 configure()
 f=pd.read_pickle(ROOT/'enriched.pkl');cal=json.loads((ROOT/'calendar.json').read_text())
 chosen=[];candidates=[]
 original=shape.window
 try:
  shape.window=lambda month,valid=2:r.window(month,2)
  for month in TEST:
   group=f[f.model_month.eq(month)];parts=[]
   for part in ('train','valid'):
    a=group[group.part.eq(part)]
    cutoff=str(pd.Period(r.window(month,2)[part][1])+1)+'-01'
    assert all(cal[int(i)+4]<cutoff for i in a.date_i)
    parts.append(a[a.complete].copy())
   item=json.loads(json.dumps(four.choose_rule(*parts,month,shape.rules())))
   item['variant']='D2S2';chosen.append(item)
   for rule in shape.rules():
    candidates.append(dict(month=month,rule=rule,train=shape.metrics(parts[0],rule),valid=shape.metrics(parts[1],rule)))
   print('rule',month,item['selected']['id'],flush=True)
 finally:shape.window=original
 dump(ROOT/'rules_frozen.json',chosen)
 dump(ROOT/'rule_candidates.json',candidates)
 print('rules saved before test account evaluation',flush=True)
def evaluate():
 configure()
 rules=json.loads((ROOT/'rules_frozen.json').read_text())
 f=pd.read_pickle(ROOT/'enriched.pkl');first=pd.read_pickle(ROOT/'first10.pkl')
 first['symbol']=first.symbol.str[:6]
 bars={(s,d):g.sort_values('time') for (s,d),g in first.groupby(KEY)}
 picks=[];metrics=[]
 for item in rules:
  month=item['month'];q=f[f.model_month.eq(month)&f.part.eq('test')]
  a=four.delay_same_stock(four.chosen(q,item['selected']),bars)
  a['rule_id']=item['selected']['id']
  a['rule_text']=item['selected']['text'].replace('五分','四分')
  picks.append(a)
  known=a[a.observed_3]
  metrics.append(dict(month=month,n=len(known),hits=int(known.label_3.sum()),hit=float(known.label_3.mean()) if len(known) else None))
  print('signal',month,len(a),flush=True)
 signal=pd.concat(picks,ignore_index=True)
 assert not signal.d1_date.duplicated().any()
 assert signal.d0_date.str[:7].isin(TEST).all()
 signal.to_pickle(ROOT/'signals.pkl')
 dump(ROOT/'shape_test_metrics.json',metrics)
 print('signals',len(signal),flush=True)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['panel','train','prepare','select','evaluate'])
 args=parser.parse_args();globals()[args.stage]()
