from pathlib import Path
import pandas as pd,numpy as np,json,sys
R=Path('/data/cyris/work/b2_news_factor_r74_20261022');f=pd.read_pickle(R/'features709.pkl')
sys.path.insert(0,'/data/cyris/fin_agent_deploy/b2-20261015')
from scripts.replay_limit_rebound_cash_r23 import replay
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
raw=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/all_signals.pkl');raw['id']=raw.stk_code+'|'+raw.d0_date
current=pd.read_pickle('/data/cyris/work/b2_model_economics_r73_20261022/joined_98.pkl')
labels={'explicit_warning_5':'近5日有明确交易风险提示','repeated_warning_5':'近5日至少2天风险提示','specific_adverse_5':'近5日减持计划或处罚等具体负面','valuation_warning_5':'近5日公告正文提示高估值','theme_denial_5':'近5日公告正文澄清热点业务'}
summaries=[];details=[]
for cohort,base in [('V2全部306',f[f.v2_allowed]),('V2_2022_2024',f[f.v2_allowed&f.d0_date.str[:4].le('2024')]),('V2_2025',f[f.v2_allowed&f.d0_date.str.startswith('2025')]),('V2_2026',f[f.v2_allowed&f.d0_date.str.startswith('2026')])]:
 for rule,title in labels.items():
  selected=base[base[rule].eq(1)];net=selected.close_net
  summaries.append({'集合':cohort,'卖法':'原D2收盘假设','规则':title,'rule':rule,'基准笔数':len(base),'提示数':len(selected),'捕获连续跌停':int(selected.D2D3连续收盘跌停.sum()),'全部连续跌停':int(base.D2D3连续收盘跌停.sum()),'误报数_非连续跌停':int(len(selected)-selected.D2D3连续收盘跌停.sum()),'捕获D2开收跌停':int(selected.D2开收跌停.sum()),'原盈利数':int(net.gt(0).sum()),'原亏损数':int(net.lt(0).sum()),'放弃盈利元_每笔1万':float(net[net>0].sum()*10000),'避开亏损元_每笔1万':float(-net[net<0].sum()*10000),'取消净贡献元_每笔1万':float(-net.sum()*10000),'被提示连跌率':float(selected.D2D3连续收盘跌停.mean()) if len(selected) else None,'未提示连跌率':float(base[base[rule].eq(0)].D2D3连续收盘跌停.mean())})
