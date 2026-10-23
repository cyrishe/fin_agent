from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
R71=Path('/data/cyris/work/b2_board_volume_r71_20261022')
R69=Path('/data/cyris/work/b2_d2_limit_follow_r69_20261021')
s=pd.read_pickle(R69/'all_signals.pkl')
r71=pd.read_csv(R71/'B2_候选连板量能特征_709笔.csv',dtype={'股票':str})
s=s.merge(r71[['股票','D0选中日','V2放行','D2开收跌停并集','D2D3连续收盘跌停']],left_on=['stk_code','d0_date'],right_on=['股票','D0选中日'],validate='one_to_one')
assert len(s)==709
pre0=pd.read_csv(R71/'preboard_daily_source.csv.gz',dtype={'stk_code':str,'trade_date':str,'对应首板日':str})
ohlcv=pd.read_csv(R/'preboard_ohlcv_source.csv.gz',dtype={'stk_code':str,'trade_date':str})
pre0=pre0[['stk_code','trade_date','对应首板日']].merge(ohlcv,on=['stk_code','trade_date'],validate='many_to_one')
pregrp={(code,first):g.sort_values('trade_date').copy() for (code,first),g in pre0.groupby(['stk_code','对应首板日'],sort=False)}
board=pd.read_csv(R71/'board_daily_source.csv.gz',dtype={'stk_code':str,'trade_date':str})
board=board.set_index(['stk_code','trade_date'])
d=pd.read_pickle(R69/'daily.pkl').set_index(['trade_date','stk_code'])
def safe_ratio(a,b):
 return float(a/b) if pd.notna(a) and pd.notna(b) and float(b)>0 else np.nan
def corr(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float)
 return float(np.corrcoef(a,b)[0,1]) if len(a)>=5 and np.std(a)>1e-9 and np.std(b)>1e-9 else np.nan
def slope_log(x):
 x=np.asarray(x,float)
 return float(np.polyfit(np.arange(len(x)),np.log(x),1)[0]) if len(x)>=5 and np.all(x>0) else np.nan
def cliplog(v):
 return float(np.clip(np.log(v),-5,5)) if np.isfinite(v) and v>0 else np.nan
def history_features(p,b):
 out={}
 out['prehistory_days']=len(p)
 for n in [5,10,20]:
  if len(p)<n:
   for key in ['price_return','volatility','volume_trend_log','turnover_trend_log','signed_amount_share','up_volume_share','cmf','price_volume_corr','first_volume_logratio','first_amount_logratio','first_turnover_logratio','first_vol_over_max','first_volume_z']:
    out[f'pre{n}_{key}']=np.nan
   continue
  q=p.tail(n)
  vol=q.volume.to_numpy(float);amount=q.amount.to_numpy(float);turn=q.turn_ratio.to_numpy(float)
  ret=(q.close/q.preclose-1).to_numpy(float)
  rng=(q.high-q.low).to_numpy(float)
  clv=np.divide((2*q.close-q.high-q.low).to_numpy(float),rng,out=np.zeros(n),where=rng>0)
  out[f'pre{n}_price_return']=safe_ratio(q.close.iloc[-1],q.close.iloc[0])-1
  out[f'pre{n}_volatility']=float(np.std(ret,ddof=1))
  out[f'pre{n}_volume_trend_log']=slope_log(vol)
  out[f'pre{n}_turnover_trend_log']=slope_log(turn) if np.all(turn>0) else np.nan
  out[f'pre{n}_signed_amount_share']=float(np.sum(np.sign(ret)*amount)/np.sum(amount))
  out[f'pre{n}_up_volume_share']=float(np.sum(vol[ret>0])/np.sum(vol))
  out[f'pre{n}_cmf']=float(np.sum(clv*vol)/np.sum(vol))
  out[f'pre{n}_price_volume_corr']=corr(ret,np.log(vol))
  out[f'pre{n}_first_volume_logratio']=cliplog(safe_ratio(b.volume,np.mean(vol)))
  out[f'pre{n}_first_amount_logratio']=cliplog(safe_ratio(b.amount,np.mean(amount)))
  out[f'pre{n}_first_turnover_logratio']=cliplog(safe_ratio(b.turn_ratio,np.mean(turn)))
  out[f'pre{n}_first_vol_over_max']=safe_ratio(b.volume,np.max(vol))
  out[f'pre{n}_first_volume_z']=float(np.clip((b.volume-np.mean(vol))/np.std(vol,ddof=1),-10,10)) if np.std(vol,ddof=1)>0 else np.nan
 if len(p)>=20:
  q=p.tail(20)
  out['pre20_recent5_oldest5_volume_logratio']=cliplog(safe_ratio(q.tail(5).volume.mean(),q.head(5).volume.mean()))
  out['pre20_recent5_oldest5_amount_logratio']=cliplog(safe_ratio(q.tail(5).amount.mean(),q.head(5).amount.mean()))
 else:
  out['pre20_recent5_oldest5_volume_logratio']=np.nan
  out['pre20_recent5_oldest5_amount_logratio']=np.nan
 return out
