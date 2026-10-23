from pathlib import Path
import sys,json,hashlib
import pandas as pd
sys.path.insert(0,'/data/cyris/tushare_backfill')
from backfill_tushare_minutes import Tushare,token_from_file,SlidingRateLimiter
ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
RAW=ROOT/'raw';DONE=ROOT/'repair_receipts';DONE.mkdir(exist_ok=True)
api=Tushare(token_from_file(Path('/data/cyris/tushare_backfill/.tushare')),SlidingRateLimiter(60))
def q(api_name,day):
 cols,rows=api.query(api_name,dict(trade_date=day))
 f=pd.DataFrame(rows,columns=cols)
 assert len(f)<6000 and set(f.trade_date)=={day},(api_name,day,len(f))
 return f
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as x:
  for b in iter(lambda:x.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
inventory=ROOT/'capped_pairs.json'
if inventory.exists():keys=json.loads(inventory.read_text())
else:
 keys=[]
 for p in sorted(RAW.glob('daily_*.csv.gz')):
  b=RAW/p.name.replace('daily_','basic_')
  if len(pd.read_csv(p,usecols=['ts_code']))>=6000 or len(pd.read_csv(b,usecols=['ts_code']))>=6000:
   keys.append(p.name[6:23])
 inventory.write_text(json.dumps(keys,indent=2)+'\n')
print('capped pairs',len(keys),flush=True)
for i,key in enumerate(keys,1):
 receipt=DONE/f'{key}.json'
 if receipt.exists():
  prior=json.loads(receipt.read_text())
  assert all(sha(RAW/x['file'])==x['new_sha256'] for x in prior['files'])
  continue
 days=key.split('_')
 assert len(days)==2 and all(len(d)==8 for d in days)
 replacements=[]
 for name,prefix in [('daily','daily'),('daily_basic','basic')]:
  path=RAW/f'{prefix}_{key}.csv.gz'
  old=pd.read_csv(path,dtype={'ts_code':str,'trade_date':str})
  pieces=[q(name,day) for day in days]
  new=pd.concat(pieces,ignore_index=True)
  assert not new.duplicated(['ts_code','trade_date']).any()
  oldkeys=set(zip(old.ts_code,old.trade_date))
  newkeys=set(zip(new.ts_code,new.trade_date))
  assert oldkeys.issubset(newkeys),(name,key,len(oldkeys-newkeys))
  assert len(new)>=len(old),(name,key,len(new),len(old))
  tmp=path.with_suffix('.repairtmp')
  new.to_csv(tmp,index=False,compression='gzip')
  replacements.append((path,tmp,len(old),len(new),sha(path)))
 for path,tmp,old_n,new_n,old_hash in replacements:
  tmp.replace(path)
 info=dict(pair=key,files=[dict(file=path.name,old_rows=old_n,new_rows=new_n,old_sha256=old_hash,new_sha256=sha(path)) for path,tmp,old_n,new_n,old_hash in replacements])
 receipt.write_text(json.dumps(info,indent=2)+'\n')
 if i%10==0 or i==len(keys):print('repaired',i,'/',len(keys),key,[(x['old_rows'],x['new_rows']) for x in info['files']],flush=True)
print('complete',len(keys),flush=True)
