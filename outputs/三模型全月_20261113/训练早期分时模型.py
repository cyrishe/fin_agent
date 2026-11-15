from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import json,sys,traceback
import numpy as np,pandas as pd,joblib
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor

R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/b2连续两日全量回放_20261113'; OLD=R/'outputs/三模型全月_20261113';A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package/daily'
features=json.loads((P/'字段.json').read_text()); factor=pd.read_pickle(P/'D0分时因子.pkl.gz');factor=factor[['qdc_symbol','d0_date','分时有效']+features]
def attach(d):
 d=d.copy();d['qdc_symbol']=d.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False)
 return d.merge(factor,on=['qdc_symbol','d0_date'],how='left',validate='many_to_one')
def label(f):
 ok=f.d1_open.gt(0)&f.d1_volume.gt(0)&f.d2_close.gt(0)&f.d1_close.gt(0)&f.d1_preclose.gt(0)&f.d2_preclose.gt(0)&f.close.gt(0)&f.d1_cap.gt(0)&(f.d1_preclose-f.close).abs().le(.011)&(f.d2_preclose-f.d1_close).abs().le(.011)&(f.d1_open-f.d1_cap).le(.011)
 return (100*(f.d2_close*.999/(f.d1_open*1.001)-1)).where(ok)
def rank(z,col):
 o=z.sort_values(['d0_date',col,'symbol'],ascending=[True,False,True]);z.loc[o.index,col+'_rank']=o.groupby('d0_date').cumcount().to_numpy()+1
def run(month):
 out=P/'逐月模型'/month;out.mkdir(parents=True,exist_ok=True)
 try:
  base=json.loads((B/'months'/month/'manifest.json').read_text())['features'];train=attach(pd.read_pickle(B/'months'/month/'train.pkl.gz'));train['ret']=label(train);train=train[train.ret.notna()].sort_values(['d0_date','symbol']).copy();train['target']=train.ret.clip(-15,20)-.7*(-train.ret-5).clip(lower=0,upper=15)
  w=1/train.groupby('d0_date').d0_date.transform('size');w=w/w.mean();cols=base+features
  model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestRegressor(n_estimators=200,max_depth=7,min_samples_leaf=50,max_features=.8,n_jobs=1,random_state=20261013));model.fit(train[cols].replace([np.inf,-np.inf],np.nan),train.target,randomforestregressor__sample_weight=w)
  joblib.dump({'model':model,'features':cols,'target':'D1开盘至D2收盘，超过5%亏损额外惩罚，非实际卖法','month':month},out/'分时模型.joblib',compress=3)
  src=(B/'近期输入'/month if (B/'近期输入'/month/'test_samples.pkl.gz').exists() else A/month);old=pd.read_pickle(OLD/'months'/month/'预测.pkl.gz');stages=[]
  first=str(pd.Period(month).start_time.date())
  for stage in ['validation','test']:
   x=attach(pd.read_pickle(src/(stage+'_samples.pkl.gz')));x=x[~x.stk_code.astype(str).str.endswith('.BJ')].copy();x['ret']=label(x)
   if stage=='validation':
    if 'd2_date' in x: mature=x.d2_date.notna()&x.d2_date.lt(first)
    else:
     cal=sorted(set(json.loads((R/'outputs/b2_stage_review_r140_r142_20261026/archive_package/inputs/source_calendars/late.json').read_text()))|set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str)));ix={d:i for i,d in enumerate(cal)};mature=x.d0_date.map(lambda d:d in ix and ix[d]+2<len(cal) and cal[ix[d]+2]<first)
    x.loc[~mature,'ret']=np.nan
   x['utility']=x.ret.clip(-15,20)-.7*(-x.ret-5).clip(lower=0,upper=15);x['分时模型']=model.predict(x[cols].replace([np.inf,-np.inf],np.nan));z=x[['d0_date','symbol','stk_code','ret','utility','分时有效','分时模型']].copy();rank(z,'分时模型');o=old[old.stage.eq(stage)][['d0_date','symbol','原连板模型','收益兼顾下跌']];z=z.merge(o,on=['d0_date','symbol'],validate='one_to_one');rank(z,'原连板模型');rank(z,'收益兼顾下跌');z['stage']=stage;stages.append(z)
  v=stages[0];methods=['原连板模型','收益兼顾下跌','分时模型'];day={}
  for method in methods:
   q=v[v[method+'_rank'].le(5)].groupby('d0_date').agg(n=('ret','size'),known=('ret','count'),u=('utility','mean'));day[method]=q[(q.n.eq(5))&(q.known.eq(5))].u
  common=set.intersection(*(set(x.index) for x in day.values()));scores={m:float(d.loc[sorted(common)].mean()) for m,d in day.items()} if len(common)>=10 else {};choice=max(scores,key=lambda x:(scores[x],x=='原连板模型')) if scores else '原连板模型'
  test=stages[1];test['验证选定']=test[choice];rank(test,'验证选定');test['选定方法']=choice;pd.concat(stages,ignore_index=True).to_pickle(out/'预测.pkl.gz',compression='gzip');meta={'month':month,'训练样本数':len(train),'训练D0分时有效比例':float(train.分时有效.eq(True).mean()),'测试D0分时有效比例':float(test.分时有效.eq(True).mean()),'共同验证天数':len(common),'验证风险收益分':scores,'验证选定':choice,'口径':'六个月拟合，两个月共同日期验证；标签在测试月前成熟；单笔D1开盘至D2收盘价格代理，非实际卖法'};(out/'概要.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print('完成',month,len(train),round(meta['测试D0分时有效比例'],3),choice,flush=True)
 except Exception as e:(out/'失败.txt').write_text(traceback.format_exc());print('失败',month,repr(e),flush=True)
if __name__=='__main__':
 months=sys.argv[1:] or [p.name for p in sorted((B/'months').iterdir()) if (p/'manifest.json').exists() and (OLD/'months'/p.name/'预测.pkl.gz').exists() and '2018-01'<=p.name<='2022-04']
 with ProcessPoolExecutor(max_workers=2) as ex:
  for f in as_completed([ex.submit(run,m) for m in months if not (P/'逐月模型'/m/'概要.json').exists()]):f.result()
