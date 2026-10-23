from pathlib import Path
import pandas as pd
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
source=Path('/data/cyris/work/b2_idle_extend_r77_20261022/replay.py').read_text().split('accounts=[]')[0]
source=source.replace("sell_clock='15:00' if mode=='close' or delayed else row['sell7_time']","sell_clock='09:31' if delayed else ('15:00' if mode=='close' else row['sell7_time'])")
source=source.replace("price=row['d3_close'] if delayed else row['d2_close'] if mode=='close' else row['sell7']","price=row['d3_open'] if delayed else row['d2_close'] if mode=='close' else row['sell7']")
exec(compile(source,'r77_replay_open_variant.py','exec'))
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
daily=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
s=s.merge(daily[['trade_date','stk_code','open']].rename(columns={'trade_date':'d3_date','open':'d3_open'}),on=['d3_date','stk_code'],validate='one_to_one')
assert len(s)==306
rows=[]
for label,frame in [('全期',s)]+[(str(year),g) for year,g in s.groupby('d0_year')]:
 start='2022-01-01' if label=='全期' else label+'-01-01'
 z,l,v=replay(frame,True,'close',start)
 rows.append(dict(范围=label,延期笔数=int(l.延期.sum()),**z))
 l.to_csv(R/f'{label}_轮空D3开盘假设_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
 v.to_csv(R/f'{label}_轮空D3开盘假设_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(rows).to_csv(R/'轮空112笔D3开盘账户.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(rows).to_string(index=False))
