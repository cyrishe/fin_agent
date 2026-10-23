from __future__ import annotations
import hashlib, json, sys, gzip, io, subprocess
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/data/cyris/work/b2_d2_floor_volume_r79_20261022')
R69=Path('/data/cyris/work/b2_d2_limit_follow_r69_20261021')
R71=Path('/data/cyris/work/b2_board_volume_r71_20261022')
R76=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
CLOCK=[f'{n//60:02d}:{n%60:02d}' for n in list(range(571,691))+list(range(781,901))]
def extract_archive(path,day,symbol):
 path=Path(path)
 selected=[]
 if path.name.endswith('.gz'):
  stream=gzip.open(path,'rb')
  proc=None
 else:
  proc=subprocess.Popen(['zstdcat',str(path)],stdout=subprocess.PIPE)
  stream=proc.stdout
 try:
  header=stream.readline()
  cols=header.decode().strip().split(',')
  ix=cols.index('ts_code') if 'ts_code' in cols else cols.index('symbol')
  for line in stream:
   if line.split(b',',ix+1)[ix][:6]==symbol.encode(): selected.append(line)
 finally:
  stream.close()
  if proc is not None:
   status=proc.wait()
   assert status==0,(path,status)
 assert selected,(path,day,symbol)
 raw=pd.read_csv(io.BytesIO(header+b''.join(selected)),dtype={cols[ix]:str})
 if 'ts_code' in cols:
  assert len(raw)==241,(day,symbol,len(raw))
  raw['time']=raw.trade_time
  raw['volume']=pd.to_numeric(raw.vol)
  au=raw[raw.time.str.endswith('09:30:00')]
  assert len(au)==1,(day,symbol)
  raw=raw[~raw.time.str.endswith('09:30:00')].copy()
  assert len(raw)==240
  first=raw.time.str.endswith('09:31:00')
  assert first.sum()==1
  for k in ['open','high','low','volume']: raw[k]=pd.to_numeric(raw[k])
  raw.loc[first,'open']=float(au.open.iloc[0])
  raw.loc[first,'high']=max(float(au.high.iloc[0]),float(raw.loc[first,'high'].iloc[0]))
  raw.loc[first,'low']=min(float(au.low.iloc[0]),float(raw.loc[first,'low'].iloc[0]))
  raw.loc[first,'volume']=float(au.volume.iloc[0])+float(raw.loc[first,'volume'].iloc[0])
 return raw[['time','open','high','low','close','volume']].copy()

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(4*1024*1024),b''): h.update(block)
 return h.hexdigest()

s=pd.read_pickle(R69/'all_signals.pkl')
s=s[s.v2_allowed].copy()
d=pd.read_pickle(R69/'daily.pkl')
f=pd.read_csv(R71/'B2_V2放行连板量能特征_306笔.csv',dtype={'股票':str})
m=s.merge(d,left_on=['d2_date','stk_code'],right_on=['trade_date','stk_code'],suffixes=('','_d2'),validate='one_to_one')
m=m.merge(f,left_on=['stk_code','d0_date'],right_on=['股票','D0选中日'],validate='one_to_one')
assert len(m)==306
d3=d.set_index(['trade_date','stk_code'])
manifest=json.loads((R76/'minute_source_manifest.json').read_text())
mins={z['id']:z for z in manifest}
assert len(mins)==306
rule=pd.read_csv(R76/'306笔_全年同卖法价格.csv',dtype={'stk_code':str}).set_index('id')
assert len(rule)==306

