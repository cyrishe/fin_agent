from pathlib import Path
import sys,hashlib
import pandas as pd,numpy as np
sys.path.insert(0,'/data/cyris/work/b2_d2_intraday_r67_20261021')
import experiment as old
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
ledger=pd.read_csv(R/'全期_1455持有_分钟成交代理_账本.csv')
entries=pd.read_pickle('/data/cyris/work/b2_news_flow_years_r76_20261022/joined_306.pkl')[['id','stk_code','entry','d3_date']]
ledger=ledger.merge(entries,on='id',validate='one_to_one')
source=pd.read_csv(R/'分钟来源及核验.csv').set_index('id')
fp=pd.read_csv(R/'2笔1455误报D3分钟核验.csv').set_index('id')
m=pd.read_csv(R/'84笔分钟预约卖出对照.csv')
m=m[m.预约卖出时点=='09:31'].set_index('id')
rows=[]
for x in ledger[ledger.sell_proxy_kind=='D3_first_eligible_minute'].itertuples():
 if x.id in source.index:
  p=Path(source.loc[x.id,'path']);clock=m.loc[x.id,'实际代理成交时点']
 else:
  p=Path(fp.loc[x.id,'source']);clock=fp.loc[x.id,'first_eligible_clock']
 b=pd.read_pickle(p) if p.suffix=='.pkl' else old.extract_archive(p,x.d3_date,x.stk_code[:6])
 if 'symbol' in b.columns:b=b[b.symbol.astype(str).str[:6].eq(x.stk_code[:6])]
 b['time']=pd.to_datetime(b.time)
 q=b[b.time.dt.strftime('%H:%M').eq(clock)].iloc[0]
 shares=x.buy_cost/(x.entry*1.001)
 rows.append(dict(id=x.id,D3=x.d3_date,bar_end=clock,shares_to_sell=shares,bar_volume_shares=q.volume,participation=shares/q.volume,sell_price_proxy=x.sell_price_proxy,buy_cost=x.buy_cost,source=str(p)))
z=pd.DataFrame(rows)
z.to_csv(R/'D3首个可卖分钟容量.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print('n',len(z),'participation quantiles',z.participation.quantile([.5,.75,.9,.95,1]).to_dict())
print('count >.05',sum(z.participation>.05),'>.1',sum(z.participation>.1),'>1',sum(z.participation>1))
print(z.sort_values('participation',ascending=False).head(10)[['id','D3','bar_end','shares_to_sell','bar_volume_shares','participation','buy_cost']].to_string(index=False))
