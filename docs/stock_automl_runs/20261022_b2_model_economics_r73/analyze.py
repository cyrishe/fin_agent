from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import json,hashlib,sys
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_model_economics_r73_20261022')
S=Path('/data/cyris/work/b2_d2_limit_follow_r69_20261021')
M=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
sys.path.insert(0,'/data/cyris/fin_agent_deploy/b2-20261015')
from scripts.replay_limit_rebound_cash_r23 import replay
FEE=.001
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
s=pd.read_pickle(S/'all_signals.pkl')
s=s[s.v2_allowed & s.d0_date.str.startswith('2026')].sort_values('d0_date').reset_index(drop=True)
p=pd.read_csv(M/'统一三分类平衡逐笔预测.csv',dtype={'股票':str})
binary=pd.read_csv(M/'逐笔冻结预测_709候选.csv',dtype={'股票':str})
s=s.merge(p,left_on=['stk_code','d0_date'],right_on=['股票','D0选中日'],validate='one_to_one')
s=s.merge(binary[['股票','D0选中日','crash_alert','strong_alert']],on=['股票','D0选中日'],validate='one_to_one')
assert len(s)==98 and sum(s['真实类别']=='急跌')==21 and sum(s['预测类别_实验']=='急跌')==30
s['id']=s.stk_code+'|'+s.d0_date
s['buy_clock']=(pd.to_datetime('2000-01-01 '+s.entry_time)-pd.Timedelta(minutes=1)).dt.strftime('%H:%M')
prior=pd.read_csv(R/'prior_fourK_predictions.csv',dtype={'symbol':str})
prior=prior[prior.candidate.eq('union_early_lr01') & prior.flagged.eq(1)]
prior_ids=set(prior.symbol.str.zfill(6)+'|'+prior.D0)
s['prior41']=[str(r.symbol).zfill(6)+'|'+r.d0_date in prior_ids for r in s.itertuples()]
assert s.prior41.sum()==41
minute_manifest=[];exit_rows=[]
for r in s.itertuples():
 fn=R/'minutes'/f'{r.d2_date}_{r.symbol}.csv.gz'
 b=pd.read_csv(fn);b['time']=pd.to_datetime(b.time);b=b.sort_values('time').reset_index(drop=True)
 assert len(b)==240 and not b.time.duplicated().any(),r.id
 for c in ['open','high','low','close','volume']:b[c]=pd.to_numeric(b[c])
 assert abs(b.iloc[-1].close-r.d2_close)<.001 and abs(b.iloc[0].open-float(pd.read_pickle(S/'daily.pkl').set_index(['trade_date','stk_code']).loc[(r.d2_date,r.stk_code),'open']))<.001
 rate=.2 if str(r.symbol).startswith(('30','68')) else .05 if r.d2_st in ['S','Y'] else .1
 floor=float((Decimal(str(r.d1_close))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
 hit=b[(b.time.dt.strftime('%H:%M')<='11:30') & (b.high/r.d1_close-1>.07)]
 trigger='';sell_time='15:00';sell=float(r.d2_close);proxy=False
 if len(hit):
  ix=int(hit.index[0]);trigger=hit.iloc[0].time.strftime('%H:%M')
  after=b.iloc[ix+1:];eligible=after[(after.open>floor+.001)&(after.volume>0)]
  if len(eligible):
   q=eligible.iloc[0];sell=float(q.open);sell_time=(q.time-pd.Timedelta(minutes=1)).strftime('%H:%M');proxy=True
 close_unverified=bool(r.d2_close<=floor+.001 or b.iloc[-1].volume<=0)
 exit_rows.append(dict(id=r.id,trigger_7=trigger,sell7=sell,sell7_time=sell_time,seven_proxy=proxy,
  close_floor=bool(r.d2_close<=floor+.001),close_exit_unverified=close_unverified,
  seven_exit_unverified=bool(not proxy and close_unverified),all_day_locked=bool(b.high.max()<=floor+.001)))
 minute_manifest.append({'file':str(fn),'sha256':hashlib.sha256(fn.read_bytes()).hexdigest()})
s=s.merge(pd.DataFrame(exit_rows),on='id',validate='one_to_one')
policies={
 'baseline':('原V2候选全买',np.ones(len(s),bool)),
 'skip_unified_crash':('跳过统一模型急跌',~s['预测类别_实验'].eq('急跌')),
 'only_unified_strong':('只买统一模型大涨',s['预测类别_实验'].eq('持续大涨')),
 'skip_binary_crash':('跳过单独急跌高分',~s.crash_alert.eq(1)),
 'only_binary_strong':('只买单独大涨高分',s.strong_alert.eq(1)),
 'skip_prior41':('跳过既有四K并集模型',~s.prior41),
}
def run_account(frame,mode):
 dates=[x for x in cal if '2026-01-01'<=x<=s.d2_date.max()]
 cash=1e6;peak=1e6;prev=1e6;holding={};ledger=[];daily=[]
 events={}
 for row in frame.to_dict('records'):
  events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,'buy',row))
  selltime='15:00' if mode=='close' else row['sell7_time']
  events.setdefault(row['d2_date'],[]).append((selltime,1,'sell',row))
 for date in dates:
  fees=0.;buycost=0.;sales=0.
  for clock,order,action,row in sorted(events.get(date,[]),key=lambda x:(x[0],x[1])):
   if action=='buy':
    assert len(holding)<=1
    cost=cash*(.5 if not holding else 1.)
    assert cost>0
    shares=cost/(1+FEE)/row['entry'];buyfee=cost-cost/(1+FEE)
    lot={'id':row['id'],'symbol':row['stk_code'],'d0':row['d0_date'],'buy_date':date,'buy_time':clock,
         'cost':cost,'shares':shares,'buy':row['entry'],'buy_fee':buyfee,'d1_close':row['d1_close']}
    holding[row['id']]=lot;cash-=cost;fees+=buyfee;buycost+=cost
   else:
    lot=holding.pop(row['id']);price=row['d2_close'] if mode=='close' else row['sell7']
    gross=lot['shares']*price;sellfee=gross*FEE;proceeds=gross-sellfee;cash+=proceeds;fees+=sellfee;sales+=proceeds
    lot.update(sell_date=date,sell_time=clock,sell=price,sell_fee=sellfee,proceeds=proceeds,pnl=proceeds-lot['cost'],net=proceeds/lot['cost']-1)
    ledger.append(lot)
  value=sum(x['shares']*x['d1_close'] for x in holding.values());nav=cash+value;peak=max(peak,nav)
  daily.append({'date':date,'cash':cash,'value':value,'nav':nav,'return':nav/prev-1,'drawdown':1-nav/peak,'fees':fees,'buy':buycost,'sales':sales,'positions':len(holding)})
  prev=nav
 assert not holding
 l=pd.DataFrame(ledger);n=pd.DataFrame(daily)
 assert np.isclose(l.pnl.sum()+1e6,nav,atol=1e-7)
 z={'entries':len(l),'final_nav':float(nav),'return':float(nav/1e6-1),'max_drawdown':float(n.drawdown.max()),'fees':float(n.fees.sum()),'wins':int(l.net.gt(0).sum()),'win_rate':float(l.net.gt(0).mean())}
 return z,l,n
