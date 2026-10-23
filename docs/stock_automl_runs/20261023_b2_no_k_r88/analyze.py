from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_HALF_UP
import sys,json,hashlib
import pandas as pd
O=Path('/data/cyris/work/b2_no_k_r88_20261023')
S=Path('/data/cyris/fin_agent_deploy/b2-v2-20261021')
A=S/'assets';sys.path.insert(0,str(S))
from scripts.automl_b2 import evaluate_morning,valid_bars,first_features
G=Path('/data/cyris/work/b2_idle_cross_r87_20261023/monthly_gate.csv')
R=Path('/data/cyris/work/b2_floor_countermeasure_r86_20261022/306笔_综合因子与基线.csv')
C=Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json')
allowed=set(pd.read_csv(G).query('pause == False').model_month)
full=pd.read_pickle(A/'reference/four_all_top3.pkl')
full=full[full.part.eq('test')&full.d0_date.le('2026-09-30')&full.d0_date.str[:7].isin(allowed)]
ref=pd.read_csv(R,encoding='utf-8-sig',dtype={'symbol':str}).set_index('d0_date')
cal=json.loads(C.read_text());rules={};out=[];missing=[];stock_by_day={}
def pick_off(picks,bars,day,keep_body):
 ready={};body={}
 for p in picks:
  if not p['ordinary']:continue
  code=p['symbol6'];b=valid_bars(bars[code],day,datetime.fromisoformat(day+' 09:40:00'),p['cap'])
  if keep_body:
   first=b[b.time.dt.strftime('%H:%M').le('09:34')]
   if len(first)!=4 or first.time.dt.strftime('%H:%M').tolist()!=['09:31','09:32','09:33','09:34']:return None
   body[code]=first_features(first,p['preclose'],p['cap'],4)['body5']
  ready[code]=b.set_index(b.time.dt.strftime('%H:%M'))
 contenders=sorted((p for p in picks if p['ordinary']),key=lambda p:p['selection_rank'])
 winner=None;activated=0
 for n in range(35,41):
  label=f'09:{n:02}';stamp=datetime.fromisoformat(day+' '+label+':00')
  if winner is None:
   if any(label not in ready[p['symbol6']].index for p in contenders):break
   for p in contenders:
    row=ready[p['symbol6']].loc[label]
    if row.volume>0 and row.open<p['cap']-.005:
     winner=p;activated=n
     if keep_body and body[p['symbol6']]<=-.08+1e-12:return None
     break
  elif n>activated:
   code=winner['symbol6']
   if label not in ready[code].index:break
   row=ready[code].loc[label]
   if row.volume>0 and row.open<winner['cap']-.005:
    return dict(symbol6=code,selection_rank=winner['selection_rank'],entry_price=float(row.open),entry_at=(stamp-timedelta(minutes=1)).isoformat())
 return None
def trade(day,pick,origin):
 if not pick:return None
 q=stock_by_day[day][pick['symbol6']];d1=str(q.d1_date);d2=cal[cal.index(d1)+1]
 return dict(id=pick['symbol6']+'|'+day,d0=day,d1=d1,d2=d2,code=pick['symbol6'],rank=int(pick['selection_rank']),entry=float(pick['entry_price']),d1close=float(q.d1_close),exit=float(q.d2_close),d2preclose=float(q.d2_preclose),rate=float(q.rate),d3close=float(q.d3_close) if pd.notna(q.d3_close) else None,d3preclose=float(q.d3_preclose) if pd.notna(q.d3_preclose) else None,time=(str(pick['entry_at']) if len(str(pick['entry_at']))==5 else str(pick['entry_at'])[11:16]),origin=origin)
for d0,g in full.groupby('d0_date'):
 g=g.sort_values('d0_rank');assert len(g)==3 and g.d0_rank.tolist()==[1,2,3],d0
 d1=str(g.d1_date.iloc[0]);month=d0[:7]
 if month not in rules:rules[month]=json.loads((A/'months'/month/'rule.json').read_text())['selected']
 picks=[];bars={};stock={}
 for q in g.itertuples(index=False):
  f=A/'minute_history'/f'{d1}_{q.symbol}.pkl'
  if not f.exists():missing.append((d0,q.symbol));continue
  bars[q.symbol]=pd.read_pickle(f);stock[q.symbol]=q
  picks.append(dict(symbol6=q.symbol,selection_rank=int(q.d0_rank),preclose=float(q.d1_preclose),cap=float(q.d1_cap),ordinary=bool(q.ordinary and q.audit_ok==True)))
 if len(picks)!=3:continue
 stock_by_day[d0]=stock
 val=evaluate_morning(picks,bars,d1,datetime.fromisoformat(d1+' 09:40:00'),rules[month])
 hits=[x for x in val if x['actionable']];assert len(hits)<=1
 base=hits[0] if hits else None
 old=ref.loc[d0] if d0 in ref.index else None
 assert (base is None)==(old is None),(d0,'buy mismatch')
 if base:assert base['symbol6']==str(old.symbol) and abs(base['entry_price']-float(old.entry))<.011,(d0,'entry mismatch')
 options=dict(原策略=base,去形态保留实体=pick_off(picks,bars,d1,True),去四K筛选=pick_off(picks,bars,d1,False))
 z=dict(D0=d0,D1=d1,year=int(d0[:4]))
 for name,p in options.items():
  t=trade(d0,p,name)
  for key in ['code','rank','time','entry','exit']:z[name+'_'+key]=t[key] if t else None
  z[name+'_净涨幅']=t['exit']/t['entry']*.999/1.001-1 if t else None
 out.append(z)
