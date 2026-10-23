from pathlib import Path
import sys,json,re,requests,time,threading,hashlib,io
import pandas as pd
from concurrent.futures import ThreadPoolExecutor,as_completed
R=Path('/data/cyris/work/b2_news_factor_r74_20261022');sys.path.insert(0,str(R/'deps'))
from pypdf import PdfReader
(R/'documents').mkdir(exist_ok=True);(R/'company_reports').mkdir(exist_ok=True)
s=pd.read_pickle(R/'cohort709.pkl');cases=s[s.v2_allowed&s.D2D3连续收盘跌停.eq(1)]
mapping={x['code']:x for x in json.loads((R/'cninfo_stockmap.json').read_text())['stockList']}
local=threading.local()
def sess():
 if not hasattr(local,'s'):
  local.s=requests.Session();local.s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'})
 return local.s
def reports(row):
 fn=R/'company_reports'/(row['id'].replace('|','_')+'.json')
 if fn.exists():return json.loads(fn.read_text())
 p={'pageNum':1,'pageSize':30,'column':'szse','tabName':'fulltext','stock':row['symbol']+','+mapping[row['symbol']]['orgId'],'seDate':str((pd.Timestamp(row['d0_date'])-pd.Timedelta(days=400)).date())+'~'+row['d0_date'],'category':'category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;','isHLtitle':'true','sortName':'time','sortType':'desc'}
 x={'id':row['id'],'ok':False}
 try:
  j=sess().post('https://www.cninfo.com.cn/new/hisAnnouncement/query',data=p,timeout=25).json();docs=j.get('announcements') or []
  valid=[d for d in docs if d['adjunctUrl'].split('/')[1]<row['d0_date'] and re.search('(季度|三季度|半年度|年度)报告',d['announcementTitle']) and not re.search('取消|更正|修订|英文',d['announcementTitle'])]
  # Prefer latest reporting period, then publication date and summary.
  def report_rank(d):
   title=re.sub('<[^>]+>','',d['announcementTitle'])
   match=re.search(r'(20\d{2})',title)
   year=int(match.group(1)) if match else 0
   month=9 if re.search('第三季度|三季度',title) else 3 if re.search('第一季度|一季度',title) else 6 if '半年度' in title else 12
   return (year,month,d['adjunctUrl'].split('/')[1],'摘要' in title)
  valid.sort(key=report_rank,reverse=True)
  x.update(ok=True,response=j,selected=valid[0] if valid else None)
 except Exception as e:x['error']=type(e).__name__+str(e)[:150]
 fn.write_text(json.dumps(x,ensure_ascii=False));return x
report_results=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 for f in as_completed([pool.submit(reports,r) for r in cases.to_dict('records')]):report_results.append(f.result())
docs=json.loads((R/'official_docs.json').read_text())
# Same title-based retrieval rule across all candidates; case-only extra documents are not used in pooled semantic factors.
wanted={d['announcementId']:{**d,'purpose':'uniform_factor'} for d in docs if re.search('风险提示|风险的提示|减持.*计划|减持.*预披露|立案|行政处罚|监管警示|业绩预',d['announcementTitle'])}
for r in cases.itertuples():
 x=json.loads((R/'cninfo_windows'/(r.id.replace('|','_')+'.json')).read_text())
 for d in x['items']:
  date=d['adjunctUrl'].split('/')[1]
  if date<=r.d0_date and re.search('异常波动|经营重大合同|资产.*购买|购买.*资产|问询函|减持|财务总监辞职',d['announcementTitle']):
   wanted.setdefault(d['announcementId'],{**d,'purpose':'case_detail'})
for x in report_results:
 if x.get('selected'):wanted.setdefault(x['selected']['announcementId'],{**x['selected'],'purpose':'case_financial_report'})
def download(d):
 aid=d['announcementId'];fn=R/'documents'/(aid+'.json')
 if fn.exists():
  o=json.loads(fn.read_text())
  if o.get('ok'):return o
 url='https://static.cninfo.com.cn/'+d['adjunctUrl'];o={'id':aid,'url':url,'title':d['announcementTitle'],'stock':d['secCode'],'name':d['secName'],'date':d['adjunctUrl'].split('/')[1],'purpose':d['purpose'],'ok':False}
 try:
  for attempt in range(3):
   try:
    z=sess().get(url,timeout=25);z.raise_for_status()
    if not z.content.startswith(b'%PDF'):raise ValueError('not a PDF response')
    break
   except Exception:
    if attempt==2:raise
    time.sleep(1+attempt)
  (R/'documents'/(aid+'.pdf')).write_bytes(z.content);reader=PdfReader(io.BytesIO(z.content))
  limit=min(len(reader.pages),25 if d['purpose']=='case_financial_report' else 40)
  text='\n\n'.join('PAGE '+str(i+1)+'\n'+(reader.pages[i].extract_text() or '') for i in range(limit))
  (R/'documents'/(aid+'.txt')).write_text(text)
  o.update(ok=True,sha256=hashlib.sha256(z.content).hexdigest(),pages=len(reader.pages),pages_extracted=limit,chars=len(text),text_file=aid+'.txt')
 except Exception as e:o['error']=type(e).__name__+': '+str(e)[:150]
 fn.write_text(json.dumps(o,ensure_ascii=False));time.sleep(.1);return o
results=[]
with ThreadPoolExecutor(max_workers=4) as pool:
 for f in as_completed([pool.submit(download,d) for d in wanted.values()]):
  results.append(f.result())
  if len(results)%40==0:print('documents',len(results),'/',len(wanted),'ok',sum(x['ok'] for x in results),flush=True)
pd.DataFrame(results).to_csv(R/'document_fetch_coverage.csv',index=False,encoding='utf-8-sig')
print('DONE documents',len(results),'ok',sum(x['ok'] for x in results),flush=True)

