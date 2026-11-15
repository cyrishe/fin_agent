from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import sys,json,hashlib,traceback,joblib,pandas as pd,numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/b2连续两日全量回放_20261113';A=R/'outputs/b2_stage_review_r140_r142_20261026/archive_package/daily';C=pd.read_csv(R/'outputs/b2_d0_market_cycles_20261102/market_calendar.csv');C=C[['d0_date','market_limit_count','market_limit_count_mean5','market_limit_count_change3','market_up_fraction_change3','market_up_fraction_change5','market_median_return_change3','market_median_return_change5']];C['市场涨停数量相对五日']=C.market_limit_count/C.market_limit_count_mean5.replace(0,np.nan);C['市场涨停数量三日变化相对五日']=C.market_limit_count_change3/C.market_limit_count_mean5.replace(0,np.nan);C=C.drop(columns=['market_limit_count','market_limit_count_mean5','market_limit_count_change3']);MC=[x for x in C.columns if x!='d0_date']
METHODS=['收益兼顾下跌']
def attach(f):return f.merge(C,on='d0_date',how='left',validate='many_to_one')
def labels(f):
 q=(f.d1_open.gt(0)&f.d1_volume.gt(0)&f.d2_close.gt(0)&f.d1_close.gt(0)&f.d1_preclose.gt(0)&f.d2_preclose.gt(0)&f.close.gt(0)&f.d1_cap.gt(0)&(f.d1_preclose-f.close).abs().le(.011)&(f.d2_preclose-f.d1_close).abs().le(.011)&(f.d1_open-f.d1_cap).le(.011));ret=100*(f.d2_close*.999/(f.d1_open*1.001)-1);return ret.where(q)
def rank(z,col):
 o=z.sort_values(['d0_date',col,'symbol'],ascending=[True,False,True]);z.loc[o.index,col+'_rank']=o.groupby('d0_date').cumcount().to_numpy()+1
def estimate(z,method):
 # validation criterion: require complete 5-stock outcome; per day equal weight
 q=z[z[method+'_rank'].le(5)].groupby('d0_date').agg(n=('proxy','size'),known=('proxy','count'),u=('utility','mean'));q=q[(q.n==5)&(q.known==5)];return (float(q.u.mean()),len(q)) if len(q)>=10 else (None,len(q))
