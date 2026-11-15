from pathlib import Path
import sys,json,hashlib,pandas as pd
q=Path(sys.argv[1]);symbols=set(sys.argv[2:]);w=pd.read_csv(q/'行情请求.csv');w=w[w.symbol.str[:6].isin(symbols)][['symbol','day']].drop_duplicates();d=pd.read_pickle(q/'日线.pkl.gz');w=w.merge(d[['symbol','day']].drop_duplicates(),on=['symbol','day'],how='left',indicator=True);w=w[w._merge.eq('left_only')];positive=set()
for p in (q/'分钟').glob('*.pkl.gz'):
 if p.name.startswith('.'):continue
 x=pd.read_pickle(p);x=x[x.symbol.str[:6].isin(symbols)];x=x[(pd.to_numeric(x.volume,errors='coerce').fillna(0)>0)|(pd.to_numeric(x.amount,errors='coerce').fillna(0)>0)];positive.update(zip(x.symbol,x.day))
old=q/'独立行情核验.csv';out=pd.read_csv(old).to_dict('records') if old.exists() else []
for day,g in w.groupby('day'):
 f=Path('/Volumes/ext/tushare_market_data')/day[:4]/(day.replace('-','')+'.csv.gz');meta=f.with_suffix('').with_suffix('.json')
 if not f.exists() or not meta.exists():continue
 j=json.loads(meta.read_text());h=hashlib.sha256(f.read_bytes()).hexdigest()
 if h!=j.get('sha256'):continue
 wanted={s.replace('.XSHG','.SH').replace('.XSHE','.SZ'):s for s in g.symbol if (s,day) not in positive};wanted={k:v for k,v in wanted.items() if k not in j.get('listed_codes_without_minute_bars',[])}
 if not wanted:continue
 has=set();bad=set()
 for x in pd.read_csv(f,usecols=['ts_code','vol','amount'],chunksize=200000):
  z=x[x.ts_code.isin(wanted)];has.update(z.ts_code.unique());bad.update(z.loc[(z.vol>0)|(z.amount>0),'ts_code'].unique())
 for k,s in wanted.items():
  if k not in bad:out.append({'symbol':s,'day':day,'独立行情状态':'记录无成交' if k in has else '不在Tushare当日日线活跃池','来源':'Tushare日线活跃池及分钟档','日文件SHA256':h})
print('证据记录',len(out),flush=True);pd.DataFrame(out).drop_duplicates(['symbol','day']).to_csv(old,index=False)
