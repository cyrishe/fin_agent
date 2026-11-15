from pathlib import Path
import json,sys,pandas as pd,numpy as np,joblib,requests
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;sys.path.insert(0,str(R));from scripts.build_automl_limit_rebound_features import prepare_daily
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel
from scripts.experiment_limit_rebound_short_mixed_r12 import load_sources,add_features,enrich_outcomes,OUT,SOURCE,EARLY,END
from scripts.experiment_limit_rebound_segments_r18 import segment_one
for period in pd.period_range('2023-12',END[:7],freq='M'):
 month=str(period);p=(EARLY if '2024-04'<=month<='2024-09' else SOURCE)/f'price_{month}.csv.gz';p=p if p.exists() else OUT/'price_months'/f'price_{month}.csv.gz';assert p.exists(),f'不调用数据库，缺少历史文件 {p}'
q=pd.read_pickle(P/'十三日全市场双源.pkl.gz');assert len(q)>=5200 and q['trade差'].fillna(0).le(.011).sum()>=len(q)-10;today=pd.DataFrame({'trade_date':'2026-11-13','stk_code':q.stk_code,'preclose':q.settlement,'open':q.open,'high':q.high,'low':q.low,'close':q.trade,'volume':q.volume,'amount':q.amount,'turn_ratio':q.turnoverratio,'is_limit_price':q.封板标记});
# 689009 is absent from the public full-market listing; existing quote confirms it traded and was not limit-up.
u='http://jzyzwup.upoem1.com/json/hq_basichq/stockHq';r=requests.post(u,json={'stReq':{'vStock':[{'shtSetcode':1,'sCode':'689009'}],'eHqData':3}},timeout=15);r.raise_for_status();item=r.json()['stRsp']['vStockHq'][0];a=item['stSimHq'];today=pd.concat([today,pd.DataFrame([{'trade_date':'2026-11-13','stk_code':'689009.SH','preclose':a['fClose'],'open':a['fOpen'],'high':a['fHigh'],'low':a['fLow'],'close':a['fNowPrice'],'volume':a['lVolume']*100,'amount':a['fAmount'],'turn_ratio':item['stExHq']['fTurnoverRate'],'is_limit_price':a['ztData']['bZT']}])],ignore_index=True);assert not today.stk_code.duplicated().any();today.to_pickle(P/'十三日日线新来源.pkl.gz',compression='gzip')
status=pd.read_csv(R/'outputs/b2连续两日全量回放_20261113/最新历史ST.csv.gz',dtype=str).fillna({'end_date':'2999-12-31'});status['symbol']=status.stk_code.str[:6];entries=[];missing=[]
for code,g in status.groupby('symbol'):
 for day in ['2026-11-12','2026-11-13']:
  m=g[g.begin_date.le(day)&g.end_date.gt(day)]
  if len(m)==1:entries.append({'symbol':code,'asof_date':day,'st_type':m.st_type.iloc[0],'is_st':int(m.st_type.iloc[0] in ['S','Y'])})
  else:missing.append((code,day))
work=P/'十三日特征';work.mkdir(exist_ok=True);pd.DataFrame(entries).to_csv(work/'historical_st.csv.gz',index=False,compression='gzip');old,_=load_sources();old=old[old.trade_date.lt('2026-10-01')];live=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');raw=pd.concat([live,today],ignore_index=True);daily=pd.concat([old,prepare_daily(raw)],ignore_index=True).sort_values(['symbol','trade_date']).reset_index(drop=True);calendar=sorted(daily.trade_date.unique());daily['date_i']=daily.trade_date.map({d:i for i,d in enumerate(calendar)}).astype(int);panel,quality=build_panel(daily,work,score_all_d0=True,d0_start='2026-11-12');panel=add_features(panel,daily);panel=enrich_outcomes(panel,daily);targets={s:f for s,f in panel.groupby('symbol',sort=False)};rows=[]
for sym,g in daily.groupby('symbol',sort=False):
 if sym not in targets:continue
 pos={d:i for i,d in enumerate(g.trade_date)}
 for r in targets[sym].itertuples():
  zz,_,_=segment_one(g,pos[r.first_limit_date],pos[r.d0_date]);rows.append({'symbol':sym,'d0_date':r.d0_date,**zz})
panel=panel.merge(pd.DataFrame(rows),on=['symbol','d0_date'],validate='one_to_one');ref=pd.read_pickle(R/'outputs/b2连续两日全量回放_20261113/近期输入/2026-11/test_samples.pkl.gz');ref=ref[ref.d0_date.eq('2026-11-12')];prev=panel[panel.d0_date.eq('2026-11-12')];joined=prev.merge(ref,on=['symbol','d0_date'],how='outer',suffixes=('_新','_旧'),indicator=True);features=joblib.load(R/'outputs/b2连续两日全量回放_20261113/months/2026-11/model.joblib')['features'];both=joined[joined._merge.eq('both')];diff={c:int((~np.isclose(both[c+'_新'],both[c+'_旧'],atol=1e-10,rtol=0,equal_nan=True)).sum()) for c in features};diff={c:n for c,n in diff.items() if n};print('原12日重合',joined._merge.value_counts().to_dict(),'特征差',diff,flush=True)
out=panel[panel.d0_date.eq('2026-11-13')].copy();out.to_pickle(P/'十三日候选日线.pkl.gz',compression='gzip');meta={'十三日全市场沪深':len(today),'十三日候选':len(out),'十二日复现':joined._merge.value_counts().to_dict(),'十二日特征差':diff,'今日行情源':'新浪全市场列表和研究行情服务；候选涨停标记由后者提供','最新历史状态未知股数':len(missing),'原始行情未知项':'两只前日股票不在公开全市场列表，其中689009已补、不涨停；002731当前无报价','quality':quality};(P/'十三日候选来源审计.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=str));print(json.dumps(meta,ensure_ascii=False),flush=True)
