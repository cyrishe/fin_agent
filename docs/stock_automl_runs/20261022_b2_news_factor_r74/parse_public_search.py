from pathlib import Path
import re,json,sys,time,hashlib,requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor,as_completed
R=Path('/data/cyris/work/b2_news_factor_r74_20261022');sys.path.insert(0,str(R/'deps'))
from bs4 import BeautifulSoup
(R/'public_pages').mkdir(exist_ok=True)
queries={x['id']:x for x in json.loads((R/'public_web_queries.json').read_text())}
observed={}; receipts={}
for p in sorted((R/'public_search').glob('batch_*.json')):
 b=json.loads(p.read_text());text=b['result']
 blocks=re.split(r'\n(?=[^\n]+\s\(https?://[^\s]+\)\n)',text)
 for q in b['queries']:
  receipts[q['id']]={'id':q['id'],'query':q['q'],'searched':1,'returned_blocks':len(blocks),'batch':p.name}
 for block in blocks:
  m=re.search(r'^([^\n]+?) \((https?://[^\s]+)\)\n',block)
  if not m:continue
  title,url=m.groups()
  for q in b['queries']:
   if not(q['name'] in block or re.search(r'(?<!\d)'+q['code']+r'(?!\d)',block)):continue
   # Search-engine date filters are not trusted. Only relevant dated URLs are candidates for publication verification.
   dm=re.search(r'/(20\d{2})[-/]?(\d\d)[-/]?(\d\d)(?:/|[^0-9])',url)
   if not dm:continue
   date='-'.join(dm.groups())
   if not q['start']<=date<=q['d1']:continue
   v=observed.setdefault(url,{'url':url,'title':title,'url_date':date,'ids':[],'search_excerpt':block[:1200]})
   if q['id'] not in v['ids']:v['ids'].append(q['id'])
def fetch(v):
 fn=R/'public_pages'/(hashlib.sha256(v['url'].encode()).hexdigest()+'.json')
 if fn.exists():
  o=json.loads(fn.read_text());o['ids']=list(set(o['ids'])|set(v['ids']));fn.write_text(json.dumps(o,ensure_ascii=False));return o
 o=dict(v,verified=False)
 try:
  z=requests.get(v['url'],headers={'User-Agent':'Mozilla/5.0'},timeout=(8,20));z.raise_for_status()
  if z.content.startswith(b'%PDF'):raise ValueError('PDF already handled by official source; not media timestamp')
  z.encoding=z.apparent_encoding
  soup=BeautifulSoup(z.text,'html.parser');dates=[]
  for m in soup.find_all('meta'):
   key=str(m.get('property') or m.get('name') or m.get('itemprop') or '').lower()
   if key in ['article:published_time','publishdate','pubdate','publish_date','datepublished','date','og:release_date','publish_time']:
    dates.append(('meta:'+key,str(m.get('content') or '')))
  for n in soup.select('time, .date-source .date, #pub_date, .pub_date, .publish-time'):
   dates.append(('visible_time',n.get('datetime') or n.get_text(' ',strip=True)))
  for method,st in dates:
   m=re.search(r'(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?:[T\s]+(\d{1,2}):(\d\d)(?::(\d\d))?)?',st)
   if not m:continue
   y,mo,d,h,mi,se=m.groups();date=f'{y}-{int(mo):02}-{int(d):02}'
   if date!=v['url_date']:continue
   stamp=date+(f' {int(h):02}:{mi}:{se or "00"}' if h else '')
   o.update(verified=True,published=stamp,time_precision='minute' if h else 'date',time_evidence=st,time_method=method)
   break
  for n in soup(['script','style','nav','footer']):n.decompose()
  text=soup.get_text(' ',strip=True)
  o['text_sha256']=hashlib.sha256(text.encode()).hexdigest()
  o['entity_verified_ids']=[i for i in v['ids'] if queries[i]['name'] in text or queries[i]['code'] in text]
  title=soup.find('h1');o['page_title']=title.get_text(' ',strip=True) if title else v['title']
  o['retrieved_at']=pd.Timestamp.now(tz='Asia/Shanghai').isoformat()
 except Exception as e:o['error']=type(e).__name__+': '+str(e)[:150]
 fn.write_text(json.dumps(o,ensure_ascii=False));return o
pages=[]
with ThreadPoolExecutor(max_workers=4) as pool:
 for f in as_completed([pool.submit(fetch,v) for v in observed.values()]):pages.append(f.result())
rows=[]
for p in pages:
 if not p.get('verified'):continue
 for ident in p.get('entity_verified_ids',[]):
  q=queries[ident];stamp=p['published']
  available=stamp if p['time_precision']!='date' else str((pd.Timestamp(stamp)+pd.Timedelta(days=1)).date())+' 00:00:00'
  if not(q['start']<=stamp and available<=q['d1']+' 09:30:00'):continue
  title=p['page_title']
  corporate=bool(re.search('公告|业绩|亏损|减持|增持|回购|中标|合同|重组|收购|监管|处罚|调查|主营|业务|澄清',title))
  price_only=bool(re.search('涨停|跌停|主力|龙虎榜|换手|资金流|盘中|成交|融资|融券|收盘',title)) and not corporate
  adverse=bool(re.search('立案|行政处罚|监管警示|警示函|预亏|预减|亏损|减持|退市|不涉及|无.{0,8}资质|业绩.{0,12}(?:下降|下滑)',title)) and not price_only
  rows.append({'id':ident,'股票':q['code'],'简称':q['name'],'D0':q['d0'],'发布时间':stamp,'时间精度':p['time_precision'],'保守可用时间':available,'D0收盘可用':int(available<=q['d0']+' 15:00:00'),'D1买前可用':1,'标题':title,'仅价格点评':int(price_only),'负面提及':int(adverse),'链接':p['url']})
df=pd.DataFrame(rows).drop_duplicates(['id','链接']) if rows else pd.DataFrame(columns=['id','链接','负面提及'])
df.to_csv(R/'公开搜索_已核验事前新闻.csv',index=False,encoding='utf-8-sig')
c=[]
for ident,q in queries.items():
 hits=df[df.id.eq(ident)]
 c.append({**receipts.get(ident,{'id':ident,'searched':0}),'verified_prebuy_news':len(hits),'verified_adverse_mentions':int(hits['负面提及'].sum()),'corpus_complete':0})
pd.DataFrame(c).to_csv(R/'306公开搜索_覆盖.csv',index=False,encoding='utf-8-sig')
summary={'searched_windows':len(receipts),'target_windows':306,'candidate_pages':len(pages),'verified_page_time':sum(p.get('verified',False) for p in pages),'verified_prebuy_links':len(df),'windows_with_verified_prebuy_news':int(df.id.nunique()),'complete_corpus':False,'note':'Public web search is a supplement, not an exhaustive historical index; unresolved dates excluded; not used for model economic comparison.'}
(R/'public_search_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False),flush=True)

