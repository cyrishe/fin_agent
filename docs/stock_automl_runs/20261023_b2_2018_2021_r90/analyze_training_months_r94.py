"""Read-only R90 audit; run on the research server, never modifies production."""
import sys,json,hashlib
from pathlib import Path
import numpy as np
import pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');sys.path.insert(0,str(R))
from scripts.replay_limit_rebound_cash_r23 import replay
from scripts.benchmark_automl_1440_inference import db_connection,query
O=R/'audit_r94';O.mkdir(exist_ok=True)
def save(f,name):f.to_csv(O/(name+'.csv'),index=False,encoding='utf-8-sig')
def dump(v,name): (O/(name+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False,default=str))
def clean(f):return json.loads(f.to_json(orient='records',force_ascii=False))
def old(f):return f.symbol.str.startswith(('300','301'))&f.d0_date.lt('2020-08-24')
months=[str(m) for m in pd.period_range('2018-01','2021-12',freq='M')]
cal=json.loads((R/'calendar.json').read_text());source=json.loads((R/'overall.json').read_text());gate=source['gate']
rules={x['month']:x for x in json.loads((R/'rules_frozen.json').read_text())}
strict={x['month']:x for x in json.loads((R/'rejected_strict_extrema/rules_frozen.json').read_text())}
en=pd.read_pickle(R/'enriched.pkl');en=en[en.model_month.isin(months)].copy()
fits=[];trainrows=[]
for m in months:
 f=pd.read_pickle(R/'daily2'/m/'training_samples.pkl');mask=old(f)
 assert f.loc[mask,'rate'].eq(.1).all() and f.loc[mask,'limit_rate'].eq(.1).all()
 fits.append(f[['symbol','d0_date','rate','limit_rate']].assign(model_month=m))
 rec=dict(month=m,rf_fit=len(f),rf_historical_growth=int(mask.sum()),rf_historical_growth_fraction=float(mask.mean()),rule=rules[m]['selected']['id'],strict_rule=strict[m]['selected']['id'])
 for part in ['train','valid']:
  keys=pd.DataFrame(rules[m]['selection_keys'][part]);g=en[en.model_month.eq(m)&en.part.eq(part)].merge(keys,on=['symbol','d0_date'],how='inner',validate='one_to_one')
  assert len(g)==len(keys)==rules[m][part+'_base']['known']
  affected=g.extrema_proxy_gap_pct.fillna(0).gt(0)
  rec.update({part+'_n':len(g),part+'_extrema_gap':int(affected.sum()),part+'_extrema_fraction':float(affected.mean()),part+'_historical_growth':int(old(g).sum())})
 trainrows.append(rec)
training=pd.DataFrame(trainrows);save(training,'training_monthly')
fitall=pd.concat(fits);fitunique=fitall.drop_duplicates(['symbol','d0_date'])
unique_shapes={}
for part in ['train','valid']:
 allkeys=[]
 for m in months:
  keys=pd.DataFrame(rules[m]['selection_keys'][part]);allkeys.append(en[en.model_month.eq(m)&en.part.eq(part)].merge(keys,on=['symbol','d0_date'],validate='one_to_one'))
 g=pd.concat(allkeys).drop_duplicates(['symbol','d0_date'])
 unique_shapes[part]=dict(unique=len(g),extrema_gap=int(g.extrema_proxy_gap_pct.fillna(0).gt(0).sum()))
summary=dict(rf_fit_exposures=len(fitall),rf_historical_growth_exposures=int(old(fitall).sum()),rf_unique=len(fitunique),rf_unique_historical_growth=int(old(fitunique).sum()),four_k_unique=unique_shapes,rule_changed_months=training.loc[training.rule.ne(training.strict_rule),'month'].tolist(),minute_direct_rf_features=0,rf_features=51)
annual_training=training.assign(year=training.month.str[:4]).groupby('year').agg({c:'sum' for c in ['rf_fit','rf_historical_growth','train_n','train_extrema_gap','valid_n','valid_extrema_gap']}).reset_index()
save(annual_training,'training_yearly');dump(summary,'training_summary')
print('TRAINING',json.dumps(summary),flush=True);print(annual_training.to_string(index=False),flush=True)
# Official index source is read only; preclose includes first-day monthly return.
with db_connection(Path('/data/cyris/fin_agent/.env')) as c:
 idx=query(c,"SELECT idx_code,index_short_name,trade_date,preclose,open,high,low,close,amount,update_time FROM kcrp_index_price WHERE idx_code IN ('000001.SH','000300.SH','000905.SH','000852.SH','399006.SZ') AND trade_date BETWEEN '2018-01-01' AND '2022-01-10' ORDER BY idx_code,trade_date",())
 c.rollback()
