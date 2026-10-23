from pathlib import Path
import re,json,hashlib
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_news_factor_r74_20261022')
s=pd.read_pickle(R/'cohort709.pkl')
# Freeze transparent observations; no optimization or fitting against outcomes.
PATTERNS={
'abnormal':r'(股票|股价|证券).*异常波动|股票异常波动',
'severe_abnormal':r'严重异常波动',
'risk_warning':r'股票交易风险提示|股票交易风险.{0,3}提示|股票交易.*风险提示|股票交易风险的提示|股票交易风险提示性',
'reduction_plan':r'减持.*(?:计划|预披露)|减持股份预披露',
'enforcement':r'立案调查|立案告知|行政处罚|监管警示|纪律处分|监管函|警示函',
'earnings_negative_title':r'预亏|预减|亏损|业绩下滑',
'earnings_forecast':r'业绩预告|业绩预盈|业绩预亏|业绩预增|业绩预减',
'restructuring':r'重大资产重组|购买资产|收购.{0,15}股权|资产购买|控制权.{0,8}变更',
'pledge_freeze':r'股份.{0,8}冻结|股份.{0,8}质押|股权.{0,8}冻结|股权.{0,8}质押',
'positive_catalyst':r'中标|重大合同|增持计划|回购股份方案|业绩预增|业绩预盈'}
SEMANTIC={
'valuation_warning':r'(?:市盈率|市净率|估值)[^。；]{0,100}(?:远高于|明显高于|显著高于|高于行业|高于同行|较高|过高|偏高)',
'theme_denial':r'(?:不涉及|未涉及|未开展|未从事)[^。；]{0,35}(?:业务|经营|概念)|无[^。；]{0,15}经营资质',
'deal_uncertainty':r'(?:交易|重组|收购)[^。；]{0,100}(?:不确定性|无法纳入|尚未完成)',
'earnings_loss_mention':r'(?:净利润|经营业绩|业绩)[^。；]{0,70}(?:亏损|预亏)|(?:归属于)[^。；]{0,70}净利润[^。；]{0,25}-[0-9]'}
spec={'title_patterns':PATTERNS,'body_patterns':SEMANTIC,'body_scope':'uniform retrieved risk warnings / reduction plans / enforcement / earnings forecasts; excludes case-only extra documents','rules':{'explicit_warning_5':'D1-known risk warning publication days >=1 in D0 plus prior5 sessions','repeated_warning_5':'D1-known risk warning publication days >=2 in same window','specific_adverse_5':'D1-known reduction plan (not completed/terminated) or enforcement or explicit earnings-negative title','valuation_warning_5':'D1-known valuation-warning wording in uniform document set','theme_denial_5':'D1-known explicit theme-denial wording in uniform document set','media_adverse_5':'D1-known non-price-only media headline adverse mention; only complete public search retrievals'}}
(R/'feature_rules.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n')
def clean(t):return re.sub(r'\s+','',re.sub('<[^>]+>','',str(t or '')))
def title_key(t):return re.sub(r'[\W_]','',clean(t))
official={}
for raw in json.loads((R/'official_docs.json').read_text()):
 aid=raw['announcementId'];date=raw['adjunctUrl'].split('/')[1];title=clean(raw['announcementTitle'])
 d={'document_id':'cninfo:'+aid,'stock6':raw['secCode'],'historical_name':clean(raw['secName']),'source':'巨潮资讯/交易所公告','published':date,'time_precision':'date','available_at':str((pd.Timestamp(date)+pd.Timedelta(days=1)).date())+' 00:00:00','title':title,'url':'https://static.cninfo.com.cn/'+raw['adjunctUrl'],'body_available':False,'semantic_evidence':{}}
 for k,p in PATTERNS.items():d[k]=int(bool(re.search(p,title)))
 # Query replies and appendices are not additional abnormal trading events.
 if re.search('回复|复函|问询函|说明|法律意见',title) and '公告' not in title:d['abnormal']=d['severe_abnormal']=0
 if re.search('减持.*(?:完成|届满|实施完毕|结果|提前终止|终止减持)',title):d['reduction_plan']=0
 for k in SEMANTIC:d[k]=0
 meta=R/'documents'/(aid+'.json')
 if meta.exists():
  m=json.loads(meta.read_text());d['body_available']=m.get('ok',False)
  if m.get('ok') and m.get('purpose')=='uniform_factor':
   body=clean((R/'documents'/(aid+'.txt')).read_text())
   for k,p in SEMANTIC.items():
    hits=[x.group(0) for x in re.finditer(p,body)]
    d[k]=int(bool(hits));d['semantic_evidence'][k]=list(dict.fromkeys(hits))[:4]
 official[aid]=d
