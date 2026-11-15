from pathlib import Path
import json,hashlib,duckdb,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/多年份训练与组合对照_20261113';Q=Path('/Volumes/ext/minite_k_data');parts=[]
for year,first,last in [('2024','2024-07-01','2024-12-31'),('2025','2025-07-01','2025-12-31'),('2026','2026-01-01','2026-06-30')]:
 p=R/'outputs/训练频率六个月对照_20261113' if year=='2024' else B;x=pd.read_pickle(p/'全部输入.pkl.gz');x=x[x.d0_date.between(first,last)].copy();parts.append(x)
x=pd.concat(parts,ignore_index=True);assert not x.duplicated(['d0_date','symbol']).any();assert not x.stk_code.str.endswith('.BJ').any();
selection=[]
for y in ['2024','2025','2026']:
 t=pd.read_csv(B/'账户'/y/'每日选股.csv',dtype={'symbol':str});selection.append(t[t.方法.eq('各取前二去重')][['d0_date','symbol']])
selection=pd.concat(selection,ignore_index=True);assert not selection.duplicated(['d0_date','symbol']).any();x=x.merge(selection.assign(组合入选=True),on=['d0_date','symbol'],how='left',validate='one_to_one');x['组合入选']=x.组合入选.fillna(False).astype(bool)
req=pd.concat([x[['qdc_symbol','d2_date']].rename(columns={'d2_date':'day'}),x[['qdc_symbol','d3_date']].rename(columns={'d3_date':'day'})]).dropna().drop_duplicates();req['day']=pd.to_datetime(req.day);c=duckdb.connect();c.execute('SET threads=3');c.execute("SET memory_limit='3GB'");c.register('req',req)
paths=[str(Q/'日线/2024.parquet'),str(Q/'日线/2025.parquet')]+[str(p) for p in sorted((Q/'日线').glob('2026-*.parquet')) if p.stem<='2026-07']
px=c.execute('''select r.qdc_symbol,r.day,p.close,p.volume from req r left join read_parquet(?) p on p.symbol=r.qdc_symbol and cast(epoch_ms(p.ts) as date)=r.day''',[paths]).df();assert not px.duplicated(['qdc_symbol','day']).any();px['day']=pd.to_datetime(px.day).dt.strftime('%Y-%m-%d')
for d in ['d2','d3']:
 z=px.rename(columns={'day':d+'_date','close':d+'_qdc_close','volume':d+'_qdc_volume'});x=x.merge(z,on=['qdc_symbol',d+'_date'],how='left',validate='many_to_one')
f=pd.read_parquet(Q/'复权因子/all.parquet');f['d']=f.d.astype(str);f=f[['symbol','d']];c.register('actions',f);c.register('samples',x[['qdc_symbol','d1_date','d3_date','symbol','d0_date']]);adj=c.execute('''select s.symbol,s.d0_date,count(a.d) as corporate_events from samples s left join actions a on a.symbol=s.qdc_symbol and a.d>s.d1_date and a.d<=s.d3_date group by 1,2''').df();x=x.merge(adj,on=['symbol','d0_date'],validate='one_to_one');
x['d2_qdc_match']=(x.d2_close-x.d2_qdc_close).abs().le(.011);ok=x.ret.notna() & x.d2_qdc_match & x.d3_qdc_close.gt(0)&x.d3_qdc_volume.gt(0)&x.corporate_events.eq(0);x['d3_ret']=(100*(x.d3_qdc_close*.999/(x.d1_open*1.001)-1)).where(ok)
x.to_pickle(P/'全部候选及结果.pkl.gz',compression='gzip');selection.to_csv(P/'组合原始入选.csv',index=False);audit={'股票日':len(x),'测试日':x.d0_date.nunique(),'组合选中股次':int(x.组合入选.sum()),'D2价格可算':int(x.ret.notna().sum()),'D2双源对上':int((x.ret.notna()&x.d2_qdc_match).sum()),'D3价格可算':int(x.d3_ret.notna().sum()),'D1后至D3除权事件':int(x.corporate_events.gt(0).sum()),'qdc日线':paths,'说明':'D3仅在D2价格双源匹配、D3有成交且D1后无复权事件时计算；不基于未来结果改变候选或排名。'};(P/'数据审计.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(audit,flush=True)
