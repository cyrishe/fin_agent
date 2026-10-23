from pathlib import Path
import hashlib,json
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_floor_countermeasure_r86_20261022')
W=Path('/data/cyris/work')
S=W/'b2_news_flow_years_r76_20261022'
P=W/'b2_news_flow_combo_r75_20261022'
N=W/'b2_news_factor_r74_20261022'
E=W/'b2_d1_floor_open_r85_20261022'
A=W/'b2_d1_floor_minutes_r84_20261022'
F=W/'b2_d2_floor_volume_r79_20261022'
s=pd.read_pickle(S/'joined_306.pkl')
p=pd.read_pickle(P/'predictions306.pkl').set_index('id')
n=pd.read_csv(N/'306入选_新闻因子.csv').set_index('id')
e=pd.read_csv(E/'306笔_买前五根开盘和四根收盘.csv')
e=e.set_index(e['股票']+'|'+e['D0选中日'])
a=pd.read_csv(A/'306笔D1开盘五分钟逐笔.csv')
a=a.set_index(a['股票']+'|'+a['D0选中日'])
f=pd.read_csv(F/'100笔D2触跌停_成交量逐笔.csv')
f['id']=f['股票']+'|'+f['D0选中日']
assert len(s)==len(p)==len(e)==len(a)==306 and len(f)==100 and f.id.nunique()==100
ids=s.id
p=p.loc[ids];n=n.loc[ids];e=e.loc[ids];a=a.loc[ids]
panel=pd.read_pickle(P/'panel306.pkl')
panel['id']=panel.stock+'|'+panel.d0_date.astype(str)
panel=panel.set_index('id').loc[ids]
assert p.index.tolist()==e.index.tolist()==a.index.tolist()==panel.index.tolist()==ids.tolist()
assert np.allclose(panel.entry.to_numpy(float),s.entry.to_numpy(float),atol=.005)
d=s.copy()
d['D2任意分钟触跌停']=ids.isin(f.id).astype(int)
d['D2收盘跌停']=a['D2收盘跌停'].to_numpy(int)
d['D2全天一价封死']=a['D2全天一价封死'].to_numpy(int)
d['D2D3连续收盘跌停']=a['D2D3连续收盘跌停'].to_numpy(int)
d['D1收盘跌停']=a['D1收盘跌停'].to_numpy(int)
d['D1全天一价封死']=a['D1全天一价封死'].to_numpy(int)
d['D1前四K全跌停']=e['前四根收盘全跌停'].to_numpy(int)
d['D0公告否认热点']=n['d0_5_theme_denial'].gt(0).to_numpy(int)
d['D1买前公告否认热点']=n['theme_denial_5'].to_numpy(int)
d['D1_Q75']=p['union_early_lr01'].to_numpy(int)
d['D1_Q80']=p['fit_sens_D1_q80'].to_numpy(int)
d['D1资金_Q80']=p['fit_sens_D1_flow_q80'].to_numpy(int)
d['D1公告资金_Q80']=p['fit_sens_D1_news_flow_q80'].to_numpy(int)
d['D2开盘价']=panel.d2_open.to_numpy(float)
d['D2跌停价']=panel.d2_floor.to_numpy(float)
d['D1收盘价']=s.d1_close.to_numpy(float)
fee_ratio=.999/1.001
d['收盘费后收益率']=s.d2_close.to_numpy(float)/s.entry.to_numpy(float)*fee_ratio-1
d['早7费后收益率']=s.sell7.to_numpy(float)/s.entry.to_numpy(float)*fee_ratio-1
assert [int(d[x].sum()) for x in ['D2任意分钟触跌停','D2收盘跌停','D2全天一价封死','D2D3连续收盘跌停','D1收盘跌停','D1全天一价封死','D1前四K全跌停','D0公告否认热点','D1买前公告否认热点','D1_Q75','D1_Q80']]==[100,52,14,20,18,5,5,7,9,97,71]
assert np.allclose(d['收盘费后收益率'].to_numpy()*100,a['原正式收盘卖法费后收益_pct'].to_numpy(),atol=1e-8)
D0=d['D0公告否认热点'].eq(1)
D1NEWS=d['D1买前公告否认热点'].eq(1)
Q75=d['D1_Q75'].eq(1)
Q80=d['D1_Q80'].eq(1)
FLOOR=d['D1前四K全跌停'].eq(1)
FLOW80=d['D1资金_Q80'].eq(1)
ALL80=d['D1公告资金_Q80'].eq(1)
flags={
 '基线_不加买前禁买':pd.Series(False,index=d.index),
 'D0_否认热点':D0,
 'D1_否认热点':D1NEWS,
 'D1_Q75':Q75,
 'D1_Q80':Q80,
 'D1前四K全跌停':FLOOR,
 'D1资金_Q80':FLOW80,
 'D1公告资金_Q80':ALL80,
 'D0否认或D1_Q75':D0|Q75,
 'D1否认或D1_Q75':D1NEWS|Q75,
 'D0否认或D1_Q80':D0|Q80,
 'D1否认或D1_Q80':D1NEWS|Q80,
 'D1_Q75或前四K全跌停':Q75|FLOOR,
 'D1_Q75或资金_Q80':Q75|FLOW80,
 'D1_Q75或D1否认或资金_Q80':Q75|D1NEWS|FLOW80,
 'D1_Q80或前四K全跌停':Q80|FLOOR,
 'D0否认或D1_Q80或前四K全跌停':D0|Q80|FLOOR
}
assert (Q75|FLOOR).equals(Q75)
def groupsum(g,mode):
 flag=g['禁买'].eq(1);cut=g[flag];remain=g[~flag]
 net=cut['收盘费后收益率' if mode=='close' else '早7费后收益率']
 return {'候选':len(g),'禁买':len(cut),'D2任意触板捕获':int(cut['D2任意分钟触跌停'].sum()),'D2任意触板剩余':int(remain['D2任意分钟触跌停'].sum()),
 '剩余触板率_pct':remain['D2任意分钟触跌停'].mean()*100 if len(remain) else None,
 'D2收盘跌停捕获':int(cut['D2收盘跌停'].sum()),'D2全天封死捕获':int(cut['D2全天一价封死'].sum()),
 'D2全天封死剩余':int(remain['D2全天一价封死'].sum()),'D2D3连续跌停捕获':int(cut['D2D3连续收盘跌停'].sum()),
 'D2D3连续跌停剩余':int(remain['D2D3连续收盘跌停'].sum()),'误拦盈利笔数':int((net>0).sum()),
 '避亏元_每笔1万':float(-net[net<0].sum()*1e4),'少赚元_每笔1万':float(net[net>0].sum()*1e4),
 '净得元_每笔1万':float(-net.sum()*1e4),'未核实卖出剩余':int(remain['close_unverified' if mode=='close' else 'seven_unverified'].sum())}
