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

R83=Path('/data/cyris/work/b2_prebuy_volume_warning_r83_20261022')
R71=Path('/data/cyris/work/b2_board_volume_r71_20261022')
R80=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
board=pd.read_csv(R71/'B2_V2放行连板量能特征_306笔.csv',dtype={'股票':str})
hist=pd.read_csv(R71/'preboard_daily_source.csv.gz',dtype={'stk_code':str})
hist=hist.merge(board[['股票','首板日']].drop_duplicates(),left_on=['stk_code','对应首板日'],right_on=['股票','首板日'],validate='many_to_one')
pairs=hist[['trade_date','stk_code','volume']].drop_duplicates(['trade_date','stk_code'])
assert len(board)==306 and len(pairs)>5000
available=json.loads((R80/'inventory.json').read_text())['server_source_paths']
old=pd.read_csv(R80/'首板前同刻分钟源_逐股日.csv.gz',dtype={'stk_code':str})
old=old[old['有效'].eq(1)].drop_duplicates(['trade_date','stk_code'])
keys=set(zip(pairs.trade_date,pairs.stk_code))
old=old[[tuple(x) in keys for x in zip(old.trade_date,old.stk_code)]]
print('pairs',len(pairs),'reuse',len(old),'archive-hit',pairs.trade_date.isin(available).sum(),flush=True)
rows=old[['trade_date','stk_code','开盘首分钟量_股','开盘前5分钟量_股','开盘前10分钟量_股','官方日成交量_股','量差_股','来源','有效','原因']].to_dict('records')
done=set(zip(old.trade_date,old.stk_code))
todo=pairs[[tuple(x) not in done for x in zip(pairs.trade_date,pairs.stk_code)]]
bydate={d:g.set_index('stk_code') for d,g in todo.groupby('trade_date')}
sourcehash={}
for n,day in enumerate(sorted(bydate),1):
 g=bydate[day]
 path=available.get(day)
 if path is None:
  for code in g.index:rows.append({'trade_date':day,'stk_code':code,'有效':0,'原因':'archive_missing','来源':''})
  continue
 got=extract_selected(path,day,list(g.index))
 for code in g.index:
  bars,err=got.get(code,(None,'missing_symbol'))
  row={'trade_date':day,'stk_code':code,'有效':0,'原因':err or '','来源':path}
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
  rows.append(row)
 sourcehash[path]=sha(path)
 if n%40==0:
  pd.DataFrame(rows).to_csv(R83/'preboard_checkpoint.csv.gz',index=False,compression='gzip')
  print('dates',n,'/',len(bydate),'rows',len(rows),flush=True)
data=pd.DataFrame(rows).sort_values(['trade_date','stk_code'])
assert len(data)==len(pairs)
data.to_csv(R83/'首板前同刻分钟_逐股日.csv.gz',index=False,compression='gzip',encoding='utf-8-sig')
(R83/'新读取压缩源SHA.json').write_text(json.dumps(sourcehash,ensure_ascii=False,indent=2))
print('DONE',len(data),'valid',int(data['有效'].sum()),'bad',data.loc[data['有效'].eq(0),'原因'].value_counts().to_dict(),flush=True)
