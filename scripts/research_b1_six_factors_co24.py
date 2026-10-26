"""CO24: causal six-factor extraction and fixed original-B1 rolling ablations."""
from __future__ import annotations
import argparse, hashlib, json
from collections import deque
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits
from scripts.experiment_automl_second_high_four_class import models, four_class
from scripts.experiment_automl_close_open_conditional_b1 import rank_topk

ROOT=Path(__file__).resolve().parents[1]
OLD=Path('/Users/cyrcyrisis/work/fin_agent/outputs/stock_automl')
SOURCE=OLD/'close_open_conditional_b1_r1'
OUT=ROOT/'outputs/stock_automl/b1_six_factors_co24_20261026'
REPORT=ROOT/'docs/stock_automl_runs/20261026_b1_six_factors_co24'
BASE=['signal_return','volume_ratio','turnover_so_far_pct','float_mv_100m_cny','volume_4of5_increasing','ma_bull_5_10_20','all_intraday_lows_above_ma']
SIX=['breakout_hold_30','breakout_support_breach_atr','pullback_relative_volume','pullback_recovery','late_positive_move_share','vwap_support_30']
ARMS={'A7':BASE,'B_breakout':BASE+SIX[:2],'C_pullback':BASE+SIX[2:4],'D_persistence':BASE+SIX[4:],'E13':BASE+SIX}
PROBS=['p_lt0','p_0to1','p_1to3','p_ge3']
END='2026-07-31'
CLOCK_MIN=np.r_[np.arange(571,691),np.arange(781,881)]

def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def read(p,**kw):return pd.read_csv(p,dtype={'symbol6':str},float_precision='round_trip',**kw)
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+'\n')

def six_values(close,volume,amount,prior_volume60,R,atr):
 """Input is an already-cut 220-minute prefix; event absences remain NaN."""
 c,v,a,qbase=map(lambda x:np.asarray(x,dtype=float),(close,volume,amount,prior_volume60))
 f=np.full(6,np.nan);audit={'path_complete':False,'breakout_observed':False,'pullback_observed':False,'volume_baseline_complete':False}
 if len(c)!=220 or len(v)!=220 or len(a)!=220 or len(qbase)!=60:raise ValueError('Expected 220 current and 60 past-clock entries')
 good=np.isfinite(c).all() and (c>0).all()
 if not good:return f,audit
 audit['path_complete']=True
 if np.isfinite(R) and R>0:
  f[0]=np.mean(c[-30:]>R)
  b=np.flatnonzero(c>R)
  if len(b):
   audit['breakout_observed']=True
   if np.isfinite(atr) and atr>0:f[1]=max(R-c[b[0]:].min(),0)/atr
 tail=c[-60:];h=int(np.argmax(tail))
 if 0<h<59:
  lo=int(h+1+np.argmin(tail[h+1:]));start=int(np.argmin(tail[:h]))
  if tail[start]<tail[h] and tail[lo]<tail[h]:
   audit['pullback_observed']=True
   f[3]=(tail[-1]-tail[lo])/(tail[h]-tail[lo])
   idx=np.r_[np.arange(start+1,h+1),np.arange(h+1,lo+1)]
   valid=np.isfinite(qbase[idx]).all() and (qbase[idx]>0).all() and np.isfinite(v[-60:][idx]).all() and (v[-60:][idx]>=0).all()
   audit['volume_baseline_complete']=bool(valid)
   if valid:
    q=v[-60:][idx]/qbase[idx];n=h-start
    if q[:n].mean()>0:f[2]=q[n:].mean()/q[:n].mean()
 up=np.maximum(np.diff(np.log(c[-31:])),0)
 if up.sum()>0:f[4]=up[-5:].sum()/up.sum()
 if np.isfinite(v).all() and np.isfinite(a).all() and (v>=0).all() and (a>=0).all():
  cv=v.cumsum();ca=a.cumsum()
  if (cv[-30:]>0).all():f[5]=np.mean(c[-30:]>=ca[-30:]/cv[-30:])
 return f,audit

