from pathlib import Path
import sys,json,hashlib,pandas as pd
q=Path(sys.argv[1]);year=sys.argv[2];e=pd.read_pickle(q/(year+'_原始10_逐笔.pkl.gz'));e=e[e.action.isin(['D1开盘未知','路径未知'])][['day','symbol']].drop_duplicates();s=pd.read_csv(q/'行情请求.csv');s['code']=s.symbol.str[:6];e=e.merge(s[['code','symbol']].drop_duplicates(),left_on='symbol',right_on='code',validate='many_to_one').rename(columns={'symbol_y':'qdc_symbol'});old=q/'独立行情核验.csv';out=pd.read_csv(old) if old.exists() else pd.DataFrame(columns=['symbol','day','独立行情状态','来源','日文件SHA256']);bars=[]
for p in (q/'分钟').glob('*.pkl.gz'):
 if not p.name.startswith('.'):bars.append(pd.read_pickle(p))
minutes=pd.concat(bars,ignore_index=True) if bars else pd.DataFrame()
for r in e.itertuples():
 day=str(r.day);sym=r.qdc_symbol;f=Path('/Volumes/ext/tushare_market_data')/day[:4]/(day.replace('-','')+'.csv.gz');meta=f.with_suffix('').with_suffix('.json')
 if not f.exists() or not meta.exists():continue
 j=json.loads(meta.read_text());h=hashlib.sha256(f.read_bytes()).hexdigest();code=sym.replace('.XSHG','.SH').replace('.XSHE','.SZ')
 if h!=j.get('sha256') or code in j.get('listed_codes_without_minute_bars',[]):continue
 m=minutes[(minutes.symbol.eq(sym))&minutes.day.eq(day)];
 if len(m) and ((pd.to_numeric(m.volume,errors='coerce').fillna(0)>0)|(pd.to_numeric(m.amount,errors='coerce').fillna(0)>0)).any():continue
 x=pd.read_csv(f,usecols=['ts_code','vol','amount']);z=x[x.ts_code.eq(code)]
 if len(z) and ((z.vol>0)|(z.amount>0)).any():continue
 status='记录无成交' if len(z) else '不在Tushare当日日线活跃池';out=pd.concat([out,pd.DataFrame([{'symbol':sym,'day':day,'独立行情状态':status,'来源':'Tushare日线活跃池及分钟档','日文件SHA256':h}])],ignore_index=True);print('已核验无成交',sym,day,flush=True)
out.drop_duplicates(['symbol','day']).to_csv(old,index=False)
