from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_d3_early_exit_r78_20261022')
s=pd.read_pickle(R/'joined91.pkl')
m=pd.read_csv(R/'84笔分钟预约卖出对照.csv')
st=pd.read_csv(R/'raw/st.csv.gz').fillna({'end_date':'9999-12-31'})
floors=[]
for x in s.itertuples():
 z=st[st.stk_code.eq(x.stk_code)&st.begin_date.le(x.d3_date)&st.end_date.gt(x.d3_date)]
 assert len(z)==1
 rate=.2 if x.symbol6.startswith(('30','68')) else .05 if z.iloc[0].st_type in ('S','Y') else .1
 floor=float((Decimal(str(x.preclose))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
 assert (abs(x.d3_close-floor)<.001)==(x.d3_limit_flag==2)
 floors.append(floor)
s['D3跌停价']=floors
s['D3开盘跌停']=abs(s.d3_open-s['D3跌停价'])<.001
s['D3开盘涨5以上']=s['D3开盘涨幅']>=.05
s['D3开盘涨7以上']=s['D3开盘涨幅']>=.07
s['开盘正涨']=s['D3开盘涨幅']>0
s['开盘涨5后收跌']=s['D3开盘涨5以上']&(s['D3收盘涨幅']<0)
s['D3开盘对D2涨幅']=s.d3_open/s.preclose-1
group=[]
for name,g in [('全部D2涨停',s),('D2轮空',s[s.idle]),('D2有新买入',s[~s.idle]),('D3继续涨停',s[s.consecutive_up]),('D3未继续涨停',s[~s.consecutive_up])]:
 group.append(dict(组别=name,n=len(g),D3开盘均值=g['D3开盘涨幅'].mean(),D3收盘均值=g['D3收盘涨幅'].mean(),D3开盘中位=g['D3开盘涨幅'].median(),D3收盘中位=g['D3收盘涨幅'].median(),D3开盘正涨=int(g.开盘正涨.sum()),D3开盘涨5以上=int(g['D3开盘涨5以上'].sum()),D3开盘涨7以上=int(g['D3开盘涨7以上'].sum()),D3开盘跌停=int(g['D3开盘跌停'].sum()),D3收盘跌停=int(g['D3收盘跌停'].sum()),开盘比收盘高=int((g['开盘卖相比收盘多拿百分点']>0).sum()),持平=int((g['开盘卖相比收盘多拿百分点']==0).sum()),开盘比收盘低=int((g['开盘卖相比收盘多拿百分点']<0).sum()),按买价开盘卖多拿百分点=g['开盘卖相比收盘多拿百分点'].mean()))
g=pd.DataFrame(group);g.to_csv(R/'91笔开收分组汇总.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(g.to_string(index=False))
yr=[]
for year,z in s.groupby('d0_year'):
 yr.append(dict(D0年=year,n=len(z),开盘均涨幅=z['D3开盘涨幅'].mean(),收盘均涨幅=z['D3收盘涨幅'].mean(),开盘多拿百分点=z['开盘卖相比收盘多拿百分点'].mean(),开盘比收盘高=int((z['开盘卖相比收盘多拿百分点']>0).sum()),D3开盘跌停=int(z['D3开盘跌停'].sum()),D3收盘跌停=int(z['D3收盘跌停'].sum()),分钟完整=int(z.分钟完整.sum())))
y=pd.DataFrame(yr);y.to_csv(R/'91笔逐年开收.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(y.to_string(index=False))
print('open>=5 and close<0',s['开盘涨5后收跌'].sum())
print('open>=7',s['D3开盘涨7以上'].sum(),'continued',int(s.loc[s['D3开盘涨7以上'],'consecutive_up'].sum()))
print('open floor and close off',int((s['D3开盘跌停']&~s['D3收盘跌停']).sum()))
# Fixed-time minute paths: same 84 cases; proxy fills and open positions marked at close.
out=[]
for (clock,yr),z in m.groupby(['预约卖出时点',m.D0.str[:4]]):
 out.append(dict(时点=clock,D0年=yr,n=len(z),平均费后收益_含未售估值=z['每笔费后收益或未售估值收益'].mean(),相比D3收盘平均多拿百分点=z['相比D3收盘价格差_百分点'].mean(),成交代理笔数=int(z.代理成交.sum()),未售估值笔数=int((~z.代理成交).sum())))
out=pd.DataFrame(out);out.to_csv(R/'84笔各年时点汇总.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print('minute by year 10:01')
print(out[out.时点=='10:01'].to_string(index=False))
for clock in ['09:31','09:35','09:41','10:01','11:30','13:01','14:56','15:00']:
 z=m[m.预约卖出时点==clock]
 sub=[]
 for k,g in z.groupby('D3收盘涨停'):
  sub.append((k,len(g),g['相比D3收盘价格差_百分点'].mean(),int(g.代理成交.sum())))
 print('clock',clock,sub)
s.to_csv(R/'91笔含开盘跌停.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
