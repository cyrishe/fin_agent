from __future__ import annotations
import gzip, hashlib, io, json, subprocess
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
R79=Path('/data/cyris/work/b2_d2_floor_volume_r79_20261022')
R76=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
CLOCK=[f'{n//60:02d}:{n%60:02d}' for n in list(range(571,691))+list(range(781,901))]
CHECK='09:31 09:35 09:40'.split()

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()

def to_normalized(raw,day):
 cols=set(raw.columns)
 if 'ts_code' in cols:
  if len(raw)!=241:return None,'raw_count_'+str(len(raw))
  if raw.trade_time.astype(str).str[:10].ne(day).any():return None,'date'
  auc=raw[raw.trade_time.str.endswith('09:30:00')]
  regular=raw[~raw.trade_time.str.endswith('09:30:00')].copy()
  if len(auc)!=1 or len(regular)!=240:return None,'auction_count'
  regular=regular.rename(columns={'trade_time':'time','vol':'volume'})
  first=regular.time.str.endswith('09:31:00')
  if first.sum()!=1:return None,'first_count'
  for col in ['open','high','low','close','volume']:regular[col]=pd.to_numeric(regular[col])
  regular.loc[first,'open']=float(auc.open.iloc[0])
  regular.loc[first,'high']=max(float(auc.high.iloc[0]),float(regular.loc[first,'high'].iloc[0]))
  regular.loc[first,'low']=min(float(auc.low.iloc[0]),float(regular.loc[first,'low'].iloc[0]))
  regular.loc[first,'volume']=float(auc.vol.iloc[0])+float(regular.loc[first,'volume'].iloc[0])
  bars=regular
 else:
  bars=raw.copy()
  for col in ['open','high','low','close','volume']:bars[col]=pd.to_numeric(bars[col])
  bars['time']=bars.time.astype(str)
 bars=bars.sort_values('time').reset_index(drop=True)
 if len(bars)!=240:return None,'normalized_count_'+str(len(bars))
 clock=pd.to_datetime(bars.time).dt.strftime('%H:%M').tolist()
 if clock!=CLOCK:return None,'clock'
 if not np.isfinite(bars[['open','high','low','close','volume']].to_numpy()).all():return None,'nonfinite'
 if (bars.volume<0).any():return None,'negative_volume'
 return bars,None