summaries=[];attribution=[];details=[];groups=[];monthly=[];verification=[]
for mode in ['close','morning7']:
 mode_label='D2收盘价假设' if mode=='close' else '上午7%触发后下一分钟开盘代理'
 prices=s.d2_close if mode=='close' else s.sell7
 net=prices/s.entry*(1-FEE)/(1+FEE)-1
 base_result,base_ledger,base_daily=run_account(s,mode)
 if mode=='close':
  assert abs(base_result['final_nav']-2428636.34)<.01,base_result
 for pid,(name,keep) in policies.items():
  keep=np.asarray(keep,bool);removed=~keep
  z,l,n=run_account(s.loc[keep],mode)
  if mode=='close':
   ref,_,daily,ledger,excluded=replay(s.loc[keep],cal,start='2026-01-01',end=s.d2_date.max(),fee_rate=FEE)
   assert not excluded and ref['pending']==0
   assert np.allclose(n.nav,daily.nav,atol=1e-7,rtol=0)
   verification.append({'policy':pid,'daily_nav_max_delta':float(np.abs(n.nav.to_numpy()-daily.nav.to_numpy()).max())})
  baseline_pnl=base_ledger.set_index('id').pnl
  removed_ids=set(s.loc[removed,'id'])
  direct=-float(base_ledger.loc[base_ledger.id.isin(removed_ids),'pnl'].sum())
  resizing=float(l.pnl.sum()-base_ledger.loc[~base_ledger.id.isin(removed_ids),'pnl'].sum())
  delta=z['final_nav']-base_result['final_nav'];assert np.isclose(delta,direct+resizing,atol=1e-7)
  unresolved=s.close_exit_unverified if mode=='close' else s.seven_exit_unverified
  sums={'mode':mode,'卖法':mode_label,'policy':pid,'方案':name,'买入笔数':int(keep.sum()),'取消笔数':int(removed.sum()),
    '账户收益率':z['return'],'最大回撤':z['max_drawdown'],'期末元':z['final_nav'],'相对基准账户变化元':delta,
    '原仓位取消直接贡献元':direct,'后续仓位变化贡献元':resizing,
    '每笔1万元保留净盈亏元':float(net[keep].sum()*1e4),'每笔1万元净改善元':float(-net[removed].sum()*1e4),
    '原盈利取消数':int((removed&net.gt(0)).sum()),'放弃盈利元_每笔1万':float(net[removed&net.gt(0)].sum()*1e4),
    '原亏损取消数':int((removed&net.lt(0)).sum()),'避开亏损元_每笔1万':float(-net[removed&net.lt(0)].sum()*1e4),
    '保留胜率':float(net[keep].gt(0).mean()),'保留笔均收益率':float(net[keep].mean()),
    '收盘封死或无量成交未核实数':int((keep&unresolved).sum()),
    '保留D2开收跌停数':int(s.loc[keep,'D2开收跌停'].sum()),'保留D2D3连续收盘跌停数':int(s.loc[keep,'D2D3连续收盘跌停'].sum())}
  summaries.append(sums)
  for label,mask in [('取消全部',removed),('取消且真实急跌',removed&s['真实类别'].eq('急跌')),('取消但非急跌',removed&~s['真实类别'].eq('急跌'))]:
   mask=np.asarray(mask,bool)
   attribution.append({'mode':mode,'policy':pid,'方案':name,'分组':label,'笔数':int(mask.sum()),'盈利笔数':int((mask&net.gt(0)).sum()),'亏损笔数':int((mask&net.lt(0)).sum()),
    '放弃盈利元_每笔1万':float(net[mask&net.gt(0)].sum()*1e4),'避开亏损元_每笔1万':float(-net[mask&net.lt(0)].sum()*1e4),'取消净贡献元_每笔1万':float(-net[mask].sum()*1e4)})
  for i,row in s.iterrows():
   details.append({'mode':mode,'policy':pid,'方案':name,'股票':row.stk_code,'D0':row.d0_date,'D1':row.d1_date,'D2':row.d2_date,
    '模型预测':row['预测类别_实验'],'真实标签':row['真实类别'],'是否买入':int(keep[i]),'买价':row.entry,'卖价假设':prices.iloc[i],
    '费后收益率':net.iloc[i],'原每笔1万盈亏元':net.iloc[i]*1e4,'取消净影响元_每笔1万':0. if keep[i] else -net.iloc[i]*1e4,
    '原账户仓位元':float(base_ledger.set_index('id').loc[row.id,'cost']),
    '原账户该笔盈亏元':float(baseline_pnl[row.id]),'D2开收跌停':row['D2开收跌停'],'D2D3连跌':row['D2D3连续收盘跌停'],
    '7触发分钟':row.trigger_7,'7退出时刻':row.sell7_time,'成交未核实':int(unresolved.iloc[i])})
  l.to_csv(R/f'{mode}_{pid}_ledger.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
  n.to_csv(R/f'{mode}_{pid}_daily.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
  prev=1e6
  for month,g in n.groupby(n.date.str[:7],sort=True):
   endnav=g.nav.iloc[-1]
   monthly.append({'mode':mode,'policy':pid,'方案':name,'月份':month,'月初元':prev,'月末元':endnav,'月收益率':endnav/prev-1})
   prev=endnav
 for predlabel in ['急跌','中间状态','持续大涨']:
  mask=s['预测类别_实验'].eq(predlabel)
  groups.append({'mode':mode,'预测类':predlabel,'笔数':int(mask.sum()),'真实急跌':int((mask&s['真实类别'].eq('急跌')).sum()),'真实大涨':int((mask&s['真实类别'].eq('持续大涨')).sum()),'盈利数':int(net[mask].gt(0).sum()),'亏损数':int(net[mask].lt(0).sum()),'净盈亏元_每笔1万':float(net[mask].sum()*1e4),'笔均收益率':float(net[mask].mean())})
for fn,rows in [('方案收益对照.csv',summaries),('取消买入得失归因.csv',attribution),('逐笔经济影响.csv',details),('预测类别实际盈亏.csv',groups),('逐月账户对照.csv',monthly)]:
 pd.DataFrame(rows).to_csv(R/fn,index=False,encoding='utf-8-sig',float_format='%.8f')
s.to_pickle(R/'joined_98.pkl')
meta={'scope':'2026 D0 V2 allowed 98 signals, through D2 2026-10-09; reset 1m at 2026-01-01',
 'frozen_predictions':'R72 unified_balanced_hgb plus two R72 binary models; previous union41 as fixed comparator','new_training':False,
 'cohort':{'all120_crash':26,'all120_predicted_crash':39,'all120_caught':9,'v2_98_crash':21,'v2_98_predicted_crash':30,'v2_98_caught':9,'v2_false_positive':21},
 'minute7_hits':int(s.trigger_7.ne('').sum()),'close_unverified':int(s.close_exit_unverified.sum()),'seven_unverified':int(s.seven_exit_unverified.sum()),
 'assumptions':['Both sell modes assume full fills; residual close floor/no volume exits explicitly counted as UNVERIFIED, not actual realized profits.',
 '7%: completed AM minute high / D1 close - 1 strictly > .07; next later positive-volume minute open above floor, never fill on past trigger high; otherwise D2 close.',
 'Chronological cash: sell before later buy adds available cash; same clock buy before sell. At purchase flat=half cash, one holding=all cash. Original shadow month gate and candidates frozen; canceled candidates not replaced.',
 'Two-sided fees 0.1%; fractional shares; monthly pause not recomputed; no slippage or market impact. Strong label ends D3, actual evaluation sells D2.'],
 'verification':verification,'source_hashes':{},'minute_sources':minute_manifest}
for path in [S/'all_signals.pkl',M/'统一三分类平衡逐笔预测.csv',M/'逐笔冻结预测_709候选.csv',R/'prior_fourK_predictions.csv']:
 meta['source_hashes'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
(R/'results.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
print(pd.DataFrame(summaries)[['mode','方案','买入笔数','账户收益率','最大回撤','相对基准账户变化元','放弃盈利元_每笔1万','避开亏损元_每笔1万','每笔1万元净改善元','收盘封死或无量成交未核实数']].to_string(index=False))
print(pd.DataFrame(attribution).query("policy=='skip_unified_crash'").to_string(index=False))
print(pd.DataFrame(groups).to_string(index=False))