def down_price(r):
 rate=Decimal('.2') if r.symbol.startswith(('30','68')) else Decimal('.05') if r.d2_st in ('S','Y') else Decimal('.1')
 return float((Decimal(str(r.preclose_d2))*(1-rate)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

m['preboard_count']=m['板前实际交易日数']
for days in (5,10,20):m[f'base{days}']=m[f'首板前{days}日均量_万股']
m['floor']=m.apply(down_price,axis=1)
m['touch']=np.isclose(m.low_d2,m.floor,atol=.001)
m['open_floor']=np.isclose(m.open_d2,m.floor,atol=.001)
m['close_floor']=np.isclose(m.close_d2,m.floor,atol=.001)
m['all_locked']=np.isclose(m.high_d2,m.floor,atol=.001)
assert int(m.open_floor.sum())==34
assert int(m.close_floor.sum())==52
assert int((m.open_floor|m.close_floor).sum())==67
assert (m.close_floor==m['D2收盘跌停'].astype(bool)).all()
assert (m.open_floor==m['D2开盘跌停'].astype(bool)).all()
assert (m.touch | (~m.open_floor & ~m.close_floor)).all()
assert not m[m.all_locked].empty
rows=[]
source_records=[]
volume_diffs=[]
volume_rel_diffs=[]
for n,r in enumerate(m.itertuples(index=False),1):
 if not r.touch:continue
 ident=r.stk_code+'|'+r.d0_date
 z=mins[ident];sp=Path(z['source']['path'])
 assert sp.exists(),sp
 if sp.suffix=='.pkl':bars=pd.read_pickle(sp)
 elif sp.name.endswith('.csv.gz') and z['source']['type']=='R73 minute':bars=pd.read_csv(sp)
 else:bars=extract_archive(sp,r.d2_date,r.symbol)
 bars=bars.copy()
 bars['time']=pd.to_datetime(bars.time)
 bars=bars.sort_values('time').reset_index(drop=True)
 for col in ['open','high','low','close','volume']:bars[col]=pd.to_numeric(bars[col])
 assert len(bars)==240 and bars.time.dt.strftime('%H:%M').tolist()==CLOCK,ident
 vol_diff=abs(float(bars.volume.sum())-float(r.volume_d2))
 assert vol_diff<=max(5000,.0001*float(r.volume_d2)),(ident,vol_diff)
 volume_diffs.append(vol_diff)
 volume_rel_diffs.append(vol_diff/float(r.volume_d2))
 assert all(abs(float(val)-float(ref))<.011 for val,ref in [
  (bars.open.iloc[0],r.open_d2),(bars.high.max(),r.high_d2),
  (bars.low.min(),r.low_d2),(bars.close.iloc[-1],r.close_d2)
 ]),ident
 mask=bars.low<=r.floor+.001
 hit=np.flatnonzero(mask.to_numpy())
 assert len(hit)>0,ident
 first=int(hit[0])
 after=bars.iloc[first+1:]
 later_high=((after.high>r.floor+.001)&(after.volume>0))
 later_full=((after.low>r.floor+.001)&(after.volume>0))
 group='全天一价封死' if r.all_locked else '触板后收盘仍跌停' if r.close_floor else '触板后收盘离板'
 sell7=rule.loc[ident]
 sell7_prior=bool(sell7.seven_proxy) and isinstance(sell7.sell7_time,str) and sell7.sell7_time < bars.time.iloc[first].strftime('%H:%M')
 base=lambda days:getattr(r,'base'+str(days))
 volume10k=float(r.volume_d2)/1e4
 d3row=d3.loc[(r.d3_date,r.stk_code)] if (r.d3_date,r.stk_code) in d3.index else None
 row={
  '股票':r.stk_code,'D0选中日':r.d0_date,'D1买入日':r.d1_date,'D2跌停日':r.d2_date,
  'D0已连板天数':int(r.board_streak_days),'首板日':r.first_trade_date,
  '首板前实际成交日数':int(r.preboard_count),
  '跌停分类':group,'D2开盘跌停':int(r.open_floor),'D2收盘跌停':int(r.close_floor),
  'D2全天一价封死':int(r.all_locked),'D2首触跌停分钟':bars.time.iloc[first].strftime('%H:%M'),
  '上午7%卖出代理明确早于触板':int(sell7_prior),
  '首触后曾有后续分钟高于跌停':int(later_high.any()),
  '首触后曾有后续完整分钟离板':int(later_full.any()),
  '首触后完整离板分钟数':int(later_full.sum()),
  '首触前成交量_万股':float(bars.volume.iloc[:first].sum())/1e4,
  '首触分钟之后成交量_万股':float(after.volume.sum())/1e4,
  '首触分钟之后成交量占全天':float(after.volume.sum()/bars.volume.sum()),
  '首触之后完整离板分钟成交量_万股':float(after.loc[later_full,'volume'].sum())/1e4,
  '全天整分钟封在跌停价数':int((bars.high<=r.floor+.001).sum()),
  'D2官方前收':float(r.preclose_d2),'D2跌停价':float(r.floor),
  'D2开盘价':float(r.open_d2),'D2最低价':float(r.low_d2),
  'D2最高价':float(r.high_d2),'D2收盘价':float(r.close_d2),
  'D2成交量_万股':volume10k,
  'D1成交量_万股':float(r.d1_volume)/1e4,
  'D0成交量_万股':float(r.volume)/1e4,
  'D2量除D1量':float(r.volume_d2/r.d1_volume) if r.d1_volume>0 else np.nan,
  'D2量除D0量':float(r.volume_d2/r.volume) if r.volume>0 else np.nan,
  'D3收盘跌停':int(d3row.is_limit_price==2) if d3row is not None else np.nan,
  'D3收盘涨跌幅_pct':float((d3row.close/d3row.preclose-1)*100) if d3row is not None else np.nan,
 }
 for days in (5,10,20):
  avg=base(days)
  row[f'首板前{days}日均量_万股']=float(avg) if pd.notna(avg) else np.nan
  row[f'D2量除首板前{days}日均量']=volume10k/avg if pd.notna(avg) and avg>0 else np.nan
 rows.append(row)
 source_records.append({'id':ident,'path':str(sp),'sha256':z['source']['sha256'],'type':z['source']['type']})
 if len(rows)%25==0:print('audited',len(rows),flush=True)
out=pd.DataFrame(rows).sort_values(['D2跌停日','股票','D0选中日']).reset_index(drop=True)
assert len(out)==int(m.touch.sum())
assert len(out)==100
assert int(out['D2全天一价封死'].sum())==14
assert int(out['D2收盘跌停'].sum())==52
assert int(out['D2开盘跌停'].sum())==34
assert int(out['上午7%卖出代理明确早于触板'].sum())==3
assert int(out['D2量除首板前20日均量'].notna().sum())==97
out.to_csv(ROOT/'100笔D2触跌停_成交量逐笔.csv',index=False,encoding='utf-8-sig',float_format='%.6f')

groups=[]
for label,x in list(out.groupby('跌停分类',sort=False))+[('全部触跌停',out)]:
 row={'类别':label,'笔数':len(x),
      '首触后曾有后续分钟高于跌停':int(x['首触后曾有后续分钟高于跌停'].sum()),
      '首触后曾有后续完整分钟离板':int(x['首触后曾有后续完整分钟离板'].sum()),
      'D3收盘跌停笔数':int(x['D3收盘跌停'].sum()),
      'D2日均量_万股':x['D2成交量_万股'].mean(),
      '首触分钟之后成交量占全天中位':x['首触分钟之后成交量占全天'].median()}
 for days in (5,10,20):
  y=x[f'D2量除首板前{days}日均量'].dropna()
  row[f'可比{days}日笔数']=len(y)
  row[f'相对{days}日均量中位倍数']=y.median()
  row[f'相对{days}日均量平均倍数']=y.mean()
  row[f'相对{days}日均量小于1倍笔数']=int((y<1).sum())
  row[f'相对{days}日均量小于0.5倍笔数']=int((y<.5).sum())
  row[f'相对{days}日均量至少2倍笔数']=int((y>=2).sum())
 groups.append(row)
groups=pd.DataFrame(groups)
groups.to_csv(ROOT/'分组量能汇总.csv',index=False,encoding='utf-8-sig',float_format='%.6f')

bins=[0,.1,.25,.5,1,2,5,10,float('inf')]
labels=['<0.1','0.1-<0.25','0.25-<0.5','0.5-<1','1-<2','2-<5','5-<10','>=10']
out['20日均量倍数档']=pd.cut(out['D2量除首板前20日均量'],bins=bins,labels=labels,right=False,include_lowest=True)
bt=pd.crosstab(out['跌停分类'],out['20日均量倍数档'],dropna=False).reindex(columns=labels,fill_value=0)
bt.insert(0,'基线缺失',out.groupby('跌停分类')['D2量除首板前20日均量'].apply(lambda v:int(v.isna().sum())))
bt.to_csv(ROOT/'20日均量倍数分档.csv',encoding='utf-8-sig')
pd.crosstab(out['D2跌停日'].str[:4],out['跌停分类']).to_csv(ROOT/'逐年触跌停分组.csv',encoding='utf-8-sig')
opening=out[out['D2开盘跌停'].eq(1)]
opening_summary=opening.groupby('D2全天一价封死').agg(笔数=('股票','size'),二十日可比=('D2量除首板前20日均量','count'),二十日倍数中位=('D2量除首板前20日均量','median'),D3仍跌停=('D3收盘跌停','sum')).reset_index()
opening_summary.to_csv(ROOT/'开盘跌停34笔_封死与打开.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
close=out[out['D2收盘跌停'].eq(1)]
risk=close.groupby('20日均量倍数档',observed=False).agg(笔数=('股票','size'),D3继续跌停=('D3收盘跌停','sum')).reset_index()
risk.to_csv(ROOT/'收盘跌停52笔_量比与D3.csv',index=False,encoding='utf-8-sig')

nreopen=int(out['首触后曾有后续分钟高于跌停'].sum())
print('TOUCH',len(out),'GROUPS',out['跌停分类'].value_counts().to_dict(),'REOPEN_LATER',nreopen)
print('OPENING',opening_summary.to_string(index=False))
print('GROUPS',groups[['类别','笔数','可比20日笔数','相对20日均量中位倍数','相对20日均量平均倍数','D3收盘跌停笔数']].to_string(index=False))
print('BINS',bt.to_string())
print('RISK',risk.to_string(index=False))

sources=[R69/'all_signals.pkl',R69/'daily.pkl',R71/'B2_V2放行连板量能特征_306笔.csv',R76/'minute_source_manifest.json',R76/'306笔_全年同卖法价格.csv']
verification={'sample_v2':len(m),'d2_intraday_touch':len(out),'d2_open_floor':int(m.open_floor.sum()),
 'd2_close_floor':int(m.close_floor.sum()),'d2_open_or_close_floor':int((m.open_floor|m.close_floor).sum()),
 'all_day_locked':int(out['D2全天一价封死'].sum()),
 'complete_minute_days_checked':len(out),'minute_ohlc_matches_daily':True,'minute_volume_matches_daily_within_0_01pct_or_5000shares':True,
 'minute_volume_max_abs_diff_shares':max(volume_diffs),'minute_volume_max_relative_diff':max(volume_rel_diffs),
 'morning7_sell_proxy_before_first_floor_touch':int(out['上午7%卖出代理明确早于触板'].sum()),
 'baseline20_available':int(out['D2量除首板前20日均量'].notna().sum()),
 'official_preclose_used_for_floor':True,
 'one_adjustment_boundary':'601279.SH D2 2025-06-17 official preclose 4.37 vs D1 close 4.38; low/close 3.94 not floor 3.93',
 'no_production_db_write':True}
(ROOT/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
files={p.name:sha(p) for p in ROOT.iterdir() if p.is_file() and p.name!='manifest.json'}
(ROOT/'manifest.json').write_text(json.dumps({'sources':{str(p):sha(p) for p in sources},'minute_sources':source_records,'outputs':files},ensure_ascii=False,indent=2)+'\n')
