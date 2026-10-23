from pathlib import Path
import sys,json,re,requests,io,time,hashlib,shutil
from concurrent.futures import ThreadPoolExecutor,as_completed
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022');A=Path('/data/cyris/work/b2_news_factor_r74_20261022')
sys.path.insert(0,str(A/'deps'));from pypdf import PdfReader
(R/'initial_coverage').mkdir(exist_ok=True);(R/'extra_documents').mkdir(exist_ok=True)
for p in list(R.iterdir()):
 if p.is_file() and p.suffix in ['.py','.pkl','.csv','.json','.joblib']:shutil.copy2(p,R/'initial_coverage'/p.name)
w=[d for d in json.loads((A/'official_docs.json').read_text()) if re.search('异常波动|澄清',d['announcementTitle']) and not re.search('回复|复函|问询|法律意见',d['announcementTitle'])]
def fetch(d):
 aid=d['announcementId'];fn=R/'extra_documents'/(aid+'.json')
 if (A/'documents'/(aid+'.txt')).exists():return {'id':aid,'ok':True,'existing':True,'file':str(A/'documents'/(aid+'.txt'))}
 if fn.exists():return json.loads(fn.read_text())
 o={'id':aid,'title':d['announcementTitle'],'stock':d['secCode'],'url':'https://static.cninfo.com.cn/'+d['adjunctUrl'],'ok':False}
 try:
  z=requests.get(o['url'],timeout=25);z.raise_for_status()
  assert z.content.startswith(b'%PDF')
  reader=PdfReader(io.BytesIO(z.content));txt='\n'.join((p.extract_text() or '') for p in reader.pages[:20])
  (R/'extra_documents'/(aid+'.pdf')).write_bytes(z.content);(R/'extra_documents'/(aid+'.txt')).write_text(txt)
  o.update(ok=True,sha256=hashlib.sha256(z.content).hexdigest(),pages=len(reader.pages),pages_extracted=min(len(reader.pages),20),file=str(R/'extra_documents'/(aid+'.txt')))
 except Exception as e:o['error']=str(e)[:180]
 fn.write_text(json.dumps(o,ensure_ascii=False));return o
out=[]
with ThreadPoolExecutor(max_workers=4) as pool:
 for fut in as_completed([pool.submit(fetch,d) for d in w]):
  out.append(fut.result())
  if len(out)%150==0:print('documents',len(out),'/',len(w),'ok',sum(d['ok'] for d in out),flush=True)
(R/'extra_document_coverage.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print('DONE',len(out),'ok',sum(d['ok'] for d in out),flush=True)

