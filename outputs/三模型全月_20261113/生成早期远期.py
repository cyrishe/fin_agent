from pathlib import Path
import pandas as pd,duckdb,json
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;w=pd.read_pickle(P/'目标股票日.pkl.gz');out=[];con=duckdb.connect();con.execute('SET threads=3');con.execute("SET memory_limit='3GB'")
for year,g in w.groupby(w.d0_date.str[:4]):
 y=int(year);files=[]
 for k in [y-1,y]:
  f=Path('/Volumes/ext/minite_k_data/日线')/f'{k}.parquet'
  files.extend([f] if f.exists() else sorted(Path('/Volumes/ext/minite_k_data/日线').glob(f'{k}-??.parquet')))
 con.register('wanted',g[['qdc_symbol','d0_date']].drop_duplicates());expr=[]
 for n in [20,60]:
  frame=f'PARTITION BY symbol ORDER BY ts ROWS BETWEEN {n} PRECEDING AND 1 PRECEDING'
  expr += [f'CASE WHEN count(ac) OVER({frame})={n} THEN lag(ac,1) OVER s/nullif(lag(ac,{n+1}) OVER s,0)-1 END AS prior_return_{n}',f'CASE WHEN count(ac) OVER({frame})={n} THEN stddev_samp(lr) OVER({frame}) END AS prior_volatility_{n}',f'CASE WHEN count(ac) OVER({frame})={n} THEN av/nullif(avg(av) OVER({frame}),0) END AS d0_volume_prior{n}',f'CASE WHEN count(ac) OVER({frame})={n} THEN amount/nullif(avg(amount) OVER({frame}),0) END AS d0_amount_prior{n}',f'CASE WHEN count(ac) OVER({frame})={n} THEN ac/nullif(avg(ac) OVER({frame}),0)-1 END AS d0_vs_ma{n}',f'CASE WHEN count(ac) OVER({frame})={n} THEN (ac-min(al) OVER({frame}))/nullif(max(ah) OVER({frame})-min(al) OVER({frame}),0) END AS d0_position_{n}']
 sql='''WITH raw AS (SELECT d.*,close*f.hfq_factor ac,high*f.hfq_factor ah,low*f.hfq_factor al,volume/nullif(f.hfq_factor,0) av,CAST(to_timestamp((ts+28800000)/1000) AS DATE) date FROM read_parquet(?) d ASOF LEFT JOIN read_parquet('/Volumes/ext/minite_k_data/复权因子/all.parquet') f ON d.symbol=f.symbol AND CAST(to_timestamp((ts+28800000)/1000) AS DATE)>=f.d), r AS (SELECT *,ln(ac/nullif(lag(ac) OVER s,0)) lr FROM raw WINDOW s AS(PARTITION BY symbol ORDER BY ts)), features AS MATERIALIZED (SELECT symbol,date,'''+','.join(expr)+''' FROM r WINDOW s AS(PARTITION BY symbol ORDER BY ts)) SELECT f.* EXCLUDE(symbol,date),w.qdc_symbol,w.d0_date FROM features f JOIN wanted w ON f.symbol=w.qdc_symbol AND f.date=CAST(w.d0_date AS DATE)'''
 p=con.execute(sql,[[str(f) for f in files]]).df();out.append(p);print(year,'目标',len(g),'已匹配',len(p),flush=True)
con.close();z=pd.concat(out,ignore_index=True);assert not z.duplicated(['qdc_symbol','d0_date']).any();cols=[c for c in z if c not in ['qdc_symbol','d0_date']];z.to_pickle(P/'远期形态因子.pkl.gz',compression='gzip');(P/'远期字段.json').write_text(json.dumps(cols,ensure_ascii=False,indent=2));print('完成',len(z),len(cols),flush=True)