def run(month):
 out=P/'months'/month;out.mkdir(parents=True,exist_ok=True)
 try:
  features=json.loads((B/'months'/month/'manifest.json').read_text())['features'];tr=attach(pd.read_pickle(B/'months'/month/'train.pkl.gz'));tr['proxy']=labels(tr);tr=tr[tr.proxy.notna()].copy();tr['target_return']=tr.proxy.clip(-30,30);tr['target_utility']=tr.proxy.clip(-15,20)-.7*(-tr.proxy-5).clip(lower=0,upper=15);tr=tr.sort_values(['d0_date','symbol']);assert len(tr)>200;tr[['symbol','d0_date','proxy','target_return','target_utility']].to_pickle(out/'训练标签.pkl.gz',compression='gzip');w=1/tr.groupby('d0_date').d0_date.transform('size');w=w/w.mean();models={};pred=[];first_test=str(pd.Period(month).start_time.date());source=(B/'近期输入'/month if (B/'近期输入'/month/'test_samples.pkl.gz').exists() else A/month)
  orig=pd.read_csv(B/'months'/month/'predictions.csv.gz',dtype={'symbol':str})
  testmacro=0
  for stage in ('validation','test'):
   f=attach(pd.read_pickle(source/(stage+'_samples.pkl.gz')));f=f[~f.stk_code.astype(str).str.endswith('.BJ')].copy();z=f[['d0_date','symbol','stk_code','d1_open','d1_close','d1_preclose','d2_close','d2_preclose','d1_volume','d2_up','d1_cap','close','rate']].copy();z['proxy']=labels(f);z['utility']=z.proxy.clip(-15,20)-.7*(-z.proxy-5).clip(lower=0,upper=15);z['stage']=stage;z['month']=month
   if stage=='validation':
    # D2 execution label must be mature by start of test month.
    if 'd2_date' in f.columns: matured=f.d2_date.notna() & f.d2_date.lt(first_test)
    else:
     cal=sorted(set(json.loads((R/'outputs/b2_stage_review_r140_r142_20261026/archive_package/inputs/source_calendars/late.json').read_text()))|set(pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz').trade_date.astype(str)));ix={d:i for i,d in enumerate(cal)};matured=f.d0_date.map(lambda d: d in ix and ix[d]+2<len(cal) and cal[ix[d]+2]<first_test)
    z.loc[~matured,['proxy','utility']]=np.nan
   oldpred=orig[orig.stage.eq(stage)][['symbol','d0_date','new_score']];z=z.merge(oldpred,on=['symbol','d0_date'],validate='one_to_one');z=z.rename(columns={'new_score':'原连板模型'});rank(z,'原连板模型')
   for method in METHODS:
    cols=features+(MC if '市场变化' in method else []);target='target_utility' if '兼顾下跌' in method else 'target_return'
    if method not in models:
     if '市场变化' in method and tr[MC].notna().all(axis=1).mean()<.8:continue
     mod=make_pipeline(SimpleImputer(strategy='median',add_indicator=True),RandomForestRegressor(n_estimators=200,max_depth=7,min_samples_leaf=50,max_features=.8,n_jobs=1,random_state=20261013));mod.fit(tr[cols].replace([np.inf,-np.inf],np.nan),tr[target],randomforestregressor__sample_weight=w);models[method]=(mod,cols);joblib.dump({'model':mod,'features':cols,'target':target,'month':month},out/(method+'.joblib'),compress=3)
    if method in models:
     mod,cols=models[method];z[method]=mod.predict(f[cols].replace([np.inf,-np.inf],np.nan));rank(z,method)
   if stage=='test':testmacro=int(f[MC].notna().all(axis=1).sum())
   pred.append(z)
  va=pred[0];available=['原连板模型']+list(models);perday={}
  for m in available:
   q=va[va[m+'_rank'].le(5)].groupby('d0_date').agg(n=('proxy','size'),known=('proxy','count'),u=('utility','mean'));perday[m]=q[(q.n==5)&(q.known==5)].u
  common=set.intersection(*(set(v.index) for v in perday.values())) if perday else set();vals={m:(float(v.loc[sorted(common)].mean()) if len(common)>=10 else None,len(common)) for m,v in perday.items()};valid={m:score for m,(score,n) in vals.items() if score is not None};chosen=max(valid,key=lambda m:(valid[m],m=='原连板模型')) if valid else '原连板模型'
  test=pred[1];test['验证选定策略']=test[chosen];rank(test,'验证选定策略');test['验证选定名称']=chosen;allp=pd.concat([va,test],ignore_index=True);allp.to_pickle(out/'预测.pkl.gz',compression='gzip');meta={'month':month,'训练股次':len(tr),'训练正收益比例':float(tr.proxy.gt(0).mean()),'验证日均收益兼顾下跌':vals,'验证选定':chosen,'测试市场变化可用股次':testmacro,'测试股次':len(test),'交易收益说明':'D1开盘至D2收盘双边费用0.1%的价格代理；未知成交、跌停和续持，非账户收益','models':list(models)};(out/'概要.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=str));print('完成',month,'train',len(tr),'choice',chosen,'valid',vals,flush=True)
 except Exception as ex:
  (out/'失败.txt').write_text(traceback.format_exc());print('失败',month,repr(ex),flush=True)
if __name__=='__main__':
 months=([] if sys.argv[1:]==['--全量'] else sys.argv[1:]) or [x.name for x in sorted((B/'months').iterdir()) if (x/'manifest.json').exists() and '2018-01'<=x.name<='2022-04'];pilot={'2022-09','2023-09','2024-08','2025-03','2025-09','2026-10'};todo=[m for m in months if not (P/'months'/m/'概要.json').exists() and not (sys.argv[1:]==['--全量'] and m in pilot)]
 with ProcessPoolExecutor(max_workers=2) as pool:
  for f in as_completed([pool.submit(run,m) for m in todo]):f.result()