def history():
 paths=sorted((OLD/'limit_rebound_2y_r1/price_months').glob('price_*.csv.gz'))
 paths=[p for p in paths if '2024-09'<=p.name[6:13]<='2026-07']
 cols=['stk_code','trade_date','preclose','high','low','close']
 d=pd.concat([pd.read_csv(p,usecols=cols,dtype={'stk_code':str,'trade_date':str},float_precision='round_trip') for p in paths],ignore_index=True)
 d['symbol6']=d.stk_code.str[:6];d=d[d.symbol6.str.startswith(('0','3','6'))].sort_values(['symbol6','trade_date'])
 assert not d.duplicated(['symbol6','trade_date']).any()
 cal=sorted(d.trade_date.unique());d['day_i']=d.trade_date.map(dict(zip(cal,range(len(cal)))))
 d['chain']=np.exp(np.log(d.close/d.preclose).groupby(d.symbol6).cumsum())
 d['hc']=d.high/d.close*d.chain;d['lc']=d.low/d.close*d.chain
 prev=d.groupby('symbol6').chain.shift()
 d['tr']=np.maximum.reduce([d.hc-d.lc,abs(d.hc-prev),abs(d.lc-prev)])
 g=d.groupby('symbol6');scale=d.preclose/prev
 d['R']=g.hc.transform(lambda x:x.shift().rolling(20).max())*scale
 d['atr']=g.tr.transform(lambda x:x.shift().rolling(20).mean())*scale
 ok=d.day_i.sub(g.day_i.shift(20)).eq(20)
 d.loc[~ok,['R','atr']]=np.nan
 return d[['trade_date','symbol6','R','atr']],cal,paths

def build():
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'feature_inputs').mkdir(exist_ok=True)
 src=read(SOURCE/'b1_candidates.csv.gz');src=src[src.signal_date.le(END)].copy()
 hist,cal,history_paths=history();p=src.merge(hist,left_on=['signal_date','symbol6'],right_on=['trade_date','symbol6'],how='left',validate='one_to_one')
 codes=sorted(hist.symbol6.unique());code_i={x:i for i,x in enumerate(codes)};n=len(codes)
 paths={}
 for root in [ROOT/'outputs/stock_automl/mk_1m_compact_2024',OLD/'mk_1m_compact_2025',OLD/'mk_1m_compact']:
  for f in root.glob('20*.csv.zst'):paths[f.name[:10]]=f
 days=[d for d in cal if '2024-12-02'<=d<=END]
 assert set(days)<=paths.keys(),str(set(days)-paths.keys())
 byday={d:g for d,g in p.groupby('signal_date',sort=True)}
 rolling=deque();total=np.zeros((n,60));count=np.zeros((n,60),dtype=np.int16)
 all_features=[];coverage=[];inputs=[];clock_lookup=np.full(1440,-1,dtype=int);clock_lookup[CLOCK_MIN]=np.arange(220)
 for no,day in enumerate(days):
  path=paths[day]
  bars=pd.read_csv(path,engine='pyarrow',usecols=['symbol','time','close','volume','turnover'])
  minute=bars.time.to_numpy().astype('datetime64[m]').astype(np.int64)%1440
  col=clock_lookup[minute]
  fac,un=pd.factorize(bars.symbol);mapping=np.array([code_i.get(x[:6],-1) for x in un]);row=mapping[fac]
  keep=(col>=0)&(row>=0);r=row[keep];k=col[keep]
  assert len(np.unique(r*220+k))==len(r),'Duplicate minute keys'
  c=np.full((n,220),np.nan);v=c.copy();a=c.copy()
  c[r,k]=bars.close.to_numpy()[keep];v[r,k]=bars.volume.to_numpy()[keep];a[r,k]=bars.turnover.to_numpy()[keep]
  # The current day is appended only AFTER its features are produced.
  baseline=np.where(count==20,total/20,np.nan)
  if day in byday:
   frame=byday[day];rr=frame.symbol6.map(code_i).to_numpy();rows=[]
   for pos,item in enumerate(frame.itertuples(index=False)):
    f,au=six_values(c[rr[pos]],v[rr[pos]],a[rr[pos]],baseline[rr[pos]],item.R,item.atr)
    # Historical execution proxy must be identical; otherwise don't mix sources.
    price=c[rr[pos],-1];match=np.isfinite(price) and abs(price-item.signal_price)<1e-8
    if not match:f[:]=np.nan
    rows.append({'sample_id':item.sample_id,**dict(zip(SIX,f)),**au,'signal_price_match':bool(match),'R':item.R,'atr':item.atr})
   ff=pd.DataFrame(rows);all_features.append(ff)
   np.savez_compressed(OUT/'feature_inputs'/f'{day}.npz',sample_id=frame.sample_id.to_numpy(),close=c[rr],volume=v[rr],amount=a[rr],prior_volume60=baseline[rr],R=frame.R.to_numpy(),atr=frame.atr.to_numpy())
   coverage.append(dict(day=day,rows=len(ff),path_complete=int(ff.path_complete.sum()),price_mismatch=int((~ff.signal_price_match).sum()),**{s:int(ff[s].notna().sum()) for s in SIX}))
  inputs.append({'day':day,'path':str(path),'sha256':sha(path)})
  valid=np.isfinite(v[:,-60:])&(v[:,-60:]>=0);nv=np.where(valid,v[:,-60:],0)
  rolling.append((nv,valid));total+=nv;count+=valid
  if len(rolling)>20:
   ov,om=rolling.popleft();total-=ov;count-=om
  if no%20==0:print('features',no+1,len(days),day,flush=True)
 f=pd.concat(all_features,ignore_index=True)
 joined=src.merge(f,on='sample_id',how='left',validate='one_to_one');assert len(joined)==len(src)
 joined.to_csv(OUT/'panel.csv.gz',index=False,float_format='%.17g',compression={'method':'gzip','mtime':0})
 pd.DataFrame(coverage).to_csv(REPORT/'feature_coverage.csv',index=False)
 save(OUT/'build_manifest.json',dict(rows=len(joined),dates=joined.signal_date.nunique(),minute_sources=inputs,daily_sources=[dict(path=str(x),sha256=sha(x)) for x in history_paths],parent_sha256=sha(SOURCE/'b1_candidates.csv.gz'),panel_sha256=sha(OUT/'panel.csv.gz'),code_sha256=sha(__file__)))
 print('BUILD COMPLETE',len(joined),flush=True)

