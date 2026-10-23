"""Recent public announcement/news snapshots; research output only, no orders or database writes."""
from pathlib import Path
import argparse,csv,json,hashlib,time,re,requests
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Asia/Shanghai')
def now():return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
def visible_at(record,cutoff,live=True):
 return record['available_at']<=cutoff and (not live or record['first_seen_at']<=cutoff)
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--stock',action='append',help='six-digit code; repeat for multiple candidates')
 p.add_argument('--days',type=int,default=20,help='calendar days, including today')
 p.add_argument('--out',type=Path)
 p.add_argument('--with-media',action='store_true',help='public Sina code search; stops at login/rate limit')
 p.add_argument('--self-test',action='store_true')
 a=p.parse_args()
 if a.self_test:
  r={'available_at':'2026-10-22 08:00:00','first_seen_at':'2026-10-22 09:00:00'}
  assert visible_at(r,'2026-10-22 09:30:00')
  assert not visible_at(r,'2026-10-22 08:30:00')
  assert not visible_at({**r,'available_at':'2026-10-23 00:00:00'},'2026-10-22 09:30:00')
  print('PASS: publication and first-observation time boundaries');return
 if not a.stock or a.out is None:p.error('--stock and --out required')
 if not all(re.fullmatch(r'\d{6}',x) for x in a.stock):p.error('stock must be six digits')
 if not 1<=a.days<=60:p.error('--days must be 1..60')
 a.out.mkdir(parents=True,exist_ok=True);(a.out/'raw').mkdir(exist_ok=True)
 cache=a.out/'records.json';records=json.loads(cache.read_text()) if cache.exists() else {}
 session=requests.Session();session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'})
 start=(datetime.now(TZ)-timedelta(days=a.days-1)).date().isoformat();end=datetime.now(TZ).date().isoformat()
 mapping=session.get('https://www.cninfo.com.cn/new/data/szse_stock.json',timeout=20)
 mapping.raise_for_status();mapping={x['code']:x for x in mapping.json()['stockList']}
 coverage=[];media_stopped=False
 def save(key,record):
  # Live observation proves public availability now; never backdate first_seen from publication date.
  if key not in records:records[key]={**record,'first_seen_at':now()}
  records[key]['last_seen_at']=now()
 def raw(source,stock,page,payload):
  fn=f'{source}_{stock}_{page}_{datetime.now(TZ).strftime("%Y%m%dT%H%M%S")}.json'
  (a.out/'raw'/fn).write_text(json.dumps(payload,ensure_ascii=False))
 for stock in a.stock:
  c={'stock':stock,'start':start,'end':end,'announcement_complete':False,'media_complete':False}
  try:
   if stock not in mapping:raise ValueError('stock missing from provider mapping')
   for page in range(1,101):
    params={'stock':stock+','+mapping[stock]['orgId'],'column':'szse','tabName':'fulltext','pageNum':page,'pageSize':30,'seDate':start+'~'+end,'isHLtitle':'true'}
    z=session.post('https://www.cninfo.com.cn/new/hisAnnouncement/query',data=params,timeout=25);z.raise_for_status();j=z.json();raw('cninfo',stock,page,j)
    if 'announcements' not in j:raise ValueError('unexpected announcement response')
    for d in j.get('announcements') or []:
     date=d['adjunctUrl'].split('/')[1]
     save('cninfo:'+d['announcementId'],{'stock':stock,'source':'cninfo','published_at':date,'time_precision':'date','available_at':now(),'title':re.sub('<[^>]+>','',d['announcementTitle']),'url':'https://static.cninfo.com.cn/'+d['adjunctUrl']})
    if not j.get('hasMore'):c['announcement_complete']=True;break
    time.sleep(1)
  except Exception as e:c['announcement_error']=str(e)[:200]
  if a.with_media and not media_stopped:
   try:
    for page in range(1,51):
     params={'q':stock,'tp':'news','sort':1,'page':page,'size':20,'from':'search_result','stime':int(datetime.fromisoformat(start).replace(tzinfo=TZ).timestamp()),'etime':int(datetime.now(TZ).timestamp())}
     time.sleep(2);z=session.get('https://search.sina.com.cn/api/news',params=params,timeout=20)
     if z.status_code in (401,403,429):media_stopped=True;raise RuntimeError('media access/login/rate limit; stopped, no retry')
     z.raise_for_status();j=z.json();raw('sina',stock,page,j)
     if '登录' in str(j.get('message','')):media_stopped=True
     if j.get('code')!=0:raise RuntimeError(str(j.get('message','unexpected response')))
     data=j.get('data') or {};items=data.get('list') or []
     for d in items:
      title=re.sub('<[^>]+>','',d.get('title') or '');intro=re.sub('<[^>]+>','',d.get('intro') or '')
      if stock not in title+intro and mapping[stock]['zwjc'] not in title+intro:continue
      stamp=d.get('dataTime') or ''
      if not re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d',stamp) or not start<=stamp<=now():continue
      url=d.get('url','');save('sina:'+hashlib.sha256(url.encode()).hexdigest(),{'stock':stock,'source':'sina','published_at':stamp,'time_precision':'second','available_at':stamp,'title':title,'url':url})
     if page*20>=int(data.get('total') or 0) or not items:c['media_complete']=True;break
   except Exception as e:c['media_error']=str(e)[:200]
  elif a.with_media:c['media_error']='provider access stop earlier in this run'
  coverage.append(c)
 cache.write_text(json.dumps(records,ensure_ascii=False,indent=2))
 stamp=now();payload={'as_of':stamp,'coverage':coverage,'records':[r for r in records.values() if r['stock'] in a.stock and start<=r['published_at'] and visible_at(r,stamp)]}
 (a.out/'latest_snapshot.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
 (a.out/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2))
 print(json.dumps({'snapshot':str(a.out/'latest_snapshot.json'),'visible_records':len(payload['records']),'coverage':coverage},ensure_ascii=False))
if __name__=='__main__':main()