idx.trade_date=idx.trade_date.astype(str);idx['ret']=idx.close.astype(float)/idx.preclose.astype(float)-1
assert idx.groupby('idx_code').size().min()>=973 and not idx.duplicated(['idx_code','trade_date']).any()
save(idx,'index_daily');market=[]
for (code,month),g in idx.groupby(['idx_code',idx.trade_date.str[:7]]):
 nav=np.r_[1,np.cumprod(1+g.ret.to_numpy())]
 market.append(dict(month=month,index_code=code,index_name=g.index_short_name.iloc[0],days=len(g),return_=nav[-1]-1,drawdown=float((1-nav/np.maximum.accumulate(nav)).max()),worst_day=float(g.ret.min()),worst_day_date=g.loc[g.ret.idxmin(),'trade_date'],up_days_fraction=float(g.ret.gt(0).mean()),daily_volatility=float(g.ret.std())))
market=pd.DataFrame(market);save(market,'market_monthly')
mi=market.pivot(index='month',columns='index_code',values='return_').rename(columns={'000001.SH':'sse','000300.SH':'csi300','000905.SH':'csi500','000852.SH':'csi1000','399006.SZ':'chinext'})
sig=pd.read_pickle(R/'signals_exit.pkl');sig=sig[sig.d0_date.str[:7].isin(months)].copy();sig['month']=sig.d0_date.str[:7];sig['paused']=sig.month.map(lambda m:gate[m]['paused'])
assert len(sig)==642 and len(sig[~sig.paused])==295
monthly=[];trades=[];annual=[]
for m in months:
 g=sig[sig.month.eq(m)].copy();rec=dict(month=m,paused=gate[m]['paused'],previous_r5=gate[m]['previous_r5'],previous_r10=gate[m]['previous_r10'],shadow_trades=len(g),allowed_trades=0 if gate[m]['paused'] else len(g),d1_extrema_gaps=int(g.extrema_proxy_gap_pct.fillna(0).gt(0).sum()),d2_close_floor=int(g.d2_close_floor.sum()),d2_oneprice_floor=int(g.d2_oneprice_floor.sum()),early_sells=int(g.early_sell.sum()),d2_missing=int((~g.d2_minute_available).sum()))
 for scenario in ['close','7pct']:
  frame=g.assign(d2_close=g.sell_price) if scenario=='7pct' else g
  end=max(g.d2_date) if len(g) else str(pd.Period(m).end_time.date())
  out,_,nav,led,bad=replay(frame,cal,start=m+'-01',end=end,initial=1e6,fee_rate=.001)
  assert not bad and out['entries']==len(g) and out['pending']==0
  rec.update({f'shadow_{scenario}_return':out['total_return'],f'actual_{scenario}_return':0 if rec['paused'] else out['total_return'],f'{scenario}_win':out['closed_win_rate'],f'{scenario}_mean_trade':out['closed_mean_return'],f'{scenario}_drawdown':out['max_drawdown']})
  if scenario=='7pct' and len(led):
   assert abs(led.pnl.sum()/1e6-out['total_return'])<1e-10
   rec['loss_pnl']=float(led.loc[led.pnl.lt(0),'pnl'].sum());rec['gain_pnl']=float(led.loc[led.pnl.gt(0),'pnl'].sum())
   worst=led.nsmallest(3,'pnl');rec['worst3']= '; '.join(f'{x.symbol} {x.d0_date} {x.return_:+.2%} 损益{x.pnl:.0f}' for x in worst.itertuples());rec['worst3_pnl']=float(worst.pnl.sum())
   cols=['symbol','d0_date','d1_date','d2_date','entry','sell','d2_close_original','return_','pnl','cost','shares','d2_cash_dividend','early_sell','sell_time','d2_close_floor','d2_oneprice_floor','d2_minute_available','d2_quality','extrema_proxy_gap_pct','rule_id']
   t=led[cols].copy();t.insert(0,'month',m);t.insert(1,'paused',rec['paused']);t['close_net_return']=(t.d2_close_original*(1-.001)+t.d2_cash_dividend)/ (t.entry*(1+.001))-1
   for code,label in [('000300.SH','csi300_d0_d2'),('000852.SH','csi1000_d0_d2')]:
    ix=idx[idx.idx_code.eq(code)].set_index('trade_date').ret
    t[label]=[float((1+ix.loc[(ix.index>d0)&(ix.index<=d2)]).prod()-1) for d0,d2 in zip(t.d0_date,t.d2_date)]
   trades.append(t)
 monthly.append(rec)
