"""CO17 frozen 20-train/10-validation/next-day experiment; never writes production."""
from pathlib import Path
import json, hashlib, argparse
import numpy as np
import pandas as pd
import joblib, sklearn
from threadpoolctl import threadpool_limits
from sklearn.ensemble import HistGradientBoostingClassifier
from scripts.backfill_b1_history import read
from scripts.run_automl_four_class_pipeline import generate_training_features,generate_training_labels,select_inference_candidates,infer,select_top
from scripts.experiment_automl_second_high_four_class import models

NAMES=['原HGB','强约束HGB','逻辑回归']
def windows(calendar,day):
 i=calendar.index(day)
 if i<30:raise ValueError('历史日期不足')
 return calendar[i-30:i-10],calendar[i-10:i]
def choose(stats):
 valid=[i for i,s in enumerate(stats) if s['trades']>=3 and not s['unknown']]
 k=max(valid,key=lambda i:(stats[i]['score'],-i)) if valid else 0
 s=stats[k];passed=bool(valid) and s['score']>0
 stable=passed and all(n>=1 and v>0 for n,v in zip(s['half_trades'],s['half_net']))
 return k,passed,stable

def new_models():
 m=models();return [m['small_boosted_tree'],HistGradientBoostingClassifier(max_iter=60,learning_rate=.05,max_leaf_nodes=3,min_samples_leaf=200,l2_regularization=10,early_stopping=False,random_state=42),m['logistic']]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,default=Path('outputs/stock_automl/b1_history_20261016/run'));ap.add_argument('--output',type=Path,default=Path('outputs/stock_automl/b1_validation_co17_20261026'));ap.add_argument('--shard',default='0/1');a=ap.parse_args();shard,total=map(int,a.shard.split('/'));assert 0<=shard<total
 if sklearn.__version__!='1.7.1':raise ValueError('Use sklearn1.7.1')
 out=a.output;out.mkdir(parents=True,exist_ok=True);c=read(a.source/'candidates.csv.gz');cal=read(a.source/'calendar.csv').date.tolist();amount=read(a.source/'amounts.csv.gz').set_index(['signal_date','symbol6']).amount
 groups={d:g for d,g in c.groupby('signal_date')};days=[d for d in cal if '2026-01-01'<=d<=c.signal_date.max() and cal.index(d)>=30 and set(cal[cal.index(d)-30:cal.index(d)])<=set(groups)]
 inputs={n:sha(a.source/n) for n in ['candidates.csv.gz','amounts.csv.gz','calendar.csv','picks.csv']};metadata=dict(hashes=inputs,sklearn=sklearn.__version__,days=days)
 if (out/'source.json').exists():
  assert json.loads((out/'source.json').read_text())==metadata,'Resume input changed'
 else:save(out/'source.json',metadata)
 days=days[shard::total]
 def prediction(model,day):
  frame=select_inference_candidates(c,day);scores=infer(model,frame);p=select_top(scores);p=p[p.selection_rank.eq(1)]
  result=dict(day=day,stock='',actionable=False,gross=0.,net=0.,label=None,unknown=False)
  if p.empty:return result,scores
  r=p.iloc[0];hist=cal[cal.index(day)-20:cal.index(day)];past=np.array([amount.get((d,r.symbol6),np.nan) for d in hist]);now=amount.get((day,r.symbol6),np.nan)
  known=np.isfinite(past).all() and np.isfinite(now) and np.median(past)>0
  gate=bool(now>=np.median(past)) if known else None
  outcome=groups[day].set_index('symbol6').loc[r.symbol6];label=(outcome.second_high/r.signal_price-1) if pd.notna(outcome.second_high) else None
  result.update(stock=r.symbol6,actionable=gate,signal_price=float(r.signal_price),sell_price=float(outcome.close_40) if pd.notna(outcome.close_40) else None,label=label,unknown=not known)
  if gate:
   if pd.isna(outcome.close_40):result.update(gross=None,net=None,unknown=True)
   else:result.update(gross=float(outcome.close_40/r.signal_price-1),net=float(outcome.close_40*(1-.001)/(r.signal_price*(1+.001))-1))
  return result,scores
 records=[];validations=[]
 with threadpool_limits(limits=1):
  for day in days:
   dest=out/'folds'/day;dest.mkdir(parents=True,exist_ok=True)
   if (dest/'result.json').exists():
    x=json.loads((dest/'result.json').read_text());records.extend(x['records']);validations.extend(x['validation']);continue
   td,vd=windows(cal,day);train=c[c.signal_date.isin(td)&c.second_high.notna()&c.next_date.le(vd[0])].copy()
   assert set(train.signal_date)==set(td) and max(td)<min(vd) and max(vd)<day
   train.to_csv(dest/'training.csv.gz',index=False,float_format='%.17g');c[c.signal_date.isin(vd)].to_csv(dest/'validation.csv.gz',index=False,float_format='%.17g');groups[day].to_csv(dest/'test.csv.gz',index=False,float_format='%.17g')
   stats=[];test=[];valrows=[]
   for k,m in enumerate(new_models()):
    m.fit(generate_training_features(train),generate_training_labels(train));joblib.dump(m,dest/f'model{k}.joblib',compress=3)
    v=[];all_scores=[]
    for vday in vd:
     rr,ss=prediction(m,vday);v.append(rr);all_scores.append(ss)
    pd.concat(all_scores).to_csv(dest/f'validation_scores{k}.csv.gz',index=False,float_format='%.17g')
    st=dict(trades=sum(r['actionable'] is True and not r['unknown'] for r in v),unknown=sum(r['unknown'] for r in v),score=sum(r['net'] or 0 for r in v)/10,half_trades=[sum(r['actionable'] is True and not r['unknown'] for r in h) for h in [v[:5],v[5:]]],half_net=[sum(r['net'] or 0 for r in h) for h in [v[:5],v[5:]]])
    stats.append(st);rr,ss=prediction(m,day);test.append(rr);ss.to_csv(dest/f'test_scores{k}.csv.gz',index=False,float_format='%.17g');valrows.extend([dict(fold=day,model=NAMES[k],**r) for r in v])
   best,passed,stable=choose(stats)
   recent=cal[cal.index(day)-20:cal.index(day)];baseline_train=c[c.signal_date.isin(recent)&c.second_high.notna()&c.next_date.le(day)];baseline=new_models()[0].fit(generate_training_features(baseline_train),generate_training_labels(baseline_train));baseline_train.to_csv(dest/'baseline_training.csv.gz',index=False,float_format='%.17g');joblib.dump(baseline,dest/'baseline.joblib',compress=3);base,bs=prediction(baseline,day);bs.to_csv(dest/'baseline_scores.csv.gz',index=False,float_format='%.17g')
   rr=[]
   for name,r,allow in [('A原20日滚动',base,True),('B早20日固定',test[0],True),('C验证选优',test[best],True),('D验证盈利才做',test[best],passed),('E两段验证均盈利',test[best],stable)]:
    rr.append(dict(**r,variant=name,chosen=NAMES[best] if name[0] in 'CDE' else NAMES[0],validation_score=stats[best]['score'],validation_trades=stats[best]['trades'],reliable=passed,stable=stable,executed=bool(r['actionable'] and allow),return_gross=r['gross'] if allow else 0.,return_net=r['net'] if allow else 0.))
   save(dest/'result.json',dict(train_dates=td,validation_dates=vd,stats=stats,records=rr,validation=valrows));records.extend(rr);validations.extend(valrows);print(day,NAMES[best],passed,stable,flush=True)
 suffix='' if total==1 else f'_shard{shard}'
 pd.DataFrame(records).to_csv(out/f'daily{suffix}.csv',index=False);pd.DataFrame(validations).to_csv(out/f'validation_picks{suffix}.csv.gz',index=False)
 save(out/f'complete{suffix}.json',dict(days=len(days),first=days[0],last=days[-1],input_hashes=inputs))
if __name__=='__main__':main()
