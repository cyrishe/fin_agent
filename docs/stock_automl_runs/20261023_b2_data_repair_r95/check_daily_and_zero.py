import sys,json
from pathlib import Path
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');O=Path('/data/cyris/work/b2_data_repair_r95_20261023');A=O/'akshare_daily';sys.path.insert(0,str(R));sys.path.insert(0,'/data/cyris/tushare_backfill')
from backfill_tushare_minutes import Tushare,token_from_file,SlidingRateLimiter
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60))
# Independent daily checks: unadjusted prices; all units normalized to shares.
rows=[]
for p in A.glob('*.csv'):
 provider,symbol,start,end=p.stem.split('_');symbol=symbol[-6:]
 try:f=pd.read_csv(p)
 except pd.errors.EmptyDataError:continue
 f=f.rename(columns={'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'})
 if not len(f):continue
 f['date']=f.date.astype(str)
 if provider=='em':f['volume']=pd.to_numeric(f.volume)*100
 for month,g in f.groupby(f.date.str[:7]):
  file=R/'price_months'/f'price_{month}.csv.gz'
  if not file.exists():continue
  ref=pd.read_csv(file,dtype={'stk_code':str,'trade_date':str});ref=ref[ref.stk_code.str.startswith(symbol)]
  q=g.merge(ref,left_on='date',right_on='trade_date',suffixes=('_ak','_saved'),validate='one_to_one')
  for x in q.to_dict('records'):
   row=dict(provider=provider,symbol=symbol,date=x['date'])
   for k in ['open','high','low','close','volume']:row[k+'_diff']=float(x[k+'_ak'])-float(x[k+'_saved'])
   row['ohlc_ok']=all(abs(row[k+'_diff'])<=.011 for k in ['open','high','low','close'])
   rows.append(row)
pd.DataFrame(rows).to_csv(O/'akshare_daily_comparison.csv',index=False,encoding='utf-8-sig');print('AK_COMPARE',pd.DataFrame(rows).groupby(['provider','symbol']).agg(rows=('date','size'),ok=('ohlc_ok','sum')).to_dict('index'),flush=True)
# A returned zero-volume flat sequence is NOT recovered trading data.
rec=pd.read_csv(O/'missing_recovery.csv',dtype={'symbol':str}).drop(columns=['volume','positive_bars','file'],errors='ignore');vol=pd.read_csv(O/'recovered_volume_check.csv');rec=rec.merge(vol,left_on=rec.date+'_'+rec.symbol+'.pkl',right_on='file',validate='one_to_one').drop(columns=['key_0'],errors='ignore')
rec['source_rows_returned']=rec.raw_rows.gt(0);rec['recovered']=rec.recovered&rec.volume.gt(0);rec['note']=np.where(rec.volume.eq(0),'全天零成交占位；不可用于成交，也不算补回交易分钟','')
rec.to_csv(O/'missing_recovery.csv',index=False,encoding='utf-8-sig')
quarantine=O/'zero_volume_placeholder';quarantine.mkdir(exist_ok=True)
for x in rec[rec.volume.eq(0)].itertuples():
 p=O/'minute_overlay'/x.file
 if p.exists():p.rename(quarantine/x.file)
# Saved model training, not candidate-universe denominators.
tr=[]
for m in pd.period_range('2018-01','2021-12',freq='M').astype(str):
 f=pd.read_pickle(R/'daily2'/m/'training_samples.pkl');bad=f.d1_volume.fillna(0).le(0)
 tr.append(dict(month=m,used=len(f),d1_zero_volume=int(bad.sum()),positive_label_among_zero=int(f.loc[bad,'label_3'].sum())))
pd.DataFrame(tr).to_csv(O/'rf_zero_volume_exposure.csv',index=False,encoding='utf-8-sig');print('RF_ZERO',pd.DataFrame(tr).groupby(pd.DataFrame(tr).month.str[:4]).sum(numeric_only=True).to_dict('index'),flush=True)
s=pd.read_pickle(R/'signals_exit.pkl');s=s[s.d0_date.ge('2018-01-01')];gate=json.loads((R/'overall.json').read_text())['gate'];sr=[]
# Cache per exit month.
for month,g in s.groupby(s.d2_date.str[:7]):
 f=pd.read_csv(R/'price_months'/f'price_{month}.csv.gz',dtype={'stk_code':str,'trade_date':str});f['symbol']=f.stk_code.str[:6]
 q=g.merge(f[['symbol','trade_date','volume']],left_on=['symbol','d2_date'],right_on=['symbol','trade_date'],suffixes=('','_exit'),validate='many_to_one')
 for x in q[q.volume_exit.le(0)].itertuples():sr.append(dict(symbol=x.symbol,d0_date=x.d0_date,d1_date=x.d1_date,d2_date=x.d2_date,entry=x.entry,d2_close=x.d2_close,paused=gate[x.d0_date[:7]]['paused'],d2_volume=x.volume_exit))
pd.DataFrame(sr).to_csv(O/'d2_zero_volume_trades.csv',index=False,encoding='utf-8-sig');print('D2_ZERO',sr,flush=True)
# Confirm a few no-trade days using the configured provider's suspension endpoint.
res=[]
for sym,date in [('600579','20180226'),('002927','20180309'),('300056','20180301'),('600113','20180323')]:
 try:
  fields,data=api.query('suspend_d',dict(ts_code=sym+('.SH' if sym.startswith('6') else '.SZ'),trade_date=date));x=dict(symbol=sym,date=date,fields=fields,rows=data)
 except Exception as e:x=dict(symbol=sym,date=date,error=str(e))
 res.append(x);print('SUSPEND',json.dumps(x,ensure_ascii=False),flush=True)
 if 'error' in x:break
(O/'suspension_probe.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
