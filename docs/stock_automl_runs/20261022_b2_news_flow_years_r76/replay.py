from pathlib import Path
import json,hashlib
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
P=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022')
D=Path('/data/cyris/work/b2_d2_intraday_r67_20261021')
s=pd.read_pickle(R/'joined_306.pkl')
p=pd.read_pickle(P/'predictions306.pkl').set_index('id')
cal=json.loads((D/'inputs/calendar.json').read_text())
fee=.001
policies={'baseline':np.ones(len(s),bool)}
for name in ['union_early_lr01','fit_D1_news','fit_D1_flow','fit_D1_news_flow','fit_sens_D1_q80','fit_sens_D1_flow_q80','fit_sens_D1_news_flow_q80','fit_econ_D1_news_flow_q80']:
 policies[name]=~p.loc[s.id,name].to_numpy(bool)
def replay(indices,keep,mode,start,end):
 frame=s.iloc[indices];mask=keep[indices]
 cash=1e6;peak=cash;hold={};events={};ledger=[];daily=[]
 for row in frame.loc[mask].to_dict('records'):
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
  t='15:00' if mode=='close' else row['sell7_time']
  events.setdefault(row['d2_date'],[]).append((t,1,row))
 for day in (d for d in cal if start<=d<=end):
  for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    cost=cash*(.5 if not hold else 1.)
    assert cost>0,(row['id'],day,cash,hold)
    shares=cost/((1+fee)*row['entry']);cash-=cost
    hold[row['id']]={'shares':shares,'cost':cost,'d1_close':row['d1_close']}
   else:
    lot=hold.pop(row['id'])
    price=row['d2_close'] if mode=='close' else row['sell7']
    proceeds=lot['shares']*price*(1-fee);cash+=proceeds
    ledger.append({'id':row['id'],'D0':row['d0_date'],'D1':row['d1_date'],'D2':row['d2_date'],'buy_cost':lot['cost'],'sell_proceeds':proceeds,'pnl':proceeds-lot['cost'],'return':proceeds/lot['cost']-1})
  nav=cash+sum(x['shares']*x['d1_close'] for x in hold.values())
  peak=max(peak,nav)
  daily.append({'date':day,'nav':nav,'cash':cash,'drawdown':1-nav/peak,'open_positions':len(hold)})
 assert not hold
 l=pd.DataFrame(ledger);v=pd.DataFrame(daily)
 assert np.isclose(v.nav.iloc[-1],1e6+l.pnl.sum(),rtol=0,atol=1e-6)
 return {'trade_count':len(l),'return':cash/1e6-1,'max_drawdown':v.drawdown.max(),'ending_nav':cash},l,v
