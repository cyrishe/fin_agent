from pathlib import Path
import pandas as pd
R=Path('/data/cyris/work/b2_idle_extend_r77_20261022')
source=(R/'replay.py').read_text().split('accounts=[]')[0]
exec(compile(source,str(R/'replay.py'),'exec'))
morning=pd.read_csv(R/'轮空0940已知特征.csv')
ids=set(morning.loc[morning['0940涨超7%'],'id'])
assert len(ids)==33
select=s.copy()
select['轮空']=select['轮空']&select.id.isin(ids)
rows=[]
for label,frame in [('全期',select)]+[(str(year),g) for year,g in select.groupby('d0_year')]:
    start='2022-01-01' if label=='全期' else label+'-01-01'
    for mode in ['close','morning7']:
        z,l,v=replay(frame,True,mode,start)
        rows.append(dict(范围=label,基线卖法=mode,规则='轮空且09:40涨超7%延到D3',延期笔数=int(l.延期.sum()),**z,D3未核实卖出=int(l.D3卖出未核实.sum())))
        l.to_csv(R/f'{label}_{mode}_0940筛选延期_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
        v.to_csv(R/f'{label}_{mode}_0940筛选延期_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(rows).to_csv(R/'0940筛选账户对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(rows).to_string(index=False))
