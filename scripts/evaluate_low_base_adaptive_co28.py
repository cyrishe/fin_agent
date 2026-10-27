"""Evaluate CO28 on paired dates and retain unknown outcome counts."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.research_low_base_adaptive_co28 import OUT,DOC,VARIANTS
from scripts.evaluate_low_base_co26 import daily,selection_eligible

PREV=Path('outputs/stock_automl/low_base_ml_co26_20261027'); RF=Path('outputs/stock_automl/low_base_rf_co27_20261027')
def main():
 months=json.loads((OUT/'complete.json').read_text())['months'];parts=[]
 for month in months:
  phase='validation' if month<'2026-01' else 'test'
  for h in [2,3]:
   for name in VARIANTS:
    s=pd.read_parquet(OUT/phase/month/f'{name}_h{h}_scores.parquet');z=daily(s,name,h)
    if phase=='validation':z=selection_eligible(z)
    z['phase']=phase;parts.append(z)
 new=pd.concat(parts,ignore_index=True);new.to_csv(OUT/'daily.csv.gz',index=False)
 old=[]
 for phase in ['validation','test']:
  z=pd.read_csv(PREV/f'{phase}_daily.csv.gz',dtype={'top1':str});z['phase']=phase;old.append(z[z.model.eq('logistic')])
 z=pd.read_csv(RF/'rf_daily.csv.gz',dtype={'top1':str});old.append(z)
 previous=pd.concat(old,ignore_index=True);results=[];monthly=[];interval=[];rng=np.random.default_rng(20261027)
 for (phase,h,name),g in new.groupby(['phase','horizon','model']):
  base='rf' if name.endswith('_rf') else 'logistic';b=previous[(previous.phase==phase)&(previous.horizon==h)&(previous.model==base)]
  x=g.merge(b,on='date',suffixes=('_new','_old'),validate='one_to_one');c=x[x.top3_net_new.notna()&x.top3_net_old.notna()].copy();assert len(c)>0
  def summarize(c):
   return dict(days=len(c),new_net=c.top3_net_new.mean(),old_net=c.top3_net_old.mean(),pool_net=c.pool_net_new.mean(),new_down=c.top3_down_new.mean(),old_down=c.top3_down_old.mean(),pool_down=c.pool_down_new.mean(),new_up=c.top3_up_new.mean(),old_up=c.top3_up_old.mean(),top1_new=c.top1_net_new.mean(),top1_old=c.top1_net_old.mean())
  results.append(dict(phase=phase,horizon=h,model=name,baseline=base,all_days=len(g),unknown_new=int(g.top3_net.isna().sum()),unknown_old=int(b.top3_net.isna().sum()),**summarize(c)))
  for mon,q in c.groupby(c.date.str[:7]):monthly.append(dict(phase=phase,horizon=h,model=name,month=mon,**summarize(q)))
  delta=c.assign(delta=c.top3_net_new-c.top3_net_old).groupby(c.date.str[:7]).delta.agg(['sum','count']);idx=rng.integers(0,len(delta),(10000,len(delta)));bs=delta['sum'].to_numpy()[idx].sum(axis=1)/delta['count'].to_numpy()[idx].sum(axis=1)
  pooldelta=c.assign(delta=c.top3_net_new-c.pool_net_new).groupby(c.date.str[:7]).delta.agg(['sum','count'])
  poolbs=pooldelta['sum'].to_numpy()[idx].sum(axis=1)/pooldelta['count'].to_numpy()[idx].sum(axis=1)
  interval.append(dict(phase=phase,horizon=h,model=name,delta=(c.top3_net_new-c.top3_net_old).mean(),low=np.quantile(bs,.025),high=np.quantile(bs,.975),months_better=int((delta['sum']>0).sum()),months=len(delta),vs_pool=(c.top3_net_new-c.pool_net_new).mean(),pool_low=np.quantile(poolbs,.025),pool_high=np.quantile(poolbs,.975),months_above_pool=int((pooldelta['sum']>0).sum())))
 pd.DataFrame(results).to_csv(DOC/'paired_overall.csv',index=False);pd.DataFrame(monthly).to_csv(DOC/'paired_monthly.csv',index=False);pd.DataFrame(interval).to_csv(DOC/'paired_bootstrap.csv',index=False)
 # Same dates for all candidates; selection is restricted to matured 2025 outcomes.
 selection={}
 for h in [2,3]:
  v=pd.concat([new,previous],ignore_index=True)
  v=v[(v.phase=='validation')&(v.horizon==h)]
  wide=v.pivot(index='date',columns='model',values='top3_net').dropna();means=wide.mean();chosen=means.idxmax();selection[str(h)]={'common_days':len(wide),'chosen':chosen,'validation_mean_net':{k:float(x) for k,x in means.items()},'validation_cutoff':'2026-01-01','compared_existing':['logistic','rf']}
 (DOC/'selection.json').write_text(json.dumps(selection,indent=2))
 # Pre-signal market context diagnostic: market 20-day direction and 2024-only volatility median.
 state=pd.read_parquet(OUT/'samples.parquet',columns=['date','market_ret20','market_vol20']).drop_duplicates('date')
 vol_boundary=float(state[state.date.str.startswith('2024')].market_vol20.median())
 state['market_direction']=np.where(state.market_ret20.ge(0),'up','down')
 state['market_volatility']=np.where(state.market_vol20.ge(vol_boundary),'high','low')
 rows=[]
 for (phase,h,name),g in new.groupby(['phase','horizon','model']):
  baseline='rf' if name.endswith('_rf') else 'logistic'
  b=previous[(previous.phase==phase)&(previous.horizon==h)&(previous.model==baseline)]
  x=g.merge(b,on='date',suffixes=('_new','_old'),validate='one_to_one').merge(state,on='date',validate='many_to_one')
  x=x[x.top3_net_new.notna()&x.top3_net_old.notna()]
  for (direction,vol),q in x.groupby(['market_direction','market_volatility']):
   rows.append(dict(phase=phase,horizon=h,model=name,market_direction=direction,market_volatility=vol,days=len(q),new_net=q.top3_net_new.mean(),old_net=q.top3_net_old.mean(),pool_net=q.pool_net_new.mean(),new_down=q.top3_down_new.mean(),old_down=q.top3_down_old.mean()))
 pd.DataFrame(rows).to_csv(DOC/'market_regimes.csv',index=False)
 ablations=[]
 contrasts=[('rank_lr','state_lr'),('rank_rf','state_rf'),('state_lr','relative_lr'),('state_lr','short_lr'),('state_lr','long_lr')]
 for (phase,h),g in new.groupby(['phase','horizon']):
  w=g.pivot(index='date',columns='model',values='top3_net')
  for a,b in contrasts:
   pair=w[[a,b]].dropna();diff=pair[b]-pair[a];monthly=diff.groupby(diff.index.str[:7]).agg(['sum','count']);idx=rng.integers(0,len(monthly),(10000,len(monthly)));draw=monthly['sum'].to_numpy()[idx].sum(axis=1)/monthly['count'].to_numpy()[idx].sum(axis=1)
   ablations.append(dict(phase=phase,horizon=h,base=a,changed=b,days=len(pair),increment=diff.mean(),ci_low=np.quantile(draw,.025),ci_high=np.quantile(draw,.975),months_better=int((monthly['sum']>0).sum()),months=len(monthly)))
 pd.DataFrame(ablations).to_csv(DOC/'variant_ablation.csv',index=False)
 (DOC/'regime_definition.json').write_text(json.dumps({'volatility_boundary_from_2024':vol_boundary,'return_boundary':0,'source':'CO25 equal-weight market returns as of D close'},indent=2))
 print(pd.DataFrame(results).to_string(index=False),flush=True)
if __name__=='__main__':main()