def fit_fold(day,panel,fold):
 dest=OUT/'folds'/day;dest.mkdir(parents=True,exist_ok=True)
 if (dest/'complete.json').exists():return
 oldfolder=SOURCE/'stage1'/day
 members=read(oldfolder/'membership.csv.gz');train=panel.set_index('sample_id').loc[members.sample_id].reset_index()
 assert train.signal_date.max()<day and train.next_date.le(day).all()
 assert train.signal_date.nunique()==20 and train.sample_id.tolist()==members.sample_id.tolist()
 test=panel[panel.signal_date.eq(day)].copy()
 y=four_class(train.stage1_second_high/train.signal_price-1).astype(str)
 members.to_csv(dest/'membership.csv.gz',index=False)
 old=read(oldfolder/'scores.csv.gz').set_index('symbol6').loc[test.symbol6]
 outputs=[];manifest=[]
 for arm,features in ARMS.items():
  m=models()['small_boosted_tree'];m.fit(train[features],y)
  prob=m.predict_proba(test[features]);sc=test[['sample_id','signal_date','symbol6']].copy()
  for label in ['lt0','0to1','1to3','ge3']:sc['p_'+label]=prob[:,list(m.classes_).index(label)]
  sc['predicted_class']=m.classes_[prob.argmax(axis=1)];sc['arm']=arm
  if arm=='A7':np.testing.assert_allclose(sc[PROBS],old[PROBS],rtol=0,atol=1e-14)
  joblib.dump(m,dest/(arm+'.joblib'),compress=3)
  np.testing.assert_array_equal(joblib.load(dest/(arm+'.joblib')).predict_proba(test[features]),prob)
  outputs.append(sc);manifest.append(dict(arm=arm,features=features,model_sha256=sha(dest/(arm+'.joblib'))))
 pd.concat(outputs,ignore_index=True).to_csv(dest/'scores.csv.gz',index=False,float_format='%.17g')
 save(dest/'complete.json',dict(day=day,train_dates=fold['train_dates'],train_rows=len(train),test_rows=len(test),label_max=train.next_date.max(),membership_sha256=sha(dest/'membership.csv.gz'),scores_sha256=sha(dest/'scores.csv.gz'),models=manifest,baseline_verified=True))

def train(shard='0/1',limit=None):
 if sklearn.__version__!='1.7.1':raise ValueError('Requires sklearn1.7.1')
 panel=read(OUT/'panel.csv.gz');folds=[f for f in json.loads((SOURCE/'stage1_folds.json').read_text()) if f['day']<=END]
 i,n=map(int,shard.split('/'));folds=folds[i::n];folds=folds[:limit] if limit else folds
 fingerprint={'panel_sha256':sha(OUT/'panel.csv.gz'),'code_sha256':sha(__file__),'sklearn':sklearn.__version__,'arms':ARMS}
 # Shards have separate immutable fingerprints, avoiding a shared write race.
 fp=OUT/f'train_source_{i}.json'
 if fp.exists():assert json.loads(fp.read_text())==fingerprint
 else:save(fp,fingerprint)
 with threadpool_limits(limits=1):
  for k,f in enumerate(folds):
   fit_fold(f['day'],panel,f)
   if k%10==0:print('train',shard,k+1,len(folds),f['day'],flush=True)
 print('TRAIN COMPLETE',shard,len(folds),flush=True)

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['build','train']);ap.add_argument('--shard',default='0/1');ap.add_argument('--limit',type=int);args=ap.parse_args()
 if args.command=='build':build()
 else:train(args.shard,args.limit)