def account(frame):
 end=current.d2_date.max();dates=[d for d in cal if '2026-01-01'<=d<=end];cash=1e6;peak=1e6;hold={};rows=[];ledger=[];events={}
 for r in frame.to_dict('records'):
  events.setdefault(r['d1_date'],[]).append((r['buy_clock'],0,r))
  events.setdefault(r['d2_date'],[]).append((r['sell7_time'],1,r))
 for date in dates:
  for tm,kind,r in sorted(events.get(date,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    assert len(hold)<=1
    cost=cash*(.5 if not hold else 1);shares=cost/1.001/r['entry'];cash-=cost
    hold[r['id']]={'cost':cost,'shares':shares,'d1_close':r['d1_close']}
   else:
    lot=hold.pop(r['id']);proceeds=lot['shares']*r['sell7']*.999;cash+=proceeds
    ledger.append({'id':r['id'],'cost':lot['cost'],'proceeds':proceeds,'pnl':proceeds-lot['cost'],'buy_date':r['d1_date'],'sell_date':date,'sell_time':tm})
  nav=cash+sum(x['shares']*x['d1_close'] for x in hold.values());peak=max(peak,nav);rows.append({'date':date,'cash':cash,'nav':nav,'drawdown':1-nav/peak})
 assert not hold
 l=pd.DataFrame(ledger);d=pd.DataFrame(rows);assert np.isclose(1e6+l.pnl.sum(),nav)
 return nav,float(d.drawdown.max()),d,l
base_nav,base_dd,base_daily,base_ledger=account(current)
original=pd.read_csv('/data/cyris/work/b2_model_economics_r73_20261022/morning7_baseline_daily.csv')
assert np.allclose(original.nav,base_daily.nav,atol=1e-6,rtol=0)
accounts=[];economic98=[]
for rule,title in {'baseline':'原V2放行98笔',**labels}.items():
 banned=set(f.loc[f[rule].eq(1),'id']) if rule!='baseline' else set()
 keep=~current.id.isin(banned);subset=current[keep];skip=current[~keep]
 nav,dd,d,l=account(subset)
 d.to_csv(R/(rule+'_daily.csv'),index=False,encoding='utf-8-sig',float_format='%.8f')
 l.to_csv(R/(rule+'_ledger.csv'),index=False,encoding='utf-8-sig',float_format='%.8f')
 net=skip.sell7/skip.entry*.999/1.001-1
 direct=-float(base_ledger[base_ledger.id.isin(banned)].pnl.sum())
 resize=float(l.pnl.sum()-base_ledger[~base_ledger.id.isin(banned)].pnl.sum())
 assert np.isclose(nav-base_nav,direct+resize,atol=.01)
 accounts.append({'rule':rule,'规则':title,'买入数':len(subset),'取消数':len(skip),'账户收益率':nav/1e6-1,'最大回撤':dd,'期末元':nav,'相对原账户变化元':nav-base_nav,'原仓位取消直接贡献元':direct,'后续仓位变化贡献元':resize,'捕获连续跌停':int(skip['D2D3连续收盘跌停'].sum()),'剩余连续跌停':int(subset['D2D3连续收盘跌停'].sum()),'剩余未核实收盘退出':int(subset.seven_exit_unverified.sum()),'放弃盈利元_每笔1万':float(net[net>0].sum()*10000),'避开亏损元_每笔1万':float(-net[net<0].sum()*10000),'取消净贡献元_每笔1万':float(-net.sum()*10000)})
 if rule!='baseline':
  for group,idx in [('全部提示',np.ones(len(skip),bool)),('提示且连续跌停',skip['D2D3连续收盘跌停'].eq(1)),('提示但非连续跌停',skip['D2D3连续收盘跌停'].eq(0))]:
   n=net[idx];economic98.append({'rule':rule,'规则':title,'分组':group,'笔数':len(n),'原盈利数':int(n.gt(0).sum()),'原亏损数':int(n.lt(0).sum()),'放弃盈利元_每笔1万':float(n[n>0].sum()*1e4),'避开亏损元_每笔1万':float(-n[n<0].sum()*1e4),'净贡献元_每笔1万':float(-n.sum()*1e4)})
  for r in current.to_dict('records'):
   val=r['sell7']/r['entry']*.999/1.001-1;cancel=r['id'] in banned
   details.append({'rule':rule,'规则':title,'股票':r['stk_code'],'D0':r['d0_date'],'取消买入':int(cancel),'连续跌停':int(r['D2D3连续收盘跌停']),'买价':r['entry'],'卖价假设':r['sell7'],'费后收益率':val,'取消净贡献元_每笔1万':-val*1e4 if cancel else 0,'收盘退出未核实':int(r['seven_exit_unverified'])})
pd.DataFrame(summaries).to_csv(R/'新闻因子_分年得失.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(accounts).to_csv(R/'2026当前卖法_账户对照.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(economic98).to_csv(R/'2026当前卖法_误报得失.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(details).to_csv(R/'2026逐笔因子动作.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
# Control for crude board-height imbalance descriptively, without causal claims.
strata=[]
for rule,title in labels.items():
 for binname,mask in [('首二板',f.board_streak_days.le(2)),('三板及以上',f.board_streak_days.ge(3))]:
  for flag in [0,1]:
   x=f[f.v2_allowed&mask&f[rule].eq(flag)];strata.append({'规则':title,'连板组':binname,'提示':flag,'笔数':len(x),'连续跌停':int(x.D2D3连续收盘跌停.sum()),'连续跌停率':float(x.D2D3连续收盘跌停.mean())})
pd.DataFrame(strata).to_csv(R/'连板高度分组_因子对照.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
print(pd.DataFrame(accounts)[['规则','取消数','捕获连续跌停','放弃盈利元_每笔1万','避开亏损元_每笔1万','取消净贡献元_每笔1万','账户收益率','最大回撤']].to_string(index=False))
print('No refit, no model selected, all predetermined factor rules shown; media excluded from economics due incomplete search coverage')