rows=[]
for name,flag in flags.items():
 d['禁买']=flag.astype(int)
 for mode in ['close','morning7']:
  for period,g in [('全部306',d),('2022-24',d[d.d0_year.isin(['2022','2023','2024'])]),('2025',d[d.d0_year.eq('2025')]),('2026',d[d.d0_year.eq('2026')])]:
   rows.append({'规则':name,'卖法':mode,'期间':period,**groupsum(g,mode)})
res=pd.DataFrame(rows);res.to_csv(R/'买前策略分期_跌停与经济得失.csv',index=False,encoding='utf-8-sig')
overlap=[]
names=['D0_否认热点','D1_否认热点','D1_Q75','D1_Q80','D1前四K全跌停','D1资金_Q80','D1公告资金_Q80']
for i,u in enumerate(names):
 for v in names[i+1:]:
  a1=flags[u];b1=flags[v];only_a=a1&~b1;only_b=b1&~a1;both=a1&b1
  overlap.append({'信号A':u,'信号B':v,'A笔数':int(a1.sum()),'B笔数':int(b1.sum()),'交集笔数':int(both.sum()),
                  '仅A笔数':int(only_a.sum()),'仅B笔数':int(only_b.sum()),
                  '仅A_触板':int(d.loc[only_a,'D2任意分钟触跌停'].sum()),'仅B_触板':int(d.loc[only_b,'D2任意分钟触跌停'].sum()),
                  '仅A_全天封死':int(d.loc[only_a,'D2全天一价封死'].sum()),'仅B_全天封死':int(d.loc[only_b,'D2全天一价封死'].sum()),
                  '仅B_收盘基线净得元_每笔1万':float(-d.loc[only_b,'收盘费后收益率'].sum()*1e4)})
pd.DataFrame(overlap).to_csv(R/'买前信号交叉和独有增量.csv',index=False,encoding='utf-8-sig')
cal=json.loads((W/'b2_d2_intraday_r67_20261021'/'inputs/calendar.json').read_text())
def replay(frame,keep,mode,start,end):
 cash=1e6;peak=cash;maxdd=0;hold={};events={}
 for row in frame.loc[keep].to_dict('records'):
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
  events.setdefault(row['d2_date'],[]).append(('15:00' if mode=='close' else row['sell7_time'],1,row))
 for day in (day for day in cal if start<=day<=end):
  for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not hold else 1.)
    shares=cost/(1.001*row['entry'])
    cash-=cost;hold[row['id']]={'shares':shares,'d1_close':row['d1_close']}
   else:
    lot=hold.pop(row['id'])
    cash+=lot['shares']*(row['d2_close'] if mode=='close' else row['sell7'])*.999
  nav=cash+sum(h['shares']*h['d1_close'] for h in hold.values())
  peak=max(peak,nav);maxdd=max(maxdd,1-nav/peak)
 assert not hold
 return cash/1e6-1,maxdd
