from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import json,hashlib,joblib,pandas as pd,numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/b2连续两日全量回放_20261113';A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package';F=R/'outputs/D0分时形态研究_20261113';short=json.loads((F/'字段.json').read_text());base=joblib.load(B/'months/2026-11/model.joblib')['features'];features=base+short
factor=pd.read_pickle(F/'D0分时因子_含十月.pkl.gz');factor=factor[factor.d0_date.lt('2026-11-01')][['qdc_symbol','d0_date','分时有效']+short];nov=pd.read_pickle(P/'研究行情分时因子.pkl.gz');nov=nov[nov.d0_date.ge('2026-11-01')][['qdc_symbol','d0_date','分时有效']+short];factor=pd.concat([factor,nov],ignore_index=True);assert not factor.duplicated(['qdc_symbol','d0_date']).any()
parts=[]
for month in pd.period_range('2026-05','2026-11',freq='M').astype(str):
 src=B/'近期输入'/month if (B/'近期输入'/month/'test_samples.pkl.gz').exists() else A/'daily'/month;parts.append(pd.read_pickle(src/'test_samples.pkl.gz'))
x=pd.concat(parts,ignore_index=True);x=x[~x.stk_code.astype(str).str.endswith('.BJ')].copy();assert not x.duplicated(['symbol','d0_date']).any();x['qdc_symbol']=x.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False);x=x.merge(factor,on=['qdc_symbol','d0_date'],how='left',validate='many_to_one')
cal=sorted(set(json.loads((A/'inputs/source_calendars/late.json').read_text()))|set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str))|{'2026-11-13'});idx={d:i for i,d in enumerate(cal)};x['实际D2日期']=x.d0_date.map(lambda d:cal[idx[d]+2] if d in idx and idx[d]+2<len(cal) else None)
ok=x.d1_open.gt(0)&x.d1_volume.gt(0)&x.d2_close.gt(0)&x.d1_close.gt(0)&x.d1_preclose.gt(0)&x.d2_preclose.gt(0)&x.close.gt(0)&x.d1_cap.gt(0)&(x.d1_preclose-x.close).abs().le(.011)&(x.d2_preclose-x.d1_close).abs().le(.011)&(x.d1_open-x.d1_cap).le(.011);x['ret']=(100*(x.d2_close*.999/(x.d1_open*1.001)-1)).where(ok);x['utility']=x.ret.clip(-15,20)-.7*(-x.ret-5).clip(lower=0,upper=15)
def fit(job):
 name,day=job;start=str((pd.Timestamp(day)-pd.DateOffset(months=6)).date());end=x.实际D2日期.le(day) if name=='每日滚动' else x.实际D2日期.lt(day);train=x[x.d0_date.ge(start)&x.d0_date.lt(day)&end&x.utility.notna()].sort_values(['d0_date','symbol']).copy();assert len(train)>500
 w=1/train.groupby('d0_date').d0_date.transform('size');w=w/w.mean();model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestRegressor(n_estimators=200,max_depth=7,min_samples_leaf=50,max_features=.8,n_jobs=1,random_state=20261013));model.fit(train[features].replace([np.inf,-np.inf],np.nan),train.utility,randomforestregressor__sample_weight=w)
 test=x[x.d0_date.eq(day) if name=='每日滚动' else x.d0_date.str.startswith('2026-11')].copy();test[name]=model.predict(test[features].replace([np.inf,-np.inf],np.nan));out=P/'十一月模型'/name;out.mkdir(parents=True,exist_ok=True);modelpath=out/(day+'.joblib');joblib.dump({'model':model,'features':features},modelpath,compress=3);test[['d0_date','symbol',name]].to_pickle(out/(day+'.pkl.gz'),compression='gzip');meta={'方式':name,'训练窗口开始':start,'决策':day,'训练股次':len(train),'训练分时有效':int(train.分时有效.eq(True).sum()),'训练末D0':train.d0_date.max(),'最晚标签日':train.实际D2日期.max(),'测试股次':len(test),'测试分时有效':int(test.分时有效.eq(True).sum()),'模型SHA256':hashlib.sha256(modelpath.read_bytes()).hexdigest()};(out/(day+'.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(name,day,'训练',len(train),'分时',meta['训练分时有效'],'测试',len(test),'测试分时',meta['测试分时有效'],flush=True)
if __name__=='__main__':
 days=sorted(x.loc[x.d0_date.str.startswith('2026-11'),'d0_date'].unique());jobs=[('每月训练','2026-11-01')]+[('每日滚动',d) for d in days];jobs=[j for j in jobs if not (P/'十一月模型'/j[0]/(j[1]+'.json')).exists()]
 with ProcessPoolExecutor(max_workers=4) as ex:
  for f in as_completed([ex.submit(fit,j) for j in jobs]):f.result()
 test=x[x.d0_date.str.startswith('2026-11')].copy()
 for name in ['每日滚动','每月训练']:
  p=pd.concat([pd.read_pickle(q) for q in sorted((P/'十一月模型'/name).glob('*.pkl.gz'))],ignore_index=True);test=test.merge(p,on=['d0_date','symbol'],validate='one_to_one')
 orig=pd.read_pickle(P/'十一月原环境评分.pkl.gz');test=test.merge(orig,on=['d0_date','symbol'],validate='one_to_one');assert len(test)==len(orig)
 for name in ['每日滚动','每月训练','六训二验']:
  o=test.sort_values(['d0_date',name,'symbol'],ascending=[True,False,True]);test.loc[o.index,name+'排名']=o.groupby('d0_date').cumcount().to_numpy()+1
 test['票数']=test[[n+'排名' for n in ['每日滚动','每月训练','六训二验']]].le(2).sum(axis=1);test['排名均值']=test[[n+'排名' for n in ['每日滚动','每月训练','六训二验']]].mean(axis=1);test.to_pickle(P/'十一月全候选与排名.pkl.gz',compression='gzip');chosen=test[test.票数.gt(0)].sort_values(['d0_date','票数','排名均值','symbol'],ascending=[True,False,True,True]);chosen.to_pickle(P/'十一月每日名单.pkl.gz',compression='gzip');print('选股完成',chosen.d0_date.nunique(),'天',len(chosen),'股次',flush=True)
