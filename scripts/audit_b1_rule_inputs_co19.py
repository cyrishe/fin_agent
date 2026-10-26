"""CO19 input coverage audit only; no return analysis or production writes."""
from pathlib import Path
import pandas as pd,numpy as np,json
root=Path(__file__).resolve().parents[1];r=root/'outputs/stock_automl/b1_history_20261016/run'
a=pd.read_csv(root/'outputs/stock_automl/b1_validation_co18_20261026/daily.csv',dtype={'stock':str});a=a[a.variant.str.startswith('A')].copy();codes=set(a.stock.dropna());cal=pd.read_csv(r/'calendar.csv').date.tolist()
parts=[]
for name in ['older_adjusted.csv.gz','daily.csv.gz']:
 for chunk in pd.read_csv(r/name,dtype={'symbol6':str},usecols=['date','symbol6','adjclose'],chunksize=100000):parts.append(chunk[chunk.symbol6.isin(codes)])
p=pd.concat(parts).drop_duplicates(['date','symbol6'],keep='last');series={code:g.set_index('date').adjclose for code,g in p.groupby('symbol6')}
fund_dir=root/'outputs/stock_automl/rsi_model_kp3_20261016/fundflow_source';ff=[];paths=[]
for path in sorted(fund_dir.glob('*.pkl.gz')):
 if '2025-12'<=path.stem[:7]<='2026-10':
  d=pd.read_pickle(path);d=d[d.stk_code.str[:6].isin(codes)].copy();d['symbol6']=d.stk_code.str[:6];d['date']=pd.to_datetime(d.trade_date).dt.strftime('%Y-%m-%d');ff.append(d);paths.append(path.name)
f=pd.concat(ff);f['valid']=np.isfinite(f.main_buy_value)&np.isfinite(f.main_sell_value)&((f.main_buy_value+f.main_sell_value)>0);fs=f.set_index(['symbol6','date']).valid
rows=[]
for row in a.itertuples():
 i=cal.index(row.day);ps=series.get(row.stock,pd.Series(dtype=float));rec={'date':row.day,'stock':row.stock,'executed':row.executed}
 for n in [3,5,10,20,60]:
  dates=cal[i-n:i];v=ps.reindex(dates).to_numpy(dtype=float);rec[f'prior{n}_prices']=bool(len(dates)==n and np.isfinite(v).all() and (v>0).all())
 for n in [1,5,20]:rec[f'prior{n}_funds']=bool(i>=n and all(fs.get((row.stock,d),False) for d in cal[i-n:i]))
 rows.append(rec)
z=pd.DataFrame(rows);out=dict(status='coverage_only_no_effect_backtest',rows=len(z),selected_rows=int(z.stock.notna().sum()),trades=int(z.executed.sum()),price_start=p.date.min(),price_end=p.date.max(),fund_files=paths,coverage={k:{'all':int(z[k].sum()),'executed':int(z.loc[z.executed,k].sum())} for k in z.columns if k.startswith('prior')},fund_vintage_verified=False)
folder=root/'docs/stock_automl_runs/20261026_b1_rules_co19';(folder/'coverage.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));z.to_csv(folder/'coverage.csv',index=False);print(json.dumps(out,ensure_ascii=False,indent=2))