def extract_selected(path,day,codes):
 path=Path(path)
 rows={c:[] for c in codes}
 if path.name.endswith('.gz'):
  header=b'ts_code,trade_time,open,high,low,close,vol,amount\n'
  dec_cmd=['gzip','-dc',str(path)]
 else:
  header=b'time,symbol,open,high,low,close,volume,turnover\n'
  dec_cmd=['zstdcat',str(path)]
 cols=header.decode().strip().split(',')
 ix=cols.index('ts_code') if 'ts_code' in cols else cols.index('symbol')
 dec=subprocess.Popen(dec_cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 grep_cmd=['grep','-F']
 for code in codes:grep_cmd.extend(['-e',code])
 filt=subprocess.Popen(grep_cmd,stdin=dec.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 dec.stdout.close()
 selected,grep_err=filt.communicate()
 dec_err=dec.stderr.read()
 dec_status=dec.wait()
 if dec_status!=0:raise RuntimeError((path,dec_status,dec_err[-400:]))
 if filt.returncode not in (0,1):raise RuntimeError((path,filt.returncode,grep_err[-400:]))
 for line in selected.splitlines(keepends=True):
  parts=line.split(b',',ix+1)
  if len(parts)>ix:
   code=parts[ix].decode()
   if code in rows:rows[code].append(line)
 out={}
 for code,lines in rows.items():
  if not lines:out[code]=(None,'missing_symbol');continue
  raw=pd.read_csv(io.BytesIO(header+b''.join(lines)),dtype={cols[ix]:str})
  out[code]=to_normalized(raw,day)
 return out

def metrics(bars):
 vol=bars.volume.to_numpy(dtype=float)
 return {'开盘首分钟量_股':float(vol[0]),'开盘前5分钟量_股':float(vol[:5].sum()),
         '开盘前10分钟量_股':float(vol[:10].sum()),'全天分钟合计量_股':float(vol.sum())}

x=pd.read_csv(R79/'100笔D2触跌停_成交量逐笔.csv',dtype={'股票':str})
p=pd.read_csv(ROOT/'preboard_daily_selected.csv.gz',dtype={'stk_code':str})
info=json.loads((ROOT/'inventory.json').read_text())
available=info['server_source_paths']
pairs=p[['trade_date','stk_code','volume']].drop_duplicates(['trade_date','stk_code'])
assert len(x)==100 and len(p)==1880 and len(pairs)>0
bydate={day:g.set_index('stk_code') for day,g in pairs.groupby('trade_date')}
checkpoint=ROOT/'checkpoint_preboard.csv.gz'
checkpoint_sources=ROOT/'checkpoint_sources.json'
if checkpoint.exists():
 pre=pd.read_csv(checkpoint).to_dict('records')
 src=json.loads(checkpoint_sources.read_text())
 done={z['trade_date'] for z in pre}
 print('resuming completed dates',len(done),flush=True)
else:
 pre=[];src=[];done=set()
for i,day in enumerate(sorted(bydate),1):
 if day in done:continue
 g=bydate[day]
 path=available.get(day)
 if path is None:
  for code in g.index:pre.append({'trade_date':day,'stk_code':code,'来源':'缺压缩档','有效':0,'原因':'archive_missing'})
  continue
 got=extract_selected(path,day,list(g.index))
 for code in g.index:
  bars,err=got.get(code,(None,'missing_symbol'))
  row={'trade_date':day,'stk_code':code,'来源':path,'有效':0,'原因':err or ''}
  if bars is not None:
   stat=metrics(bars)
   official=float(g.loc[code,'volume'])
   diff=abs(stat['全天分钟合计量_股']-official)
   if diff<=max(5000,.0001*official):
    row.update(stat)
    row['官方日成交量_股']=official
    row['量差_股']=diff
    row['有效']=1
   else:row['原因']='daily_volume_mismatch_'+str(round(diff,2))
  pre.append(row)
 src.append({'date':day,'path':path,'sha256':sha(path),'size':Path(path).stat().st_size})
 if i%50==0:
  pd.DataFrame(pre).to_csv(checkpoint,index=False,compression='gzip')
  checkpoint_sources.write_text(json.dumps(src,ensure_ascii=False))
  print('preboard archives',i,'/',len(bydate),'pairs',len(pre),flush=True)
pre=pd.DataFrame(pre).sort_values(['trade_date','stk_code'])
pre.to_csv(ROOT/'首板前同刻分钟源_逐股日.csv.gz',index=False,compression='gzip',encoding='utf-8-sig',float_format='%.6f')
(ROOT/'首板前压缩源清单.json').write_text(json.dumps(src,ensure_ascii=False,indent=2)+chr(10))
print('preboard valid',int(pre['有效'].sum()),'total unique',len(pre),'bad reasons',pre[pre['有效'].eq(0)].原因.value_counts().to_dict(),flush=True)

r76=json.loads((R76/'minute_source_manifest.json').read_text())
source={v['id']:v['source'] for v in r76}
d2=[]
for i,r in x.iterrows():
 day=r['D2跌停日'];code=r['股票']
 z=source[code+'|'+r['D0选中日']]
 path=Path(z['path'])
 if path.suffix=='.pkl':
  raw=pd.read_pickle(path)
  bars,err=to_normalized(raw,day)
 elif z['type']=='R73 minute':
  raw=pd.read_csv(path)
  bars,err=to_normalized(raw,day)
 else:
  bars,err=extract_selected(path,day,[code])[code]
 assert bars is not None,(code,day,err)
 stat=metrics(bars)
 official=float(r['D2成交量_万股'])*1e4
 assert abs(stat['全天分钟合计量_股']-official)<=max(5000,.0001*official),(code,day)
 floor=float(r['D2跌停价'])
 d2.append({'股票':code,'D0选中日':r['D0选中日'],'D2跌停日':day,**stat,
            '首分钟整根封死':int(float(bars.high.iloc[0])<=floor+.001),
            '前5分钟均未高于跌停':int(float(bars.high.iloc[:5].max())<=floor+.001),
            '前10分钟均未高于跌停':int(float(bars.high.iloc[:10].max())<=floor+.001),
            '首分钟后曾高于跌停':int((bars.high.iloc[1:]>floor+.001).any()),
            '首5分钟后曾高于跌停':int((bars.high.iloc[5:]>floor+.001).any()),
            '首10分钟后曾高于跌停':int((bars.high.iloc[10:]>floor+.001).any()),
            'D2分钟源':str(path),'D2分钟源SHA256':z['sha256']})
 if (i+1)%25==0:print('D2 days',i+1,flush=True)
d2=pd.DataFrame(d2)
d2.to_csv(ROOT/'D2开盘分钟源_100笔.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
valid=pre[pre['有效'].eq(1)].set_index(['trade_date','stk_code'])
p=p.sort_values('trade_date',ascending=False)
feature=[]
for _,r in x.iterrows():
 d2row=d2[(d2['股票']==r['股票'])&(d2['D0选中日']==r['D0选中日'])].iloc[0]
 history=p[(p.stk_code==r['股票'])&(p['对应首板日']==r['首板日'])].sort_values('trade_date',ascending=False)
 out={**r.to_dict(),**{k:d2row[k] for k in ['开盘首分钟量_股','开盘前5分钟量_股','开盘前10分钟量_股','首分钟整根封死','前5分钟均未高于跌停','前10分钟均未高于跌停','首分钟后曾高于跌停','首5分钟后曾高于跌停','首10分钟后曾高于跌停']}}
 for n in (5,10,20):
  dates=history.head(n).trade_date.tolist()
  complete=len(dates)==n and all((day,r['股票']) in valid.index for day in dates)
  out[f'板前{n}日同刻完整']=int(complete)
  if not complete:continue
  h=valid.loc[[(day,r['股票']) for day in dates]]
  for label in ['开盘首分钟量_股','开盘前5分钟量_股','开盘前10分钟量_股']:
   mean=float(h[label].mean())
   out[f'板前{n}日{label}均值']=mean
   out[f'D2{label}除板前{n}日同刻均值']=float(d2row[label])/mean if mean>0 else np.nan
 feature.append(out)
feat=pd.DataFrame(feature).sort_values(['D2跌停日','股票','D0选中日']).reset_index(drop=True)
assert len(feat)==100
feat.to_csv(ROOT/'100笔D2开盘同刻量比.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
print('coverage',{'n'+str(n):int(feat[f'板前{n}日同刻完整'].sum()) for n in (5,10,20)},flush=True)
checkpoint.unlink(missing_ok=True)
checkpoint_sources.unlink(missing_ok=True)
print('opening floor 34 first minute sealed',int(feat.loc[feat['D2开盘跌停'].eq(1),'首分钟整根封死'].sum()),flush=True)
(ROOT/'extract_verification.json').write_text(json.dumps({'sample':len(feat),'preboard_unique_pairs':len(pre),'preboard_valid_pairs':int(pre['有效'].sum()),'complete_5':int(feat['板前5日同刻完整'].sum()),'complete_10':int(feat['板前10日同刻完整'].sum()),'complete_20':int(feat['板前20日同刻完整'].sum()),'d2_minute_days':len(d2),'all_daily_volume_checked':True,'same_clock_0931_includes_auction':True},ensure_ascii=False,indent=2)+chr(10))
