from pathlib import Path
from datetime import datetime
import sys,json
import pandas as pd
O=Path('/data/cyris/work/b2_idle_cross_r87_20261023')
S=Path('/data/cyris/fin_agent_deploy/b2-v2-20261021')
A=S/'assets'
sys.path.insert(0,str(S))
from scripts.automl_b2 import evaluate_morning
gate=pd.read_csv(O/'monthly_gate.csv')
allowed=set(gate.loc[~gate.pause.astype(bool),'model_month'])
full=pd.read_pickle(A/'reference/four_all_top3.pkl')
full=full[full.part.eq('test')&full.d0_date.le('2026-09-30')&full.d0_date.str[:7].isin(allowed)]
ref=pd.read_csv('/data/cyris/work/b2_floor_countermeasure_r86_20261022/306笔_综合因子与基线.csv',encoding='utf-8-sig',dtype={'symbol':str})
ref=ref.set_index('d0_date')
rules={};out=[];missing=[]
for d0,g in full.groupby('d0_date'):
 g=g.sort_values('d0_rank')
 assert len(g)==3 and g.d0_rank.tolist()==[1,2,3],d0
 month=d0[:7];d1=g.d1_date.iloc[0]
 if month not in rules:rules[month]=json.loads((A/'months'/month/'rule.json').read_text())['selected']
 rule=rules[month];picks=[];bars={};stock={}
 for v in g.itertuples(index=False):
  f=A/'minute_history'/f'{d1}_{v.symbol}.pkl'
  if not f.exists():missing.append([d0,v.symbol]);continue
  bars[v.symbol]=pd.read_pickle(f);stock[v.symbol]=v
  picks.append(dict(symbol6=v.symbol,selection_rank=int(v.d0_rank),preclose=float(v.d1_preclose),cap=float(v.d1_cap),ordinary=bool(v.ordinary and v.audit_ok==True)))
 if len(picks)!=3:continue
 def replay(pp):
  if not pp:return [],None
  val=evaluate_morning(pp,bars,d1,datetime.fromisoformat(d1+' 09:40:00'),rule)
  hit=[z for z in val if z['actionable']]
  assert len(hit)<=1
  return val,hit[0] if hit else None
 base,buy=replay(picks)
 original=ref.loc[d0] if d0 in ref.index else None
 assert (original is None)==(buy is None),(d0,'buy mismatch')
 if buy:
  assert buy['symbol6']==str(original.symbol) and abs(buy['entry_price']-float(original.entry))<.011,(d0,'entry mismatch',buy['symbol6'],buy['entry_price'],str(original.symbol),float(original.entry))
 flagged=bool(buy and int(original.D1_Q75)==1)
 no_body=[p for p in picks if (next(z for z in base if z['symbol6']==p['symbol6']).get('shape_features') or {}).get('body5',0)>-.08+1e-12]
 _,body=replay(no_body)
 _,alternate=replay([p for p in picks if p['symbol6']!=buy['symbol6']]) if flagged else ([],None)
 chosen=next((z for z in base if z['chosen']),None)
 if buy:reason='原策略已买入'
 elif chosen is None:reason='无合格激活'
 elif chosen.get('reason','').startswith('四分实体'):reason='锁定后实体门槛失败'
 else:reason='锁定后无确认买点'
 def econ(x):
  if not x:return (None,None,None,None,None)
  v=stock[x['symbol6']]
  ret=float(v.d2_close)/x['entry_price']*.999/1.001-1 if pd.notna(v.d2_close) else None
  return (x['symbol6'],int(x['selection_rank']),x['entry_price'],ret,x.get('entry_at'))
 bc,br,be,bnet,bt=econ(buy);cc,cr,ce,cnet,ct=econ(body);ac,ar,ae,anet,at=econ(alternate)
 out.append(dict(D0=d0,D1=d1,year=d0[:4],base_buy=int(buy is not None),base_reason=reason,base_code=bc,base_rank=br,base_entry=be,base_net=bnet,base_time=bt,
                 Q75_flag=int(flagged),body_prefilter_code=cc,body_prefilter_rank=cr,body_prefilter_entry=ce,body_prefilter_net=cnet,body_prefilter_time=ct,
                 Q75_unchecked_alt_code=ac,Q75_unchecked_alt_rank=ar,Q75_unchecked_alt_entry=ae,Q75_unchecked_alt_net=anet,Q75_unchecked_alt_time=at,
                 base_chosen_code=chosen['symbol6'] if chosen else None,base_chosen_reason=chosen.get('reason') if chosen else None))
print('MISSING',missing)
z=pd.DataFrame(out)
z.to_csv(O/'逐日原Top3轮空与未评分替补.csv',index=False,encoding='utf-8-sig')
summary=dict(all_days=len(full.d0_date.unique()),missing_days=len(set(d for d,_ in missing)),days=len(z),base_buys=int(z.base_buy.sum()),idle_days=int(z.base_buy.eq(0).sum()),reasons=z[z.base_buy.eq(0)].base_reason.value_counts().to_dict(),body_extra=int((z.base_buy.eq(0)&z.body_prefilter_code.notna()).sum()),Q75_flagged=int(z.Q75_flag.sum()),Q75_unchecked_alt=int(z.Q75_unchecked_alt_code.notna().sum()))
(O/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(summary)
for yr,h in z.groupby('year'):
 b=h[h.base_buy.eq(0)&h.body_prefilter_code.notna()]
 q=h[h.Q75_flag.eq(1)&h.Q75_unchecked_alt_code.notna()]
 print('YEAR',yr,'days',len(h),'buys',int(h.base_buy.sum()),'body_extra',len(b),'body_extra_1w',float(b.body_prefilter_net.sum()*1e4),'Q75',int(h.Q75_flag.sum()),'alternatives',len(q),'alt_1w',float(q.Q75_unchecked_alt_net.sum()*1e4),'alt_winners',int(q.Q75_unchecked_alt_net.gt(0).sum()))
print('BODY EXTRAS')
print(z[z.base_buy.eq(0)&z.body_prefilter_code.notna()][['D0','base_reason','base_chosen_code','body_prefilter_code','body_prefilter_entry','body_prefilter_net']].to_string(index=False))
print('Q75 ALTS')
print(z[z.Q75_flag.eq(1)&z.Q75_unchecked_alt_code.notna()][['D0','base_code','Q75_unchecked_alt_code','Q75_unchecked_alt_entry','Q75_unchecked_alt_net']].to_string(index=False))