annual=[];whole=[];pertrade=[];yearly_continuous=[]
for mode in ['close','morning7']:
 price=s.d2_close if mode=='close' else s.sell7
 net=price/s.entry*(1-fee)/(1+fee)-1
 for name,keep in policies.items():
  for year,g in s.groupby('d0_year',sort=True):
   ix=g.index.to_numpy()
   d1=g.d1_date.min();end=g.d2_date.max()
   z,l,v=replay(ix,keep,mode,year+'-01-01',end)
   canceled=~keep[ix];n=net.iloc[ix].to_numpy()
   row={'卖法':mode,'方案':name,'D0年':year,'分期':'训练内' if year<='2024' else '验证期' if year=='2025' else '已观察测试期','候选笔数':len(g),'过滤笔数':int(canceled.sum()),'买入笔数':int(keep[ix].sum()),'过滤盈利笔数':int((canceled&(n>0)).sum()),'放弃盈利元_每笔1万':float(n[canceled&(n>0)].sum()*1e4),'过滤亏损笔数':int((canceled&(n<0)).sum()),'避开亏损元_每笔1万':float(-n[canceled&(n<0)].sum()*1e4),'过滤净贡献元_每笔1万':float(-n[canceled].sum()*1e4),'账户独立重置收益率':z['return'],'账户独立重置最大回撤':z['max_drawdown'],'年末结清日':end,'剩余未核实退出数':int(s.loc[ix[keep[ix]],'seven_unverified' if mode=='morning7' else 'close_unverified'].sum()),'剩余D2开收跌停数':int(s.loc[ix[keep[ix]],'D2开收跌停'].sum()),'剩余连续跌停数':int(s.loc[ix[keep[ix]],'连续跌停'].sum())}
   stress=(g.d3_close-g.sell7)/g.entry*(1-fee)/(1+fee)
   row['过滤D3额外估值损失减少元_非成交']=float((-stress.to_numpy()*canceled*g.seven_unverified.to_numpy()).sum()*1e4) if mode=='morning7' else None
   annual.append(row)
   l.to_csv(R/f'{year}_{mode}_{name}_ledger.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
   v.to_csv(R/f'{year}_{mode}_{name}_daily.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
  ix=s.index.to_numpy()
  z,l,v=replay(ix,keep,mode,'2022-01-01',s.d2_date.max())
  canceled=~keep;n=net.to_numpy()
  whole.append({'卖法':mode,'方案':name,'候选笔数':len(s),'过滤笔数':int(canceled.sum()),'买入笔数':z['trade_count'],'账户全期连续收益率':z['return'],'账户全期最大回撤':z['max_drawdown'],'全期结清日':s.d2_date.max(),'放弃盈利元_每笔1万':float(n[canceled&(n>0)].sum()*1e4),'避开亏损元_每笔1万':float(-n[canceled&(n<0)].sum()*1e4),'过滤净贡献元_每笔1万':float(-n[canceled].sum()*1e4),'剩余未核实退出数':int(s.loc[keep,'seven_unverified' if mode=='morning7' else 'close_unverified'].sum())})
  l.to_csv(R/f'全期_{mode}_{name}_ledger.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
  v.to_csv(R/f'全期_{mode}_{name}_daily.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
  prev=1e6
  for yr,grp in v.groupby(v.date.str[:4]):
   end=grp.nav.iloc[-1]
   yearly_continuous.append({'卖法':mode,'方案':name,'自然年':yr,'年初账户元':prev,'年末账户元':end,'自然年收益率':end/prev-1,'年内最大回撤_承接全期峰值':grp.drawdown.max()})
   prev=end
  for i,r in s.iterrows():
   pertrade.append({'卖法':mode,'方案':name,'股票':r.stk_code,'D0':r.d0_date,'D1':r.d1_date,'D2':r.d2_date,'是否过滤':int(not keep[i]),'买入价':r.entry,'卖出价假设':float(price.iloc[i]),'原交易费后收益率':float(net.iloc[i]),'过滤净贡献元_每笔1万':float(-net.iloc[i]*1e4 if not keep[i] else 0),'卖出代理':int(r.seven_proxy) if mode=='morning7' else 0,'卖出未核实':int(r.seven_unverified if mode=='morning7' else r.close_unverified),'D2开收跌停':r['D2开收跌停'],'连续跌停':r['连续跌停']})
pd.DataFrame(annual).to_csv(R/'逐年独立重置_全部方案.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(whole).to_csv(R/'全期连续账户_全部方案.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(yearly_continuous).to_csv(R/'全期连续账户_逐自然年.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(pertrade).to_csv(R/'306笔_逐方案经济归因.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
# Reconcile 2026 one-year replay and all pre-existing R75 policy results exactly.
existing=pd.read_csv(P/'2026当前卖法_组合得失.csv').set_index('方案')
for x in annual:
 if x['卖法']=='morning7' and x['D0年']=='2026':
  e=existing.loc[x['方案']]
  assert abs(x['账户独立重置收益率']-e['账户收益率'])<1e-8,(x['方案'],'return')
  assert abs(x['账户独立重置最大回撤']-e['最大回撤'])<1e-8,(x['方案'],'drawdown')
  assert abs(x['过滤净贡献元_每笔1万']-e['净贡献元_每笔1万'])<1e-6,(x['方案'],'ticket')
  assert abs(x['放弃盈利元_每笔1万']-e['少赚元_每笔1万'])<1e-6,(x['方案'],'forgone')
  assert abs(x['避开亏损元_每笔1万']-e['避亏元_每笔1万'])<1e-6,(x['方案'],'avoided')
  assert x['剩余未核实退出数']==e['未核实退出整笔数'],(x['方案'],'unverified')
assert pd.DataFrame(annual).query("卖法=='morning7' and D0年=='2026'").shape[0]==len(policies)
(R/'verification.json').write_text(json.dumps({'cohort_year_counts':s.d0_year.value_counts().sort_index().to_dict(),'2026_reproduced_policy_count':len(policies),'year_accounts_reconciled':len(annual),'full_accounts_reconciled':len(whole),'data_prices':'306 complete minute days; 2026 exit minute and eight accounts match R73/R75','window':'D0 year cohort, each reset 1m; full single account reset 2022-01-01','not_actual_fills':'close-floor/zero-last-volume exits retained as unverified, no delayed positions'},ensure_ascii=False,indent=2))
print('2026 policies exact',len(policies),'accounts',len(annual),'full',len(whole))
print(pd.DataFrame(annual).query("卖法=='morning7' and 方案 in ['baseline','union_early_lr01','fit_D1_flow','fit_D1_news_flow','fit_sens_D1_flow_q80','fit_sens_D1_news_flow_q80']")[['D0年','方案','候选笔数','过滤笔数','放弃盈利元_每笔1万','避开亏损元_每笔1万','过滤净贡献元_每笔1万','账户独立重置收益率','账户独立重置最大回撤','剩余未核实退出数']].to_string(index=False))

