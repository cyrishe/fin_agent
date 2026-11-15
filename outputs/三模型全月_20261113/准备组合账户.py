from pathlib import Path
import sys,json,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;F=R/'outputs/D0分时形态研究_20261113';A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package';old=R/'outputs/多年份训练与组合对照_20261113';prior=R/'outputs/训练频率六个月对照_20261113'; year=sys.argv[1]
x=pd.read_pickle((P/'早期' if year<'2022' else P)/'全部输入.pkl.gz');
if year=='2026' and (P/'近月/全部输入.pkl.gz').exists():
 t=pd.read_pickle(P/'近月/全部输入.pkl.gz');x=pd.concat([x,t[t.d0_date.str.startswith('2026-10')]],ignore_index=True)
cals=A/'inputs/source_calendars';cal=sorted(set().union(*(set(json.loads(f.read_text())) for f in cals.glob('*.json'))));cal=sorted(set(cal)|set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str))|set(pd.read_pickle(R/'outputs/最近两周三模型_20261113/十三日日线新来源.pkl.gz').trade_date.astype(str)));idx={d:i for i,d in enumerate(cal)}
rows=[]; requests=[]; orders=[]; status=[]
evidence=P/'2018十月开盘补证.csv';fixes={}
if evidence.exists():
 for r in pd.read_csv(evidence,dtype={'symbol':str}).to_dict('records'):fixes[(r['d0_date'],r['symbol'])]=r
for period in ([pd.Period(sys.argv[2],freq='M')] if len(sys.argv)>2 else pd.period_range(year+'-01',year+'-12',freq='M')):
 month=str(period); source_x=pd.read_pickle(P/'2022备用来源/全部输入.pkl.gz') if '2022-01'<=month<='2022-04' and (P/'2022备用来源/全部输入.pkl.gz').exists() else x; z=source_x[source_x.d0_date.str.startswith(month)].copy()
 if z.empty:status.append((month,'缺日线候选'));continue
 src=prior if '2024-07'<=month<='2024-12' else old if '2025-07'<=month<='2026-06' else (P/'2022备用来源' if '2022-01'<=month<='2022-04' else P/'近月' if month=='2026-10' else P/'早期' if month<'2022-05' else P)
 comb=(P if month<'2022-05' else F)/'组合逐月模型'/month/'预测.pkl.gz'
 if not comb.exists():status.append((month,'缺择优模型'));continue
 missing=False
 for name,folder in [('每日滚动','每日滚动六个月'),('每月训练','月训六个月')]:
  paths=sorted((src/'模型'/folder).glob(month+'*.pkl.gz'))
  if not paths:missing=True;break
  p=pd.concat([pd.read_pickle(path) for path in paths],ignore_index=True);p=p[['d0_date','symbol','score']].rename(columns={'score':name}); z=z.merge(p,on=['d0_date','symbol'],how='left',validate='one_to_one')
  if z[name].isna().any():missing=True;break
 if missing:status.append((month,'缺滚动或月训预测'));continue
 p=pd.read_pickle(comb)[['d0_date','symbol','四方案验证选定']].rename(columns={'四方案验证选定':'六训二验'});z=z.merge(p,on=['d0_date','symbol'],how='left',validate='one_to_one')
 if z['六训二验'].isna().any():status.append((month,'择优预测不完整'));continue
 for day,g in z.groupby('d0_date',sort=True):
  g=g.copy()
  for method in ['每日滚动','每月训练','六训二验']:
   o=g.sort_values([method,'symbol'],ascending=[False,True]);g.loc[o.index,method+'排名']=np.arange(1,len(g)+1)
  g['排名均值']=g[[m+'排名' for m in ['每日滚动','每月训练','六训二验']]].mean(axis=1);g['选中次数']=g[[m+'排名' for m in ['每日滚动','每月训练','六训二验']]].le(2).sum(axis=1)
  sel=g[g.选中次数.gt(0)].sort_values(['选中次数','排名均值','symbol'],ascending=[False,True,True]);assert 2<=len(sel)<=6
  for rank,r in enumerate(sel.to_dict('records'),1):
   fix=fixes.get((r['d0_date'],r['symbol']))
   if fix:
    for key in ['d1_preclose','d1_open','d1_cap']:r[key]=float(fix[key])
   orders.append({'月份':month,'d0_date':day,'symbol':r['symbol'],'排名':rank,'价格收益%':r['ret'],'日排':r['每日滚动排名'],'月排':r['每月训练排名'],'验排':r['六训二验排名'],'前二票数':r['选中次数']})
   for key in [month+'__各取前二去重']:
    rows.append({**r,'month':key,'new_rank':rank,'dates':cal[idx[day]:idx[day]+31]})
   requests.extend({'symbol':r['qdc_symbol'],'day':d} for d in cal[idx[day]:idx[day]+31])
 status.append((month,'已准备'))
q=P/'账户'/year;q.mkdir(parents=True,exist_ok=True);pd.to_pickle(rows,q/'candidates.pkl');pd.DataFrame(orders).to_csv(q/'每日选股.csv',index=False);pd.DataFrame(requests).drop_duplicates().to_csv(q/'行情请求.csv',index=False);pd.DataFrame(status,columns=['月份','状态']).to_csv(q/'月份状态.csv',index=False);print(year,status,flush=True)