(R/'classified_official_documents.json').write_text(json.dumps(list(official.values()),ensure_ascii=False))
all_features=[];links=[];coverage=[];media_global={}
for r in s.to_dict('records'):
 key=r['id'];fn=key.replace('|','_')+'.json';raw=json.loads((R/'cninfo_windows'/fn).read_text())
 anns={d['announcementId']:official[d['announcementId']] for d in raw['items']}
 hist=[d for d in anns.values() if d['published']<=r['d0_date']]
 name=max(hist,key=lambda d:d['published'])['historical_name'] if hist else r['stk_name']
 media_file=R/'sina_windows'/fn;media_raw=json.loads(media_file.read_text()) if media_file.exists() else None
 media_ok=bool(media_raw and media_raw.get('ok'))
 media=[];discard=0
 if media_raw:
  # Uniform pooled search: code first; use name only if code query returned zero. Extra case name searches do not enrich the factors.
  code_items=[];name_items=[]
  for q in media_raw.get('queries',[]):
   items=(q.get('response',{}).get('data') or {}).get('list') or []
   (code_items if q['params']['q']==r['symbol'] else name_items).extend(items)
  candidates=code_items or name_items
  aliases={clean(x['historical_name']).replace('*ST','').removeprefix('ST') for x in anns.values()}|{clean(name).replace('*ST','').removeprefix('ST')}
  seen=set()
  for x in candidates:
   title=re.sub('<[^>]+>','',x.get('title') or '');intro=re.sub('<[^>]+>','',x.get('intro') or x.get('searchSummary') or '');text=clean(title+intro)
   if not (re.search(r'(?<!\d)'+r['symbol']+r'(?!\d)',text) or any(len(n)>=3 and n in text for n in aliases)):discard+=1;continue
   stamp=x.get('dataTime') or x.get('time') or ''
   if not re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d',stamp):discard+=1;continue
   if not r['start10']<=stamp<=r['d1_date']+' 09:30:00':discard+=1;continue
   ident=(stamp[:10],title_key(title))
   if ident in seen:continue
   seen.add(ident)
   adverse=bool(re.search('立案|行政处罚|监管警示|警示函|预亏|预减|亏损|减持|退市|不涉及|无.{0,8}资质|业绩.{0,12}(?:下降|下滑)',title))
   corporate=bool(re.search('公告|业绩|亏损|减持|增持|回购|中标|合同|重组|收购|监管|处罚|调查|主营|业务|澄清',title))
   price_only=bool(re.search('涨停|跌停|主力|龙虎榜|换手|资金流|盘中|成交|融资|融券|收盘|涨[0-9]|跌[0-9]',title)) and not corporate
   mid='sina:'+str(x.get('dataid') or hashlib.sha256(x.get('url','').encode()).hexdigest()[:20])
   d={'document_id':mid,'stock6':r['symbol'],'historical_name':name,'source':x.get('media_show') or '新浪收录','published':stamp,'available_at':stamp,'time_precision':'second','title':title,'url':x.get('url',''),'snippet':intro,'media_adverse':int(adverse and not price_only),'price_only':int(price_only)}
   media.append(d);media_global[mid]=d
 coverage.append({'id':key,'stock':r['stk_code'],'D0':r['d0_date'],'official_ok':int(raw['ok']),'official_docs':len(anns),'media_complete':int(media_ok),'media_docs_observed':len(media),'media_discarded_unverified_entity_or_time':discard,'media_error':(media_raw or {}).get('error','not retrieved' if media_raw is None else '')})
 row={k:r[k] for k in ['id','stk_code','d0_date','d1_date','d2_date','d3_date','v2_allowed','board_streak_days','entry','d2_close','D2开收跌停','D2D3连续收盘跌停','真实类别']}
 row.update(historical_name=name,official_complete=int(raw['ok']),media_complete=int(media_ok),close_net=r['d2_close']/r['entry']*.999/1.001-1)
 for timing,cutoff in [('d0',r['d0_date']+' 15:00:00'),('d1',r['d1_date']+' 09:30:00')]:
  for w in [5,10]:
   # Window includes D0 and the preceding w exchange sessions plus any intervening non-trading days.
   start=r['start'+str(w)]
   selected=[d for d in anns.values() if start<=d['published'] and d['available_at']<=cutoff]
   prefix=f'{timing}_{w}_'
   row[prefix+'announcement_count']=len(selected)
   for k in list(PATTERNS)+list(SEMANTIC):
    row[prefix+k]=sum(d[k] for d in selected)
   row[prefix+'risk_warning_days']=len({d['published'] for d in selected if d['risk_warning']})
   selected_media=[d for d in media if start<=d['published'] and d['available_at']<=cutoff]
   for field in ['news_count','media_adverse','price_only']:
    val=len(selected_media) if field=='news_count' else sum(d[field] for d in selected_media)
    row[prefix+field]=val if media_ok else np.nan
 for d in list(anns.values())+media:
  links.append({'id':key,'股票':r['stk_code'],'当时简称':name,'D0':r['d0_date'],'D1':r['d1_date'],'document_id':d['document_id'],'来源':d['source'],'发布时间':d['published'],'时间精度':d['time_precision'],'保守可用时间':d['available_at'],'D0收盘可用':int(d['available_at']<=r['d0_date']+' 15:00:00'),'D1买前可用':int(d['available_at']<=r['d1_date']+' 09:30:00'),'近5日内':int(d['published']>=r['start5']),'标题':d['title'],'链接':d['url'],'明确风险提示':d.get('risk_warning',0),'估值提示':d.get('valuation_warning',0),'题材澄清':d.get('theme_denial',0),'减持计划':d.get('reduction_plan',0),'媒体负面提及':d.get('media_adverse',0),'仅价格点评':d.get('price_only',0)})
 all_features.append(row)