ac=[]
for name,flag in flags.items():
 keep=(~flag).to_numpy(bool)
 for mode in ['close','morning7']:
  for yr,g in s.groupby('d0_year'):
   ix=g.index.to_numpy()
   ret,dd=replay(g,keep[ix],mode,str(yr)+'-01-01',g.d2_date.max())
   ac.append({'规则':name,'卖法':mode,'期间':str(yr),'买入笔数':int(keep[ix].sum()),'账户收益率_pct':ret*100,'最大回撤_pct':dd*100})
  ret,dd=replay(s,keep,mode,'2022-01-01',s.d2_date.max())
  ac.append({'规则':name,'卖法':mode,'期间':'2022-26连续','买入笔数':int(keep.sum()),'账户收益率_pct':ret*100,'最大回撤_pct':dd*100})
ac=pd.DataFrame(ac)
ref=pd.read_csv(S/'全期连续账户_全部方案.csv')
refmap={'基线_不加买前禁买':'baseline','D1_Q75':'union_early_lr01','D1_Q80':'fit_sens_D1_q80','D1资金_Q80':'fit_sens_D1_flow_q80','D1公告资金_Q80':'fit_sens_D1_news_flow_q80'}
for name,original in refmap.items():
 for mode in ['close','morning7']:
  got=ac[(ac.规则==name)&(ac.卖法==mode)&(ac.期间=='2022-26连续')].iloc[0]
  want=ref[(ref.方案==original)&(ref.卖法==mode)].iloc[0]
  assert abs(got['账户收益率_pct']/100-want['账户全期连续收益率'])<1e-9,(name,mode,'return')
  assert abs(got['最大回撤_pct']/100-want['账户全期最大回撤'])<1e-9,(name,mode,'dd')
assert ac[(ac.规则=='D1_Q75')]['账户收益率_pct'].tolist()==ac[(ac.规则=='D1_Q75或前四K全跌停')]['账户收益率_pct'].tolist()
ac.to_csv(R/'组合_年度与全期账户价格假设.csv',index=False,encoding='utf-8-sig')
# Bought on D1: end-of-day sell signal can act on D2 only; compare feasible non-floor open proxies with the favorable close baseline.
d1late=d[d['D1收盘跌停'].eq(1)].copy()
d1late['D2开盘高于跌停']=d1late['D2开盘价']>d1late['D2跌停价']+.005
d1late['开盘卖相对D2收盘_每笔1万元差额']=np.where(d1late['D2开盘高于跌停'],
 (d1late['D2开盘价']-d1late['d2_close'])/d1late.entry*fee_ratio*1e4,0.)
d1late[['id','d1_date','d2_date','D1收盘跌停','D1全天一价封死','D2开盘价','D2跌停价','d2_close','D2开盘高于跌停',
 'D2任意分钟触跌停','D2收盘跌停','D2全天一价封死','D2D3连续收盘跌停','开盘卖相对D2收盘_每笔1万元差额']].to_csv(R/'D1盘末跌停_次晨退出价格对照.csv',index=False,encoding='utf-8-sig')
floorids=set(f.id)
early=f.set_index('id')['上午7%卖出代理明确早于触板']
early=pd.to_numeric(early,errors='coerce').fillna(0).astype(int)
assert int(early.sum())==3
d['早7相对收盘_每笔1万元差额']=(d['早7费后收益率']-d['收盘费后收益率'])*1e4
post=[]
for period,g in [('全部306',d),('D2触跌停100',d[d.id.isin(floorids)]),('D2收盘跌停52',d[d['D2收盘跌停'].eq(1)]),('D2D3连跌20',d[d['D2D3连续收盘跌停'].eq(1)]),
                 ('D1盘末跌停18',d[d['D1收盘跌停'].eq(1)])]:
 post.append({'集合':period,'笔数':len(g),'早7价格触发笔数':int(g.trigger7.ne('').sum()),'早7比收盘价更好笔数':int((g['早7相对收盘_每笔1万元差额']>1e-8).sum()),
              '早7相对收盘等额净增元_每笔1万':float(g['早7相对收盘_每笔1万元差额'].sum()),
              'D2全天一价封死':int(g['D2全天一价封死'].sum())})
pd.DataFrame(post).to_csv(R/'买后_D1盘末与D2早卖对照.csv',index=False,encoding='utf-8-sig')
d.drop(columns=['禁买']).to_csv(R/'306笔_综合因子与基线.csv',index=False,encoding='utf-8-sig')
print('top close full')
print(res[(res.期间=='全部306')&(res.卖法=='close')][['规则','禁买','D2任意触板捕获','D2全天封死捕获','D2D3连续跌停捕获','误拦盈利笔数','净得元_每笔1万','未核实卖出剩余']].to_string(index=False))
print('overlap')
print(pd.DataFrame(overlap).to_string(index=False))
print('full accounts')
print(ac[ac.期间.eq('2022-26连续')][['规则','卖法','买入笔数','账户收益率_pct','最大回撤_pct']].to_string(index=False))
print('D1 late',len(d1late),'D2 open nonfloor',int(d1late['D2开盘高于跌停'].sum()),'proxy gain',d1late['开盘卖相对D2收盘_每笔1万元差额'].sum())
print('D2 post');print(pd.DataFrame(post).to_string(index=False))
