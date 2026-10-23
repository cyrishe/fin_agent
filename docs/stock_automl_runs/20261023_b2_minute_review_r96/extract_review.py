import sys,json,hashlib
from pathlib import Path
import pandas as pd,numpy as np
R=Path('/data/cyris/work/b2_2016_2020_r90_20261023');P=Path('/data/cyris/work/b2_data_repair_r95_20261023');O=Path('/data/cyris/work/b2_minute_review_r96_20261023');O.mkdir(exist_ok=True);sys.path.insert(0,str(P))
from import_tushare_minutes_fixed import convert,CLOCK
cases=[('300313','2019-05-24'),('002457','2020-07-14'),('000677','2019-03-27'),('300543','2016-09-14')];result=[];adj=[]
for sym,day in cases:
 path=P/'fresh_minutes'/f'{day}_{sym}_1.json';j=json.loads(path.read_text());f=pd.DataFrame(j['rows'],columns=j['fields']);f.insert(0,'api_order',np.arange(1,len(f)+1));fields=j['fields']
 for c in ['open','high','low','close','vol','amount']:f[c]=pd.to_numeric(f[c])
 assert f.ts_code.nunique()==1 and f.ts_code.str[:6].eq(sym).all() and f.trade_time.str[:10].eq(day).all() and not f.duplicated(['ts_code','trade_time']).any()
 q=f.sort_values('trade_time').reset_index(drop=True);assert q.trade_time.str[11:16].tolist()==CLOCK
 b=convert(q[fields],day).sort_values('time').reset_index(drop=True);b['label_index']=np.arange(1,len(b)+1)
 q.to_csv(O/f'{sym}_{day}_raw241.csv',index=False,encoding='utf-8-sig');b.to_csv(O/f'{sym}_{day}_standard240.csv',index=False,encoding='utf-8-sig')
 months=[str(x) for x in pd.period_range(pd.Period(day[:7])-1,pd.Period(day[:7])+1,freq='M')];frames=[]
 for m in months:
  p=R/'price_months'/f'price_{m}.csv.gz'
  if p.exists():
   d=pd.read_csv(p,dtype={'stk_code':str,'trade_date':str});frames.append(d[d.stk_code.str[:6].eq(sym)])
 d=pd.concat(frames).sort_values('trade_date');pos=np.flatnonzero(d.trade_date.eq(day))[0];near=d.iloc[max(0,pos-3):pos+4].copy();near['case']=sym+' '+day;near['symbol']=sym;near['day_offset']=np.arange(max(0,pos-3),min(pos+4,len(d)))-pos;adj.extend(near.to_dict('records'));target=d.iloc[pos]
 ag=dict(open=float(q.open.iloc[0]),high=float(q.high.max()),low=float(q.low.min()),close=float(q.close.iloc[-1]),volume=float(q.vol.sum()),amount=float(q.amount.sum()))
 comparisons=[]
 for x in near.itertuples():
  price_errors={k:ag[k]-float(getattr(x,k)) for k in ['open','high','low','close']};comparisons.append(dict(date=x.trade_date,day_offset=x.day_offset,**{k+'_diff':v for k,v in price_errors.items()},volume_diff=ag['volume']-x.volume,price_all_match=all(abs(v)<=.011 for v in price_errors.values())))
 result.append(dict(symbol=sym,date=day,source=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),fields=fields,raw=q.to_dict('records'),standard=b.to_dict('records'),daily=target.to_dict(),raw_summary=ag,nearby_match=comparisons,api_first=f.trade_time.iloc[0],api_last=f.trade_time.iloc[-1],api_sorted_desc=f.trade_time.is_monotonic_decreasing))
pd.DataFrame(adj).to_csv(O/'daily_neighbors.csv',index=False,encoding='utf-8-sig')
# True historical windows and pre-filter candidate evidence for every 2018 model.
f=pd.read_pickle(R/'enriched.pkl');rules=json.loads((R/'rules_frozen.json').read_text());win=[]
for r in rules:
 if not r['month'].startswith('2018'):continue
 meta=json.loads((R/'daily2'/r['month']/'metrics.json').read_text());base=dict(month=r['month'],daily_train='—'.join(meta['window']['train']),daily_valid='—'.join(meta['window']['valid']),shape_train='—'.join(r['window']['train']),shape_valid='—'.join(r['window']['valid']))
 for part in ['train','valid']:
  g=f[f.model_month.eq(r['month'])&f.part.eq(part)];used=g.merge(pd.DataFrame(r['selection_keys'][part]),on=['symbol','d0_date']);base.update({part+'_candidate_rows':len(g),part+'_price_conflicts':int(g.reason.fillna('').str.contains('价格冲突').sum()),part+'_used':len(used),part+'_first_d0':used.d0_date.min(),part+'_last_d0':used.d0_date.max()})
 win.append(base)
pd.DataFrame(win).to_csv(O/'2018_training_windows.csv',index=False,encoding='utf-8-sig')
# Explanatory cross-scenario inventory. No new return claim is made from this count-only join.
frames={};rules_by={};gates={}
for name,path in [('base',R),('strict',R/'rejected_strict_extrema')]:
 s=pd.read_pickle(path/'signals_exit.pkl');s=s[s.d0_date.ge('2021-04-01')&s.d0_date.lt('2022-01-01')].copy();gate=json.loads((path/'overall.json').read_text())['gate'];s['allowed']=s.d0_date.str[:7].map(lambda m:not gate[m]['paused']);frames[name]=s;gates[name]=gate;rules_by[name]={x['month']:x['selected']['id'] for x in json.loads((path/'rules_frozen.json').read_text())}
rr=[]
for m in pd.period_range('2021-04','2021-12',freq='M').astype(str):
 rec=dict(month=m)
 for name,s in frames.items():
  a=s[s.d0_date.str.startswith(m)];rec[name+'_paused']=gates[name][m]['paused'];rec[name+'_rule']=rules_by[name][m];rec[name+'_shadow']=len(a);rec[name+'_trades']=int(a.allowed.sum())
 rr.append(rec)
pd.DataFrame(rr).to_csv(O/'2021_scenario_changes.csv',index=False,encoding='utf-8-sig')
keys={k:set(zip(v.loc[v.allowed,'symbol'],v.loc[v.allowed,'d0_date'])) for k,v in frames.items()};change=dict(base=len(keys['base']),strict=len(keys['strict']),common=len(keys['base']&keys['strict']),only_base=len(keys['base']-keys['strict']),only_strict=len(keys['strict']-keys['base']))
miss=pd.read_csv(P/'archive_missing_codes.csv',dtype=str);miss=miss[miss.date.str.startswith('2016')];counts=miss.groupby('ts_code').size().reset_index(name='missing_stockdays');counts.to_csv(O/'2016_missing_codes.csv',index=False,encoding='utf-8-sig');missing=dict(stockdays=len(miss),unique_codes=miss.ts_code.nunique(),shsz_unique=miss[miss.ts_code.str.endswith(('.SH','.SZ'))].ts_code.nunique())
payload=dict(cases=result,neighbors=adj,windows2018=win,scenario_changes=rr,trade_overlap=change,missing2016=missing)
(O/'review_data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str));print('CASES',[(r['symbol'],r['raw_summary'],r['nearby_match']) for r in result],flush=True);print('WINDOW_JAN',win[0],flush=True);print('OVERLAP',change,flush=True);print('MISSING2016',missing,flush=True);print(pd.DataFrame(rr).to_string(index=False),flush=True)