rows=[]
for r in s.itertuples():
 key=(r.stk_code,r.first_trade_date)
 p=pregrp.get(key,pd.DataFrame())
 b=board.loc[key]
 assert b.is_limit_price==1
 assert r.first_trade_date<=r.d0_date
 f=history_features(p,b)
 d3=d.loc[(r.d3_date,r.stk_code)]
 d1ret=r.d1_close/r.close-1
 d2ret=r.d2_close/r.d1_close-1
 d3ret=r.d3_close/r.d2_close-1
 future3=r.d3_close/r.close-1
 minimum=min(r.d1_close,r.d2_close,r.d3_close)/r.close-1
 floor2=bool(r.D2开收跌停并集)
 floor3=int(d3.is_limit_price)==2
 crash=bool(floor2 or floor3 or minimum<=-.15)
 strong=bool(future3>=.15 and min(d1ret,d2ret,d3ret)>=0)
 # Only first-board and preboard factors below. In particular no D0 board count, D1 minutes, model score, V2 gate or calendar year.
 f.update({
  'first_return':safe_ratio(b.close,b.preclose)-1,
  'first_open_gap':safe_ratio(b.open,b.preclose)-1,
  'first_intraday_return':safe_ratio(b.close,b.open)-1,
  'first_range':safe_ratio(b.high-b.low,b.preclose),
  'first_close_location':float((b.close-b.low)/(b.high-b.low)) if b.high>b.low else 1.0,
  'first_one_price':int(b.high==b.low and b.volume>0),
  'first_turnover_pct':float(b.turn_ratio),
  'first_price_impact_per_relvolume':safe_ratio(b.close/b.preclose-1,np.exp(f.get('pre20_first_volume_logratio',np.nan))),
  'first_signed_flow_proxy':float(((2*b.close-b.high-b.low)/(b.high-b.low) if b.high>b.low else 0)*b.turn_ratio),
 })
 out={'股票':r.stk_code,'首板日':r.first_trade_date,'D0选中日':r.d0_date,'D1日':r.d1_date,'D2日':r.d2_date,'D3日':r.d3_date,
      'V2放行':int(r.V2放行),'本轮D0已连板天数_仅审计':int(r.board_streak_days),
      '首板前数据截至':p.trade_date.max() if len(p) else '',
      'D0至D3收盘涨跌幅':future3,'D0至D3最低收盘涨跌幅':minimum,
      'D2开收跌停':int(floor2),'D3收盘跌停':int(floor3),'D2D3连续收盘跌停':int(r.D2D3连续收盘跌停),
      '三日急跌标签':int(crash),'三日持续大涨标签':int(strong),
      **f}
 rows.append(out)
t=pd.DataFrame(rows).sort_values(['D0选中日','股票']).reset_index(drop=True)
assert len(t)==709 and not t[['股票','D0选中日']].duplicated().any()
assert sum(t.V2放行)==306 and sum(t.loc[t.V2放行==1,'D2开收跌停'])==67
assert not ((t['三日急跌标签']==1)&(t['三日持续大涨标签']==1)).any()
feature_cols=list(f.keys())
assert len(feature_cols)>35
# Exact model inputs are feature_cols only, not identifier, chronology, post-D0 result or board count.
(R/'feature_columns.json').write_text(json.dumps(feature_cols,ensure_ascii=False,indent=2)+'\n')
t.to_csv(R/'首板前量价资金趋势_709候选.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
for year,g in t.groupby(t.D0选中日.str[:4]):
 print(year,len(g),'crash',int(g['三日急跌标签'].sum()),'strong',int(g['三日持续大涨标签'].sum()),'v2',int(g.V2放行.sum()))
print('features',len(feature_cols),'history20complete',int(t.prehistory_days.eq(20).sum()),'D2floor',int(t['D2开收跌停'].sum()))
m=json.loads((R/'manifest.json').read_text())
for fn in ['feature_columns.json','首板前量价资金趋势_709候选.csv','prepare.py','features.py']:
 m['files'][fn]=hashlib.sha256((R/fn).read_bytes()).hexdigest()
(R/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
