import sys,json
from pathlib import Path
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_idle_extend_r77_20261022')
M=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
records={x['id']:x for x in json.loads((M/'minute_source_manifest.json').read_text())}
s=pd.read_csv(R/'306笔逐笔对照.csv',dtype={'股票':str})
s=s.merge(pd.read_pickle(M/'joined_306.pkl')[['id','d1_close']],on='id',validate='one_to_one')
sys.path.insert(0,'/data/cyris/work/b2_d2_intraday_r67_20261021')
import experiment as old
rows=[]
for row in s[s.轮空].itertuples(index=False):
    src=records[row.id]['source']
    p=Path(src['path'])
    b=old.extract_archive(p,row.d2_date,row.股票) if src['type']=='archive' else pd.read_pickle(p) if p.suffix=='.pkl' else pd.read_csv(p)
    b['time']=pd.to_datetime(b.time)
    b=b.sort_values('time').reset_index(drop=True)
    assert len(b)==240 and b.time.dt.strftime('%H:%M').iloc[9]=='09:40'
    q=b.iloc[9]
    morning=b.iloc[:10]
    rows.append(dict(id=row.id,股票=row.股票,D0=row.d0_date,D2=row.d2_date,D3=row.d3_date,涨幅0940=float(q.close/row.d1_close-1),上午前10分钟最高涨幅=float(morning.high.max()/row.d1_close-1),近十分钟净涨幅=float(q.close/b.open.iloc[0]-1),D2收盘涨停=bool(row.d2_limit_up),D3收盘涨停=bool(row.d3_limit_up),连续涨停=bool(row.consecutive_up),多持一日差_百分点=row.多持一日差_百分点,D3收盘跌停=bool(row.D3卖出未核实_跌停或零日量)))
f=pd.DataFrame(rows)
f['0940涨超7%']=f.涨幅0940>.07
f['0940已涨停']=f.涨幅0940>.095
f.to_csv(R/'轮空0940已知特征.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
summary=[]
for key,g in f.groupby('0940涨超7%'):
    summary.append(dict(早盘涨超7=key,n=len(g),D2收盘涨停=int(g.D2收盘涨停.sum()),D3收盘涨停=int(g.D3收盘涨停.sum()),连续涨停=int(g.连续涨停.sum()),D3收盘跌停=int(g.D3收盘跌停.sum()),多持一日平均差_百分点=g.多持一日差_百分点.mean(),每笔1万元合计差元=g.多持一日差_百分点.sum()*100))
pd.DataFrame(summary).to_csv(R/'轮空0940分组.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
year=[]
for (yr,flag),g in f.groupby([f.D0.str[:4],'0940涨超7%']):
    year.append(dict(D0年=yr,早盘涨超7=flag,n=len(g),连续涨停=int(g.连续涨停.sum()),D3收盘跌停=int(g.D3收盘跌停.sum()),多持一日平均差_百分点=g.多持一日差_百分点.mean(),每笔1万元合计差元=g.多持一日差_百分点.sum()*100))
pd.DataFrame(year).to_csv(R/'轮空0940逐年.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(summary).to_string(index=False));print(pd.DataFrame(year).to_string(index=False))
