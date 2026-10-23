from pathlib import Path
import pandas as pd,numpy as np,json
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022');P=Path('/data/cyris/work/b2_model_economics_r73_20261022')
f=pd.read_pickle(R/'panel306.pkl');pr=pd.read_pickle(R/'predictions306.pkl').set_index('id')
s=pd.read_pickle(P/'joined_98.pkl');s=s.merge(f[['id','flow_D0_timestamp_by_buy']],on='id',validate='one_to_one')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
flagcols=[c for c in pr if c.startswith(('r_','OR_','AND_','fit_')) and not c.endswith('_score')]+['union_early_lr01','close_early_lr1']
flags={name:pr.loc[s.id,name].to_numpy(bool) for name in flagcols}
weights={'baseline':np.ones(len(s))}
for name,a in flags.items():weights[name]=(~a).astype(float)
for name in [c for c in flagcols if c.startswith('r_')]:
 base=flags['union_early_lr01'];extra=flags[name]&~base
 weights['HALF_extra_'+name]=np.where(base,0,np.where(extra,.5,1))
# If source row had not been recorded by the decision, do not act on this flow rule.
weights['OR_r_news_flow_recorded']=(~(flags['union_early_lr01']|(flags['r_news_flow']&s.flow_D0_timestamp_by_buy.eq(1)))).astype(float)
for name in ['union_early_lr01','fit_D1_news','fit_D1_flow','fit_D1_news_flow',json.loads((R/'economic_selection.json').read_text())['chosen']]:
 weights['HALF_alert_'+name]=np.where(flags[name],.5,1.)
def replay(w):
 cash=1e6;peak=1e6;hold={};ledger=[];daily=[];events={}
 for n,r in enumerate(s.to_dict('records')):
  if w[n]<=0:continue
  r['weight']=float(w[n]);events.setdefault(r['d1_date'],[]).append((r['buy_clock'],0,r));events.setdefault(r['d2_date'],[]).append((r['sell7_time'],1,r))
 for date in [d for d in cal if '2026-01-01'<=d<=s.d2_date.max()]:
  for tm,k,r in sorted(events.get(date,[]),key=lambda x:(x[0],x[1])):
   if k==0:
    cost=cash*(.5 if not hold else 1)*r['weight'];cash-=cost;hold[r['id']]={'cost':cost,'shares':cost/1.001/r['entry'],'d1_close':r['d1_close']}
   else:
    lot=hold.pop(r['id']);proceeds=lot['shares']*r['sell7']*.999;cash+=proceeds
    ledger.append({'id':r['id'],'cost':lot['cost'],'proceeds':proceeds,'pnl':proceeds-lot['cost']})
  nav=cash+sum(x['shares']*x['d1_close'] for x in hold.values());peak=max(peak,nav);daily.append({'date':date,'nav':nav,'cash':cash,'drawdown':1-nav/peak})
 assert not hold
 l=pd.DataFrame(ledger);d=pd.DataFrame(daily);assert np.isclose(cash,1e6+l.pnl.sum())
 return cash/1e6-1,d.drawdown.max(),l,d
