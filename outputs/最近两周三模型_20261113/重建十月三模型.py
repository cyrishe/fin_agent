from pathlib import Path
import json,hashlib,joblib,numpy as np,pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;P.mkdir(exist_ok=True);B=R/'outputs/b2连续两日全量回放_20261113';A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package';F=R/'outputs/D0分时形态研究_20261113';date='2026-10-30';
features=joblib.load(B/'months/2026-10/model.joblib')['features']+json.loads((F/'字段.json').read_text());factor=pd.read_pickle(F/'D0分时因子_含十月.pkl.gz');factor=factor[['qdc_symbol','d0_date','分时有效']+json.loads((F/'字段.json').read_text())];pieces=[]
for month in pd.period_range('2026-04','2026-10',freq='M').astype(str):
 src=B/'近期输入'/month if (B/'近期输入'/month/'test_samples.pkl.gz').exists() else A/'daily'/month;parts=pd.read_pickle(src/'test_samples.pkl.gz');pieces.append(parts)
x=pd.concat(pieces,ignore_index=True);x=x[~x.stk_code.astype(str).str.endswith('.BJ')].copy();assert not x.duplicated(['symbol','d0_date']).any();x['qdc_symbol']=x.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False);x=x.merge(factor,on=['qdc_symbol','d0_date'],how='left',validate='many_to_one');
cal=sorted(set(json.loads((A/'inputs/source_calendars/late.json').read_text()))|set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str)));idx={d:i for i,d in enumerate(cal)};x['实际D2日期']=x.d0_date.map(lambda d:cal[idx[d]+2] if d in idx and idx[d]+2<len(cal) else None)
ok=x.d1_open.gt(0)&x.d1_volume.gt(0)&x.d2_close.gt(0)&x.d1_close.gt(0)&x.d1_preclose.gt(0)&x.d2_preclose.gt(0)&x.close.gt(0)&x.d1_cap.gt(0)&(x.d1_preclose-x.close).abs().le(.011)&(x.d2_preclose-x.d1_close).abs().le(.011)&(x.d1_open-x.d1_cap).le(.011)
x['ret']=(100*(x.d2_close*.999/(x.d1_open*1.001)-1)).where(ok);x['utility']=x.ret.clip(-15,20)-.7*(-x.ret-5).clip(lower=0,upper=15);test=x[x.d0_date.eq(date)].copy();assert len(test)==47
for label,day in [('每日滚动',date),('每月训练','2026-10-01')]:
 start=str((pd.Timestamp(day)-pd.DateOffset(months=6)).date());mature=x.实际D2日期.le(day) if label=='每日滚动' else x.实际D2日期.lt(day);train=x[x.d0_date.ge(start)&x.d0_date.lt(day)&mature&x.utility.notna()].sort_values(['d0_date','symbol']).copy();assert len(train)>500 and train.实际D2日期.max()<=day
 w=1/train.groupby('d0_date').d0_date.transform('size');w=w/w.mean();model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestRegressor(n_estimators=200,max_depth=7,min_samples_leaf=50,max_features=.8,n_jobs=1,random_state=20261013));model.fit(train[features].replace([np.inf,-np.inf],np.nan),train.utility,randomforestregressor__sample_weight=w);test[label]=model.predict(test[features].replace([np.inf,-np.inf],np.nan));joblib.dump({'model':model,'features':features,'训练日':day},P/(label+'.joblib'),compress=3);train.to_pickle(P/(label+'训练.pkl.gz'),compression='gzip');print(label,'训练股次',len(train),'分时有效',int(train.分时有效.eq(True).sum()),'末D0',train.d0_date.max(),'末标签',train.实际D2日期.max(),flush=True)
sel=pd.read_pickle(F/'组合逐月模型/2026-10/预测.pkl.gz');sel=sel[sel.d0_date.eq(date)][['d0_date','symbol','四方案验证选定']].rename(columns={'四方案验证选定':'六训二验'});test=test.merge(sel,on=['d0_date','symbol'],validate='one_to_one');assert len(test)==47
names=['每日滚动','每月训练','六训二验']
for label in names:
 ordered=test.sort_values([label,'symbol'],ascending=[False,True]);test.loc[ordered.index,label+'排名']=np.arange(1,len(test)+1)
test['前二票数']=test[[m+'排名' for m in names]].le(2).sum(axis=1);test['排名均值']=test[[m+'排名' for m in names]].mean(axis=1);selected=test[test.前二票数.gt(0)].sort_values(['前二票数','排名均值','symbol'],ascending=[False,True,True]);assert 2<=len(selected)<=6
fields=['d0_date','symbol','stk_code','close','d1_date','d1_open','d1_close','d1_cap','d1_up','实际D2日期','d2_close','d2_up','d3_close','ret','分时有效','前二票数','每日滚动排名','每月训练排名','六训二验排名'];selected[fields].to_csv(P/'十月三十日名单与价格.csv',index=False);test.to_pickle(P/'十月三十日全候选.pkl.gz',compression='gzip');print('test minute valid',test.分时有效.sum(),'/',len(test),'选择',len(selected));print(selected[fields].to_string(index=False))