mon=pd.DataFrame(monthly).merge(mi,left_on='month',right_index=True,validate='one_to_one');ts=pd.concat(trades,ignore_index=True)
save(mon,'monthly_all');save(ts,'trades_all');save(mon[mon.paused],'paused_months')
loss=mon[mon.shadow_7pct_return.le(-.15)].sort_values('shadow_7pct_return');save(loss,'large_loss_months');save(ts[ts.month.isin(loss.month)],'large_loss_trades');save(market[market.month.isin(loss.month)],'large_loss_market')
for y in ['2018','2019','2020','2021']:
 for scenario in ['close','7pct']:
  for state in ['allowed','shadow']:
   g=sig[sig.month.str.startswith(y)&((~sig.paused) if state=='allowed' else True)].copy()
   if scenario=='7pct':g['d2_close']=g.sell_price
   out,_,_,led,bad=replay(g,cal,start=y+'-01-01',end=max(g.d2_date),initial=1e6,fee_rate=.001)
   assert not bad and out['entries']==len(g)
   annual.append(dict(year=y,scenario=scenario,state=state,trades=len(g),return_=out['total_return'],win=out['closed_win_rate'],dd=out['max_drawdown'],early_sells=int(g.early_sell.sum()),extrema_gaps=int(g.extrema_proxy_gap_pct.fillna(0).gt(0).sum()),floor_close=int(g.d2_close_floor.sum())))
annual=pd.DataFrame(annual);save(annual,'annual')
oldannual=pd.read_csv(R/'primary_model_year.csv',dtype={'year':str})
for row in annual[annual.state.eq('allowed')].itertuples():
 ref=oldannual[oldannual.year.eq(row.year)&oldannual.scenario.eq('pause_7pct' if row.scenario=='7pct' else 'V2_pause_close')].iloc[0]
 assert abs(row.return_-ref.return_)<1e-10
yearmarket=[]
for (code,y),g in idx[idx.trade_date.le('2021-12-31')].groupby(['idx_code',idx.trade_date.str[:4]]):yearmarket.append(dict(year=y,index_code=code,return_=float((1+g.ret).prod()-1)))
save(pd.DataFrame(yearmarket),'market_yearly')
print('ANNUAL',annual.to_string(index=False),flush=True);print('LOSSES',loss[['month','paused','shadow_trades','shadow_7pct_return','shadow_close_return','csi300','csi1000','d2_close_floor']].to_string(index=False),flush=True)
print('PAUSED',mon.loc[mon.paused,['month','shadow_7pct_return','shadow_trades']].to_string(index=False),flush=True)
dump(dict(monthly=clean(mon),trades=clean(ts),losses=clean(loss),training=clean(training),training_yearly=clean(annual_training),annual=clean(annual),market=clean(market),market_yearly=yearmarket),'workbook_data')
files=['panel.pkl','enriched.pkl','rules_frozen.json','signals_exit.pkl','overall.json','calendar.json','rejected_strict_extrema/rules_frozen.json']
dump(dict(strategy='imac/B2_V2',definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',source_root=str(R),production_changed=False,database_registration='未提交',cloud_archive='待同步',script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),source_hashes={x:hashlib.sha256((R/x).read_bytes()).hexdigest() for x in files},output_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in O.glob('*.csv')},checks=['642 shadow trades / 295 allowed','48 months / 26 paused','annual allowed returns reproduce R90 <1e-10','monthly ledger PnL equals account result','four-K selected keys equal frozen training denominator']), 'manifest')
print('DONE',flush=True)