baseRet,baseDD,baseLedger,baseDaily=replay(weights['baseline'])
assert np.allclose(baseDaily.nav,pd.read_csv(P/'morning7_baseline_daily.csv').nav,rtol=0,atol=1e-6)
union=flags['union_early_lr01'];net=s.sell7/s.entry*.999/1.001-1;rows=[];detail=[];extras=[];boot=[]
rng=np.random.default_rng(20261022)
for name,w in weights.items():
 cancel=1-w;affected=cancel>0
 ret,dd,l,d=replay(w)
 direct=-float((baseLedger.set_index('id').loc[s.id,'pnl'].to_numpy()*cancel).sum())
 resize=float(l.pnl.sum()-(baseLedger.set_index('id').loc[s.id,'pnl'].to_numpy()*w).sum())
 assert np.isclose((ret-baseRet)*1e6,direct+resize,atol=1e-6)
 row={'方案':name,'取消整笔':int((w==0).sum()),'半仓笔数':int((w==.5).sum()),'捕获D2开收跌停':int(s.loc[affected,'D2开收跌停'].sum()),'捕获连续跌停':int(s.loc[affected,'D2D3连续收盘跌停'].sum()),'捕获全天封死':int(s.loc[affected,'all_day_locked'].sum()),'捕获三日急跌':int(s.loc[affected,'真实类别'].eq('急跌').sum()),'少赚元_每笔1万':float((net.clip(lower=0)*cancel).sum()*1e4),'避亏元_每笔1万':float((-net.clip(upper=0)*cancel).sum()*1e4),'净贡献元_每笔1万':float((-net*cancel).sum()*1e4),'账户收益率':ret,'最大回撤':dd,'未核实退出整笔数':int(s.loc[w>0,'seven_exit_unverified'].sum()),'原仓位直接贡献元':direct,'后续仓位贡献元':resize}
 stress=(s.d3_close-s.sell7)/s.entry*.999/1.001
 row['D3额外估值损失减少_非成交']=float((-stress*cancel*s.seven_exit_unverified).sum()*1e4)
 row['剩余D2风险仓位合计']=float((w*s['D2开收跌停']).sum())
 row['剩余连续跌停仓位合计']=float((w*s['D2D3连续收盘跌停']).sum())
 rows.append(row)
 if True:
  l.to_csv(R/(name+'_ledger.csv'),index=False,encoding='utf-8-sig');d.to_csv(R/(name+'_daily.csv'),index=False,encoding='utf-8-sig')
  for i,r in enumerate(s.to_dict('records')):detail.append({'方案':name,'id':r['id'],'D0':r['d0_date'],'买入比例':w[i],'D2开收跌停':r['D2开收跌停'],'连续跌停':r['D2D3连续收盘跌停'],'全天封死':r['all_day_locked'],'费后收益率':net.iloc[i],'取消净贡献_1万':-net.iloc[i]*cancel[i]*1e4,'退出未核实':r['seven_exit_unverified']})
 if name.startswith('r_') or name.startswith('fit_'):
  a=flags[name];extra=a&~union;overlap=a&union
  n=net[extra]
  extras.append({'规则':name,'消息或资金提示数':int(a.sum()),'与D1重合':int(overlap.sum()),'D1外新增':int(extra.sum()),'新增D2开收跌停':int(s.loc[extra,'D2开收跌停'].sum()),'新增连续跌停':int(s.loc[extra,'D2D3连续收盘跌停'].sum()),'新增全天封死':int(s.loc[extra,'all_day_locked'].sum()),'新增少赚':float(n.clip(lower=0).sum()*1e4),'新增避亏':float(-n.clip(upper=0).sum()*1e4),'新增净贡献':float(-n.sum()*1e4)})
  contribution=pd.Series(-net.to_numpy()*extra*1e4).groupby(s.d0_date.str[:7]).sum().to_numpy()
  vals=contribution[rng.integers(0,len(contribution),(2000,len(contribution)))].sum(axis=1)
  boot.append({'规则':name,'新增净贡献':float(contribution.sum()),'月聚类95下界':float(np.quantile(vals,.025)),'月聚类95上界':float(np.quantile(vals,.975))})
pd.DataFrame(rows).to_csv(R/'2026当前卖法_组合得失.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(extras).to_csv(R/'D1之外_新增保护与误拦.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(boot).to_csv(R/'新增贡献_月份不确定性.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(detail).to_csv(R/'2026逐笔动作.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
print(pd.DataFrame(rows).query("方案 in ['baseline','union_early_lr01','fit_D1_news','fit_D1_flow','fit_D1_news_flow','OR_r_repeat','OR_r_outflow','OR_r_news_flow','AND_r_news_flow','HALF_extra_r_news_flow']")[['方案','取消整笔','半仓笔数','捕获D2开收跌停','捕获连续跌停','捕获全天封死','净贡献元_每笔1万','账户收益率','最大回撤']].to_string(index=False))
print(pd.DataFrame(extras).to_string(index=False))