panel=pd.DataFrame(out);panel.to_csv(O/'逐日三方案.csv',index=False,encoding='utf-8-sig')
miss_days=sorted({d for d,_ in missing});assert len(panel)==195 and len(miss_days)==6
def oldtrade(r):
 return dict(id=r.id,d0=r.d0_date,d1=r.d1_date,d2=r.d2_date,code=r.symbol,rank=None,entry=float(r.entry),d1close=float(r.d1_close),exit=float(r.d2_close),d2preclose=None,rate=None,d3close=None,d3preclose=None,time=r.buy_clock,origin='原档保留')
orig=[oldtrade(r) for r in ref.reset_index().itertuples(index=False) if '2025-09-01'<=r.d0_date<='2026-09-30']
assert len(orig)==133
byday={t['d0']:t for t in orig};trades={n:[] for n in ['原策略','去形态保留实体','去四K筛选']}
for day in miss_days:
 if day in byday:
  for name in trades:trades[name].append(byday[day].copy())
for r in panel.itertuples(index=False):
 for name in trades:
  code=getattr(r,name+'_code')
  if not isinstance(code,str):continue
  p=dict(symbol6=code,selection_rank=getattr(r,name+'_rank'),entry_price=getattr(r,name+'_entry'),entry_at=getattr(r,name+'_time'))
  trades[name].append(trade(r.D0,p,name))
assert sorted((t['d0'],t['code'],round(t['entry'],2)) for t in trades['原策略'])==sorted((t['d0'],t['code'],round(t['entry'],2)) for t in orig)
def replay(rows,start,end):
 cash=1e6;hold={};peak=cash;dd=0;events={}
 sub=[t for t in rows if start<=t['d0']<=end]
 for t in sub:
  events.setdefault(t['d1'],[]).append((t['time'],0,t))
  events.setdefault(t['d2'],[]).append(('15:00',1,t))
 horizon=max((t['d2'] for t in sub),default=end)
 for day in [d for d in cal if start<=d<=horizon]:
  for clock,kind,t in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
   if kind==0:
    invest=cash*(.5 if not hold else 1.)
    shares=invest/(t['entry']*1.001);cash-=invest;hold[t['id']]=(shares,t['d1close'])
   else:
    shares,_=hold.pop(t['id']);cash+=shares*t['exit']*.999
  nav=cash+sum(sh*mark for sh,mark in hold.values())
  peak=max(peak,nav);dd=max(dd,1-nav/peak)
 assert not hold
 return cash/1e6-1,dd
def floor(p,r):
 return float((Decimal(str(p))*(Decimal('1')-Decimal(str(r)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def risk(t):
 if t['d2preclose'] is None:return None,None,None
 a=t['exit']<=floor(t['d2preclose'],t['rate'])+.005
 b=t['d3close'] is not None and t['d3preclose'] is not None and t['d3close']<=floor(t['d3preclose'],t['rate'])+.005
 return a,b,a and b
summ=[];trows=[]
for name,rows in trades.items():
 for t in rows:
  a,b,c=risk(t);trows.append(dict(方案=name,**t,净涨幅=t['exit']/t['entry']*.999/1.001-1,D2收盘跌停=a,D3收盘跌停=b,D2D3双跌停=c))
 for period,start,end in [('全段','2025-09-01','2026-09-30'),('2025','2025-09-01','2025-12-31'),('2026','2026-01-01','2026-09-30')]:
  sub=[t for t in rows if start<=t['d0']<=end];ret,dd=replay(rows,start,end)
  known=[risk(t) for t in sub if t['origin']!='原档保留']
  summ.append(dict(期间=period,方案=name,D0日数=sum(start<=d<=end for d in list(panel.D0)+miss_days),买入笔数=len(sub),账户收益率=ret,最大回撤=dd,每笔等额净收益和=sum(t['exit']/t['entry']*.999/1.001-1 for t in sub),D2收盘跌停_可核验=sum(x[0] for x in known),D2D3双收盘跌停_可核验=sum(x[2] for x in known),风险可核验笔数=len(known)))
pd.DataFrame(summ).to_csv(O/'同口径账户与跌停风险.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(trows).to_csv(O/'逐笔三方案.csv',index=False,encoding='utf-8-sig')
print('MISSING',missing)
print(pd.DataFrame(summ).to_string(index=False))
for alt in ['去形态保留实体','去四K筛选']:
 a=panel['原策略_code'].fillna('无买');b=panel[alt+'_code'].fillna('无买')
 print(alt,'same',sum(a==b),'added',sum((a=='无买')&(b!='无买')),'removed',sum((a!='无买')&(b=='无买')),'replaced',sum((a!='无买')&(b!='无买')&(a!=b)))
manifest={'inputs':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [G,R,C,A/'reference/four_all_top3.pkl']},'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(O/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
