from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import json,joblib,pandas as pd,numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/b2连续两日全量回放_20261113';A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package';F=R/'outputs/D0分时形态研究_20261113';base=joblib.load(B/'months/2026-11/model.joblib')['features'];short=json.loads((F/'字段.json').read_text());long=json.loads((F/'远期字段.json').read_text());f=pd.read_pickle(F/'D0分时因子_含十月.pkl.gz')[['qdc_symbol','d0_date']+short].merge(pd.read_pickle(F/'远期形态_含十月.pkl.gz')[['qdc_symbol','d0_date']+long],on=['qdc_symbol','d0_date'],how='left',validate='one_to_one');
def attach(x):
 x=x.copy();x['qdc_symbol']=x.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False);return x.merge(f,on=['qdc_symbol','d0_date'],how='left',validate='many_to_one')
def label(x):
 ok=x.d1_open.gt(0)&x.d1_volume.gt(0)&x.d2_close.gt(0)&x.d1_close.gt(0)&x.d1_preclose.gt(0)&x.d2_preclose.gt(0)&x.close.gt(0)&x.d1_cap.gt(0)&(x.d1_preclose-x.close).abs().le(.011)&(x.d2_preclose-x.d1_close).abs().le(.011)&(x.d1_open-x.d1_cap).le(.011)
 return (100*(x.d2_close*.999/(x.d1_open*1.001)-1)).where(ok)
tr=attach(pd.read_pickle(B/'months/2026-11/train.pkl.gz'));tr['ret']=label(tr);tr=tr[tr.ret.notna()].sort_values(['d0_date','symbol']).copy();tr['utility']=tr.ret.clip(-15,20)-.7*(-tr.ret-5).clip(lower=0,upper=15);val=attach(pd.read_pickle(B/'近期输入/2026-11/validation_samples.pkl.gz'));val=val[~val.stk_code.str.endswith('.BJ')].copy();val['ret']=label(val)
cal=sorted(set(json.loads((A/'inputs/source_calendars/late.json').read_text()))|set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str)));idx={d:i for i,d in enumerate(cal)};mature=val.d0_date.map(lambda d:d in idx and idx[d]+2<len(cal) and cal[idx[d]+2]<'2026-11-01');val.loc[~mature,'ret']=np.nan;val['utility']=val.ret.clip(-15,20)-.7*(-val.ret-5).clip(lower=0,upper=15)
def fit(item):
 name,columns=item;weights=1/tr.groupby('d0_date').d0_date.transform('size');weights/=weights.mean();model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestRegressor(n_estimators=200,max_depth=7,min_samples_leaf=50,max_features=.8,n_jobs=1,random_state=20261013));model.fit(tr[columns].replace([np.inf,-np.inf],np.nan),tr.utility,randomforestregressor__sample_weight=weights);joblib.dump({'model':model,'features':columns},P/(name+'.joblib'),compress=3);p=val[['d0_date','symbol','ret','utility']].copy();p[name]=model.predict(val[columns].replace([np.inf,-np.inf],np.nan));p.to_pickle(P/(name+'验证.pkl.gz'),compression='gzip');print('训练完成',name,flush=True);return name
if __name__=='__main__':
 jobs=[('收益兼顾下跌',base),('分时模型',base+short),('分时加远期模型',base+short+long)]
 with ProcessPoolExecutor(max_workers=3) as pool:
  for r in as_completed([pool.submit(fit,j) for j in jobs]):r.result()
 orig=pd.read_csv(B/'months/2026-11/predictions.csv.gz',dtype={'symbol':str});orig=orig[orig.stage.eq('validation')][['d0_date','symbol','new_score']].rename(columns={'new_score':'原连板模型'});z=val[['d0_date','symbol','ret','utility']].merge(orig,on=['d0_date','symbol'],validate='one_to_one')
 for name,_ in jobs:
  p=pd.read_pickle(P/(name+'验证.pkl.gz'))[['d0_date','symbol',name]];z=z.merge(p,on=['d0_date','symbol'],validate='one_to_one')
 methods=['原连板模型']+[j[0] for j in jobs];per={}
 for name in methods:
  p=z.sort_values(['d0_date',name,'symbol'],ascending=[True,False,True]);p=p.groupby('d0_date').head(5).groupby('d0_date').agg(n=('ret','size'),known=('ret','count'),u=('utility','mean'));per[name]=p[p.n.eq(5)&p.known.eq(5)].u
 common=set.intersection(*(set(q.index) for q in per.values()));scores={name:float(p.loc[sorted(common)].mean()) for name,p in per.items()} if len(common)>=10 else {};choice=max(scores,key=lambda n:(scores[n],n=='原连板模型')) if scores else '原连板模型';meta={'测试月':'2026-11','共同验证交易日':len(common),'验证各模型分数':scores,'选定':choice,'训练样本':len(tr),'说明':'四选一使用测试月前成熟的D1开盘到D2收盘惩罚后价格分'};(P/'十一月验证择优.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));z.to_pickle(P/'十一月四方案验证.pkl.gz',compression='gzip');print(json.dumps(meta,ensure_ascii=False),flush=True)
