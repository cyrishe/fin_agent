from pathlib import Path
import json,re,hashlib,bisect
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022');A=Path('/data/cyris/work/b2_news_factor_r74_20261022')
s=pd.read_pickle(A/'cohort709.pkl');cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text())
docs={x['document_id'].split(':')[1]:x for x in json.loads((A/'classified_official_documents.json').read_text())}
# Uniform expansion to all abnormal-trading/clarification announcements.
patterns=json.loads((A/'feature_rules.json').read_text())['body_patterns']
for aid,d in docs.items():
 if re.search('异常波动|澄清',d['title']) and not re.search('回复|复函|问询|法律意见',d['title']):
  path=R/'extra_documents'/(aid+'.txt')
  if not path.exists():path=A/'documents'/(aid+'.txt')
  if path.exists():
   text=re.sub(r'\s+','',path.read_text())
   for field in ['valuation_warning','theme_denial','deal_uncertainty']:
    hits=list(dict.fromkeys(x.group(0) for x in re.finditer(patterns[field],text)))
    d[field]=int(bool(hits));d['semantic_evidence'][field]=hits[:8]
generic=r'热点概念|公司资产注入|公司基本面|未开展经营|不涉及上市公司|不涉及重大资产重组'

for d in docs.values():
 ev=d['semantic_evidence'].get('theme_denial',[])
 d['specific_denial']=int(any(not re.search(generic,t) for t in ev))
 d['generic_denial']=int(any(re.search(generic,t) for t in ev))
