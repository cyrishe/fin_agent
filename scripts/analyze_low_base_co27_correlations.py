"""Pooled factor correlations and day-matched correlations with future outcomes."""
from pathlib import Path
import json
import numpy as np,pandas as pd
from scripts.build_low_base_co26 import CORE,EXTRA
from scripts.research_gentle_rise_gr1 import sha
R=Path('outputs/stock_automl/low_base_rf_co27_20261027');P=Path('outputs/stock_automl/low_base_ml_co26_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27');F=CORE+EXTRA

def main():
 s=pd.read_parquet(P/'samples.parquet',columns=['date','stk_code','net2','net3',*F]);s['year']=s.date.str[:4]
 tables=[]
 for year,g in [('all',s)]+list(s.groupby('year',sort=True)):
  corr=g[F].corr(method='pearson',min_periods=100);corr.to_csv(D/f'factor_pearson_{year}.csv')
  for i,x in enumerate(F):
   for y in F[i+1:]:tables.append(dict(year=year,factor1=x,factor2=y,pearson=corr.loc[x,y],paired=int((g[x].notna()&g[y].notna()).sum())))
 pd.DataFrame(tables).to_csv(D/'factor_pairs.csv',index=False)
 candidate_days=s.date.nunique()
 records=[]
 for day,g in s.groupby('date',sort=True):
  for h in [2,3]:
   z=g[g[f'net{h}'].notna()];
   if len(z)<30:continue
   net=z[f'net{h}'].round(10);x=z[F].replace([np.inf,-np.inf],np.nan).rank(method='average',pct=True)
   signals={'net_spearman':net.rank(method='average',pct=True),'up2_rank_corr':(net>.02).astype(float),'down2_rank_corr':(net<-.02).astype(float)}
   vals={name:x.corrwith(y) if y.nunique()>1 else pd.Series(np.nan,index=F) for name,y in signals.items()}
   # Pairwise Spearman ranks must be recomputed on the same nonmissing rows.
   pair=z[['position120',f'net{h}']].dropna().copy();pair[f'net{h}']=pair[f'net{h}'].round(10)
   vals['net_spearman']['position120']=pair.corr(method='spearman').iloc[0,1] if len(pair)>=30 else np.nan
   for metric in vals.values():metric.loc[x.notna().sum()<30]=np.nan
   for f in F:records.append(dict(date=day,year=day[:4],month=day[:7],horizon=h,factor=f,n=int((x[f].notna()&net.notna()).sum()),**{k:float(v[f]) for k,v in vals.items()}))
 daily=pd.DataFrame(records);daily.to_csv(R/'factor_ic_daily.csv.gz',index=False)
 for gran,group in [('year',['year','horizon','factor']),('month',['month','horizon','factor'])]:
  s=daily.groupby(group).agg(days=('date','count'),mean_spearman=('net_spearman','mean'),median_spearman=('net_spearman','median'),positive_days=('net_spearman',lambda v:int(v.gt(0).sum())),valid_days=('net_spearman','count'),mean_up_corr=('up2_rank_corr','mean'),mean_down_corr=('down2_rank_corr','mean')).reset_index();s.to_csv(D/f'factor_ic_{gran}.csv',index=False)
 meta={'source_sha256':sha(P/'samples.parquet'),'factors':F,'pooled_method':'Pearson pairwise nonmissing (stock-day weighted)','label_method':'daily Spearman: feature ranks vs net return ranks; feature rank vs binary +/-2% labels','daily_rows':len(daily),'candidate_days':candidate_days,'ic_days':daily.date.nunique(),'code_sha256':sha(Path(__file__))}
 (D/'correlation_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
