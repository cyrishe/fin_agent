"""Daily within-candidate-pool Spearman feature correlations."""
from pathlib import Path
import json
import numpy as np,pandas as pd
from scripts.build_low_base_co26 import CORE,EXTRA
from scripts.research_gentle_rise_gr1 import sha
P=Path('outputs/stock_automl/low_base_ml_co26_20261027');R=Path('outputs/stock_automl/low_base_rf_co27_20261027');D=Path('docs/stock_automl_runs/20261027_low_base_rf_co27');F=CORE+EXTRA

def main():
 s=pd.read_parquet(P/'samples.parquet',columns=['date',*F]);records=[]
 for date,g in s.groupby('date',sort=True):
  if len(g)<30:continue
  corr=g[F].corr(method='spearman',min_periods=30)
  for i,f in enumerate(F):
   for j in range(i+1,len(F)):records.append((date,f,F[j],corr.iat[i,j]))
 out=pd.DataFrame(records,columns=['date','factor1','factor2','spearman']);out['year']=out.date.str[:4];out.to_csv(R/'factor_pair_daily.csv.gz',index=False)
 summary=out.groupby(['year','factor1','factor2']).spearman.agg(days='count',mean='mean',median='median',positive_days=lambda x:int(x.gt(0).sum())).reset_index();summary.to_csv(D/'factor_pair_daily_summary.csv',index=False)
 m={'source_sha256':sha(P/'samples.parquet'),'days':int(out.date.nunique()),'rows':len(out),'method':'Daily within-L candidate-pool Spearman, then date-equal mean; pairwise nonmissing, minimum 30 stocks','code_sha256':sha(Path(__file__))};(D/'factor_pair_daily_manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(m,indent=2))
if __name__=='__main__':main()
