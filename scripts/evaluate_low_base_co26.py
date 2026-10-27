"""CO26 daily paired evaluation; validation chooses algorithms before test."""
from pathlib import Path
import argparse,json
import numpy as np
import pandas as pd
from scripts.research_gentle_rise_gr1 import sha

R=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_ml_co26');MODELS=['logistic','hgb']

def daily(s,model,h):
 out=[]
 for day,g in s.groupby('date',sort=True):
  g=g.sort_values(['score','stk_code'],ascending=[False,True]);p=g.net.dropna();t=g.head(3);v=t.net.dropna();first=g.iloc[0]
  ends=g.label_end.dropna().unique();assert len(ends)<=1
  row=dict(date=day,label_end=str(ends[0]) if len(ends) else None,month=day[:7],model=model,horizon=h,pool_candidates=len(g),pool_known=len(p),pool_unknown=int(g.net.isna().sum()),pool_net=p.mean(),pool_up=(p.round(10)>.02).mean(),pool_down=(p.round(10)<-.02).mean(),top1=first.stk_code,top1_net=first.net,top3_n_known=len(v),top3_net=v.mean() if len(v)==3 else np.nan,top3_up=(v.round(10)>.02).mean() if len(v)==3 else np.nan,top3_down=(v.round(10)<-.02).mean() if len(v)==3 else np.nan,top3_symbols=','.join(t.stk_code.astype(str)))
  out.append(row)
 return pd.DataFrame(out)

def summarize(df,period):
 groups=['horizon','model']+(['month'] if period=='month' else [])
 rows=[]
 for key,g in df.groupby(groups,sort=True):
  z=g[g.top3_net.notna()&g.pool_net.notna()];one=g[g.top1_net.notna()&g.pool_net.notna()];base=dict(zip(groups,key if isinstance(key,tuple) else (key,)))
  base.update(days=len(g),paired_top3_days=len(z),paired_top1_days=len(one),candidate_mean=g.pool_candidates.mean(),pool_known=int(g.pool_known.sum()),pool_unknown=int(g.pool_unknown.sum()),top3_net=z.top3_net.mean(),pool_net=z.pool_net.mean(),top3_net_excess=(z.top3_net-z.pool_net).mean(),top3_up=z.top3_up.mean(),pool_up=z.pool_up.mean(),top3_down=z.top3_down.mean(),pool_down=z.pool_down.mean(),top1_net=one.top1_net.mean(),top1_net_excess=(one.top1_net-one.pool_net).mean(),top1_up=(one.top1_net.round(10)>.02).mean(),top1_down=(one.top1_net.round(10)<-.02).mean(),top1_pool_up=one.pool_up.mean(),top1_pool_down=one.pool_down.mean())
  rows.append(base)
 return pd.DataFrame(rows)

def selection_eligible(df, cutoff='2026-01-01'):
 return df[df.label_end.notna() & df.label_end.lt(cutoff)].copy()

def main():
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['validation','test']);a=p.parse_args();complete=json.loads((R/f'{a.phase}_complete.json').read_text());chosen=json.loads((R/'selection.json').read_text())['selected_models'] if a.phase=='test' else None
 days=[]
 for month in complete['months']:
  folder=R/a.phase/month;receipt=json.loads((folder/'complete.json').read_text());assert receipt['source_sha256']==complete['samples_sha256'] and receipt['design_sha256']==complete['design_sha256']
  for h in [2,3]:
   names=MODELS if chosen is None else [chosen[str(h)]]
   for name in names:
    s=pd.read_parquet(folder/f'{name}_h{h}_scores.parquet');days.append(daily(s,name,h))
   # Independent, predetermined lower-position ranking on exactly the same candidate rows.
   s['score']=-s.position60;days.append(daily(s,'lowest_position',h))
 df=pd.concat(days,ignore_index=True)
 if a.phase=='validation':df=selection_eligible(df)
 df.to_csv(R/f'{a.phase}_daily.csv.gz',index=False)
 overall=summarize(df,'overall');monthly=summarize(df,'month');overall.to_csv(D/f'{a.phase}_overall.csv',index=False);monthly.to_csv(D/f'{a.phase}_monthly.csv',index=False)
 if a.phase=='validation':
  selected={}; comparisons={}
  for h in [2,3]:
   available=selection_eligible(df)
   vals=available[(available.horizon==h)&available.model.isin(MODELS)].pivot(index='date',columns='model',values='top3_net');common=vals.dropna().index
   subset=available[(available.horizon==h)&available.model.isin(MODELS)&available.date.isin(common)].copy()
   assert len(subset)==2*len(common)
   values={n:float((subset[subset.model.eq(n)].top3_net-subset[subset.model.eq(n)].pool_net).mean()) for n in MODELS}
   selected[str(h)]=max(MODELS,key=lambda n:(values[n],-MODELS.index(n)));comparisons[str(h)]=dict(common_days=len(common),average_daily_excess=values)
  selection=dict(selected_models=selected,validation=comparisons,design_sha256=complete['design_sha256'],samples_sha256=complete['samples_sha256'],label_maturity_cutoff='2026-01-01',selection_rule='paired daily Top3 net excess vs same-day pool, maximum; fixed model order for tie')
  (R/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2));(D/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2));print(json.dumps(selection,ensure_ascii=False,indent=2))
 print(overall.to_string(index=False),flush=True)
if __name__=='__main__':main()