f=pd.DataFrame(all_features)
f['explicit_warning_5']=f.d1_5_risk_warning_days.ge(1).astype(int)
f['repeated_warning_5']=f.d1_5_risk_warning_days.ge(2).astype(int)
f['specific_adverse_5']=(f.d1_5_reduction_plan+f.d1_5_enforcement+f.d1_5_earnings_negative_title).gt(0).astype(int)
f['valuation_warning_5']=f.d1_5_valuation_warning.gt(0).astype(int)
f['theme_denial_5']=f.d1_5_theme_denial.gt(0).astype(int)
f['media_adverse_5']=np.where(f.media_complete.eq(1),f.d1_5_media_adverse.ge(1).astype(int),np.nan)
f.to_pickle(R/'features709.pkl')
f.to_csv(R/'709候选_新闻因子.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
f[f.v2_allowed].to_csv(R/'306入选_新闻因子.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(links).to_csv(R/'逐笔公告新闻_时间与来源.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(coverage).to_csv(R/'逐笔检索覆盖.csv',index=False,encoding='utf-8-sig')
(R/'media_documents.json').write_text(json.dumps(list(media_global.values()),ensure_ascii=False))
print('features',len(f),'links',len(links),'media complete',f.media_complete.sum(),'media unique',len(media_global),flush=True)
print(f[f.v2_allowed & f.D2D3连续收盘跌停.eq(1)][['stk_code','historical_name','d0_date','d1_5_risk_warning_days','d1_5_abnormal','d1_5_reduction_plan','d1_5_valuation_warning','d1_5_theme_denial','d1_5_news_count']].to_string(index=False))

