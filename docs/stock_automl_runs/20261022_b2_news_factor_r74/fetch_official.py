from pathlib import Path
import requests,json,time,re,hashlib,threading,traceback
from concurrent.futures import ThreadPoolExecutor,as_completed
import pandas as pd
R=Path('/data/cyris/work/b2_news_factor_r74_20261022')
(R/'cninfo_windows').mkdir(exist_ok=True)
s=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/all_signals.pkl')
p=pd.read_csv('/data/cyris/work/b2_first_board_flow_r72_20261022/统一三分类平衡逐笔预测.csv')
s=s.merge(p[['股票','D0选中日','D2开收跌停','D2D3连续收盘跌停','真实类别']],left_on=['stk_code','d0_date'],right_on=['股票','D0选中日'],validate='one_to_one')
names=pd.read_csv(R/'stock_names.csv')
s=s.merge(names,on='stk_code',how='left',validate='many_to_one')
cal=json.loads(Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json').read_text());idx={d:i for i,d in enumerate(cal)}
s['start10']=[cal[idx[d]-10] for d in s.d0_date];s['start5']=[cal[idx[d]-5] for d in s.d0_date];s['id']=s.stk_code+'|'+s.d0_date
s.to_pickle(R/'cohort709.pkl')
mapping={x['code']:x for x in json.loads((R/'cninfo_stockmap.json').read_text())['stockList']}
spec={'scope':'709 final B2 candidates, 306 V2 allowed; consecutive=20 trades/19 companies (D2+D3 close floor)','windows':'D0 and preceding 5/10 exchange trading days, including intervening calendar dates','cutoffs':['D0 15:00','D1 09:30'],'date_only_rule':'date-only disclosure conservatively available from next calendar day 00:00; D0 same-day announcements excluded from D0 cut, included at D1; D1 date-only excluded','features':['announcement_count','abnormal_count','severe_abnormal_count','explicit_risk_warning_count','reduction_plan_count','enforcement_count','earnings_negative_count','deal_change_count','restructuring_count','pledge_freeze_count','news_count','news_negative_count'],'evaluation':'title categories are observable mentions, not proof of misconduct or sentiment; do not equate routine abnormal announcements with negative news; no model fit this round; report coverage and equal-ticket PnL before account claims'}
(R/'factor_spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n')
local=threading.local()
def session():
 if not hasattr(local,'s'):
  local.s=requests.Session();local.s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/new/disclosure','Accept':'application/json, text/javascript, */*; q=0.01'})
 return local.s
def fetch(row):
 key=row['id'];fn=R/'cninfo_windows'/(key.replace('|','_')+'.json')
 if fn.exists():
  old=json.loads(fn.read_text())
  if old.get('ok'):return old
 code=row['symbol'];m=mapping.get(code);out={'id':key,'stock':row['stk_code'],'start':row['start10'],'end':row['d1_date'],'ok':False,'items':[],'pages':[]}
 if not m:
  out['error']='stock not found in official current mapping';fn.write_text(json.dumps(out,ensure_ascii=False));return out
 param={'pageNum':1,'pageSize':30,'column':'szse','tabName':'fulltext','stock':code+','+m['orgId'],'seDate':row['start10']+'~'+row['d1_date'],'isHLtitle':'true','sortName':'time','sortType':'desc'}
 try:
  for page in range(1,101):
   param['pageNum']=page
   for attempt in range(3):
    try:
     res=session().post('https://www.cninfo.com.cn/new/hisAnnouncement/query',data=param,timeout=20);res.raise_for_status();data=res.json()
     if not isinstance(data,dict) or 'totalAnnouncement' not in data:raise ValueError('invalid response')
     break
    except Exception:
     if attempt==2:raise
     time.sleep(1+attempt*2)
   out['pages'].append(data);out['items'].extend(data.get('announcements') or [])
   if page*30>=int(data.get('totalAnnouncement') or 0):break
   time.sleep(.15)
  else:raise RuntimeError('pagination exceeded 100')
  if any(x.get('secCode')!=code for x in out['items']):raise ValueError('stock association mismatch')
  out['ok']=True;out['total']=len(out['items'])
 except Exception as e:out['error']=type(e).__name__+': '+str(e)[:160]
 out['retrieved_at']=pd.Timestamp.now(tz='Asia/Shanghai').isoformat()
 fn.write_text(json.dumps(out,ensure_ascii=False))
 time.sleep(.2)
 return out
records=s.to_dict('records');records.sort(key=lambda r:(not (r['v2_allowed'] and r['D2D3连续收盘跌停']),r['d0_date']))
results=[]
with ThreadPoolExecutor(max_workers=3) as pool:
 futures=[pool.submit(fetch,r) for r in records]
 for f in as_completed(futures):
  x=f.result();results.append({k:v for k,v in x.items() if k not in ['pages','items']})
  if len(results)%25==0:print('official',len(results),'/',len(records),'ok',sum(x['ok'] for x in results),flush=True)
pd.DataFrame(results).to_csv(R/'official_fetch_coverage.csv',index=False,encoding='utf-8-sig')
print('DONE',len(results),'ok',sum(x['ok'] for x in results),flush=True)