def dayavailable(t):return bisect.bisect_left(cal,t[:10])
rows=[];trace=[];flowaud=[]
for r in s.to_dict('records'):
 i=cal.index(r['d0_date']);raw=json.loads((A/'cninfo_windows'/(r['id'].replace('|','_')+'.json')).read_text())
 seen={x['announcementId']:docs[x['announcementId']] for x in raw['items']}
 eligible=[d for d in seen.values() if d['available_at']<=r['d0_date']+' 15:00:00']
 row={'id':r['id'],'stock':r['stk_code'],'D0':r['d0_date'],'D1':r['d1_date'],'v2_allowed':r['v2_allowed'],'连续跌停':r['D2D3连续收盘跌停']}
 groups={name:[d for d in eligible if lo<=dayavailable(d['available_at'])<=hi] for name,lo,hi in [('r5',i-4,i),('p5',i-9,i-5),('r3',i-2,i),('p3',i-5,i-3)]}
 for name,dd in groups.items():
  row['news_docs_'+name]=len(dd)
  row['news_days_'+name]=len(set(d['published'] for d in dd))
  row['risk_days_'+name]=len(set(d['published'] for d in dd if d['risk_warning']))
  row['abnormal_days_'+name]=len(set(d['published'] for d in dd if d['abnormal']))
  row['families_'+name]=len(set(re.sub(r'[\W\d_]|关于|公告|股份有限公司','',d['title']) for d in dd))
 row['news_day_change']=row['news_days_r5']-row['news_days_p5']
 row['news_doc_logchange']=np.log((1+row['news_docs_r5'])/(1+row['news_docs_p5']))
 row['risk_change']=row['risk_days_r3']-row['risk_days_p3']
 row['risk_share']=row['risk_days_r5']/max(row['news_days_r5'],1)
 row['new_families']=len({re.sub(r'[\W\d_]|关于|公告|股份有限公司','',d['title']) for d in groups['r5']}-{re.sub(r'[\W\d_]|关于|公告|股份有限公司','',d['title']) for d in groups['p5']})
 for field in ['valuation_warning','specific_denial','generic_denial','reduction_plan','enforcement','deal_uncertainty']:
  row[field]=int(any(d[field] for d in groups['r5']))
 for d in eligible:
  if dayavailable(d['available_at'])>=i-4 and d['theme_denial']:
   trace.append({'id':r['id'],'股票':r['stk_code'],'D0':r['d0_date'],'公告日期':d['published'],'保守可用时间':d['available_at'],'具体题材否认':d['specific_denial'],'泛化否认措辞':d['generic_denial'],'原句':' | '.join(d['semantic_evidence']['theme_denial']),'链接':d['url']})
 f=pd.read_pickle(R/'flow_raw'/(r['stk_code']+'.pkl')).copy()
 if len(f):
  f['trade_date']=pd.to_datetime(f.trade_date).dt.strftime('%Y-%m-%d')
  assert not f.trade_date.duplicated().any()
  f=f.set_index('trade_date')
 else:f=pd.DataFrame(columns=['trade_date']).set_index('trade_date')
 dates=cal[max(0,i-20):i+1];g=f.reindex(dates)
 for k in ['main','huge','large','medium','small']:
  for side in ['buy','sell']:
   col=f'{k}_{side}_value'
   if col not in g:g[col]=np.nan
 total=g[[f'{k}_buy_value' for k in ['huge','large','medium','small']]].sum(axis=1,min_count=4)
 selltotal=g[[f'{k}_sell_value' for k in ['huge','large','medium','small']]].sum(axis=1,min_count=4)
 for k in ['main','huge','large','medium','small']:
  net=g[f'{k}_buy_value']-g[f'{k}_sell_value'];balance=net/(g[f'{k}_buy_value']+g[f'{k}_sell_value']).replace(0,np.nan)
  frac=net/total.replace(0,np.nan);row[f'flow_{k}_net_D0']=float(net.iloc[-1]);row[f'flow_{k}_frac_D0']=float(frac.iloc[-1]);row[f'flow_{k}_balance_D0']=float(balance.iloc[-1])
  row[f'flow_{k}_pre5']=float(frac.iloc[-6:-1].mean()) if frac.iloc[-6:-1].notna().sum()==5 else np.nan
  row[f'flow_{k}_pre20']=float(frac.iloc[:-1].mean()) if frac.iloc[:-1].notna().sum()==20 else np.nan
  row[f'flow_{k}_change']=row[f'flow_{k}_frac_D0']-row[f'flow_{k}_pre5']
  row[f'flow_{k}_trend5']=float(frac.iloc[-5:].mean()-frac.iloc[-10:-5].mean()) if frac.iloc[-10:].notna().sum()==10 else np.nan
  row[f'flow_{k}_negative5']=float(frac.iloc[-5:].lt(0).sum()) if frac.iloc[-5:].notna().sum()==5 else np.nan
 row['flow_huge_gross_share']=(g.huge_buy_value.iloc[-1]+g.huge_sell_value.iloc[-1])/(2*total.iloc[-1]) if total.iloc[-1]>0 else np.nan
 row['flow_observed_days21']=int(total.notna().sum())
 row['flow_D0_row']=int(pd.notna(total.iloc[-1]))
 at=g.iloc[-1]
 row['flow_create_time']=str(at.get('create_time',''))
 row['flow_update_time']=str(at.get('update_time',''))
 row['flow_D0_timestamp_by_close']=int(row['flow_D0_row'] and row['flow_update_time']<=r['d0_date']+' 15:00:00' and row['flow_create_time']<=r['d0_date']+' 15:00:00')
 row['flow_D0_timestamp_by_buy']=int(row['flow_D0_row'] and row['flow_update_time']<=r['d1_date']+' 09:34:00' and row['flow_create_time']<=r['d1_date']+' 09:34:00')
 for offset in [1,2,3]:
  date=r.get(f'd{offset}_date')
  q=f.loc[date] if date in f.index else None
  row[f'POST_D{offset}_main_net']=float(q.main_buy_value-q.main_sell_value) if q is not None else np.nan
  denom=sum(float(q[f'{k}_buy_value']) for k in ['huge','large','medium','small']) if q is not None else np.nan
  row[f'POST_D{offset}_main_frac']=row[f'POST_D{offset}_main_net']/denom if denom>0 else np.nan
 row['r_repeat']=int(row['risk_days_r5']>=2)
 row['r_attention_burst']=int(row['news_days_r5']>=3 and row['news_day_change']>=2)
 row['r_risk_accel']=int(row['risk_days_r3']>=2 and row['risk_change']>=1)
 row['r_specific_denial']=row['specific_denial']
 row['r_generic_denial']=row['generic_denial']
 row['r_outflow']=int(row['flow_main_frac_D0']<0)
 row['r_flow_deteriorate']=int(row['flow_main_frac_D0']<-.05 and row['flow_main_change']<-.05)
 row['r_news_flow']=int((row['r_repeat'] or row['r_attention_burst'] or row['specific_denial']) and row['r_outflow'])
 rows.append(row)
 if row['flow_D0_row']:
  flowaud.append({'id':r['id'],'all_buy_minus_sell':float(total.iloc[-1]-selltotal.iloc[-1]),'main_minus_huge_large':float((at.main_buy_value-at.main_sell_value)-(at.huge_buy_value-at.huge_sell_value+at.large_buy_value-at.large_sell_value)),'fraction_diff_provider':row['flow_main_frac_D0']-float(at.main_net_buy_value_ratio)/100 if pd.notna(at.main_net_buy_value_ratio) else None})
features=pd.DataFrame(rows);features.to_pickle(R/'features709.pkl');features.to_csv(R/'709候选_消息与资金.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
panel=pd.read_pickle(R/'inputs/panel.pkl');panel['id']=panel.stk_code+'|'+panel.d0_date
panel=panel.merge(features.drop(columns=['v2_allowed']),on='id',validate='one_to_one',suffixes=('','_new'))
panel.to_pickle(R/'panel306.pkl');panel[['id','split',*features.columns.drop(['id','v2_allowed'])]].to_csv(R/'306入选_消息资金时点.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
pd.DataFrame(trace).to_csv(R/'否认热点_逐句复核.csv',index=False,encoding='utf-8-sig');pd.DataFrame(flowaud).to_csv(R/'资金定义核验.csv',index=False)
print(panel.groupby('split')[['flow_D0_row','flow_D0_timestamp_by_close','flow_D0_timestamp_by_buy']].agg(['count','sum']).to_string())
print('flow training nonmissing',panel[panel.split.eq('train')][['flow_main_frac_D0','flow_main_pre5','flow_main_trend5']].notna().mean().to_dict())
print('identity',pd.DataFrame(flowaud)[['all_buy_minus_sell','main_minus_huge_large','fraction_diff_provider']].abs().max().to_dict())
print('specific',panel.loc[panel.specific_denial.eq(1),['stock','D0','split','specific_denial','generic_denial']].to_string(index=False))

