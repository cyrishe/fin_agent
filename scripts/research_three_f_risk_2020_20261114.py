from pathlib import Path
import json, numpy as np,pandas as pd
np.seterr(all="ignore")
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import rankdata
P=Path('outputs/三模型全月_20261113');O=P/'D0交叉排除_20261114'/'2020模型';O.mkdir(exist_ok=True,parents=True)
features=['board_streak_days','streak_return','d0_open_gap','d0_range','d0_lower_shadow','d0_close_position','first_open_gap','first_range','first_one_price','d0_volume_ratio_pre5','d0_amount_ratio_pre5','d0_turnover_pct','d0_amount_100m','d0_volume_ratio_first','d0_turnover_ratio_first','market_limit_count','market_prev_limit_count','market_up_fraction','market_median_return','pre5_volatility','pre5_up_count','market_reseal_fraction','d0_one_price','amount_board_rank','turnover_board_rank','log_float_mv_proxy','log_price','normal_days5','normal_return5','normal_volatility5','normal_return10','normal_volatility10','normal_turnover5','normal_range5','normal_drawdown5','d0_normal_volume_ratio','board_normal_volume_ratio','d0_normal_amount_ratio','board_normal_amount_ratio','board_turnover_sum','board_one_price_fraction','board_range_mean','board_volume_cv','board_volume_slope','d0_board_volume_ratio','normal_volume_ratio5_10','首30分钟涨幅','末30分钟涨幅','上午涨幅','下午涨幅','首30分钟成交量占比','末30分钟成交量占比','下午成交量占比','下午相对上午成交量','上涨K成交量方向比','上涨分钟比例','全日分钟波动','走势效率','触板时间','收板分钟占比','分钟收盘开板次数','封板时成交量占比','尾盘板下分钟占比']
frames=[pd.read_pickle(P/'早期/全部输入.pkl.gz'),pd.read_pickle(P/'全部输入.pkl.gz'),pd.read_pickle(P/'近月/全部输入.pkl.gz')];a=pd.concat(frames,ignore_index=True).drop_duplicates(['d0_date','symbol'],keep='first');a['d0_date']=a.d0_date.astype(str);a['symbol']=a.symbol.astype(str).str.zfill(6);a['D1日期x']=a.d1_date.astype(str);a['year']=a.d0_date.str[:4].astype(int)
# Features are explicit D0-only whitelist; future prices used solely as outcomes.
valid=(a[['d1_open','d1_close','d1_cap','d1_preclose']].notna().all(axis=1)) & (a.d1_open>0)&(a.d1_preclose>0)
a['f_label']=np.where(valid,((a.d1_open<a.d1_cap-.005)&(a.d1_close<a.d1_cap-.005)&(a.d1_close<a.d1_open-.005)).astype(float),np.nan)
a['green_label']=np.where(valid,((a.d1_open<a.d1_preclose-.005)&(a.d1_close<a.d1_preclose-.005)).astype(float),np.nan)
s=pd.read_pickle(P/'D1状态样本全景_20261114/全候选D1分支明细.pkl.gz');s['symbol']=s.symbol.astype(str).str.zfill(6);s['d0']=s.d0.astype(str);assert not s.duplicated(['d0','symbol']).any();s=s.merge(a[['d0_date','symbol','f_label','green_label']+features],left_on=['d0','symbol'],right_on=['d0_date','symbol'],how='left',validate='one_to_one');s=s[(s.买入状态=='假设买入') & s.d0_date.notna()].copy(); known=(s['D2行情']=='有效') & s[['D2开盘','D2收盘','D2前收']].notna().all(axis=1);s['d2green_label']=np.where(known,((s['D2开盘']<s['D2前收']-.005)&(s['D2收盘']<s['D2前收']-.005)).astype(float),np.nan);s['D1日期x']=s['D1日期'].astype(str);s['D2日期x']=s['D2日期'].astype(str);s['year']=s.d0.str[:4].astype(int);d1=s.f_label.notna();assert (s.loc[d1,'f_label'].astype(bool)==s.loc[d1,'D1分支'].str.startswith('F')).all()
# Histories must be mature at each fold's cutoff, including validation labels.
def data(df):return df[features].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan).to_numpy(float)
def fit(train,label):
 X=data(train);y=train[label].to_numpy(float); med=np.nanmedian(X,axis=0); med=np.where(np.isfinite(med),med,0);q1=np.nanpercentile(X,25,axis=0);q3=np.nanpercentile(X,75,axis=0); scale=q3-q1;scale=np.where(np.isfinite(scale)&(scale>1e-8),scale,1)
 def trans(v):
  missing=~np.isfinite(v);v=np.where(missing,med,v);v=np.clip((v-med)/scale,-5,5);return np.column_stack([np.ones(len(v)),v,np.maximum(v-1,0),np.maximum(-v-1,0),missing.astype(float)])
 z=trans(X);reg=max(8.0,len(y)*0.003)
 def fun(w):
  t=z@w; loss=(np.logaddexp(0,t)-y*t).sum()+.5*reg*np.dot(w[1:],w[1:]); grad=z.T@(expit(t)-y);grad[1:]+=reg*w[1:];return loss,grad
 res=minimize(fun,np.zeros(z.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':500,'ftol':1e-8});return med,scale,res.x,res.success,res.message

def score(df,med,scale,w):
 v=data(df);missing=~np.isfinite(v);v=np.where(missing,med,v);v=np.clip((v-med)/scale,-5,5);z=np.column_stack([np.ones(len(v)),v,np.maximum(v-1,0),np.maximum(-v-1,0),missing.astype(float)]);return expit(z@w)

def auc(y,p):
 y=np.asarray(y,dtype=bool);n=y.sum();m=len(y)-n
 return (rankdata(p)[y].sum()-n*(n+1)/2)/(n*m) if n and m else np.nan
pred=[];folds=[]
for name,trainpool,label,datacol in [('选中池_F',s,'f_label','D1日期x'),('全涨停池_F',a,'f_label','D1日期x')]:
 for yr in (2020,):
  val_year=yr-1;train_end=f'{val_year}-01-01';test_start=f'{yr}-01-01';train=trainpool[(trainpool.year<val_year)&trainpool[label].notna()&(trainpool[datacol]<train_end)];v=s[(s.year==val_year)&s[label].notna()&(s[datacol]<test_start)];test=s[(s.year==yr)&s[label].notna()];
  if len(train)<300 or len(v)<100 or len(test)<30:print('SKIP',name,yr,len(train),len(v),len(test),flush=True);continue
  med,scale,w,success,msg=fit(train,label);pv=score(v,med,scale,w);pt=score(test,med,scale,w); thresholds=np.quantile(pv,[.8,.9]); zz=test[['d0','symbol','year','D1分支','结果状态','真实模拟收益%','D1开盘','D1收盘','D1前收','D2开盘','D2收盘','D2前收','f_label','green_label','d2green_label']].copy();zz['实验']=name;zz['风险分']=pt;zz['验证80分位阈值']=thresholds[0];zz['验证90分位阈值']=thresholds[1];zz['触发20']=pt>=thresholds[0];zz['触发10']=pt>=thresholds[1];pred.append(zz);np.savez_compressed(O/f'{name}_{yr}_numpy模型.npz',features=np.array(features),median=med,scale=scale,weights=w,thresholds=thresholds);folds.append({'实验':name,'测试年':yr,'训练数':len(train),'验证选中数':len(v),'测试选中数':len(test),'训练最晚D0':str(train.d0_date.max() if 'd0_date' in train else train.d0.max()),'训练标签最晚日期':str(train[datacol].max()),'验证标签最晚日期':str(v[datacol].max()),'训练优化收敛':bool(success),'训练信息':str(msg),'测试目标AUC':auc(test[label],pt),'验证80阈':thresholds[0],'验证90阈':thresholds[1]});print(name,yr,'train',len(train),'val',len(v),'test',len(test),'auc',round(auc(test[label],pt),3),'ok',success,flush=True)
q=pd.concat(pred,ignore_index=True);q.to_pickle(O/'逐候选前推风险分.pkl.gz');q.to_csv(O/'逐候选前推风险分.csv.gz',index=False,compression='gzip',encoding='utf-8-sig');pd.DataFrame(folds).to_csv(O/'训练验证窗口.csv',index=False,encoding='utf-8-sig')
rows=[]
for name,g in q.groupby('实验'):
 for group,gg in [('全部',g)]+[(str(y),t) for y,t in g.groupby('year')]:
  for k in ['触发10','触发20']:
   z=gg[gg[k]];c=z[z.结果状态=='全部卖出'];r=c['真实模拟收益%'];base=gg[gg.结果状态=='全部卖出'];allr=base['真实模拟收益%'];rows.append({'实验':name,'测试年':group,'测试候选数':len(gg),'触发方式':k,'触发数':len(z),'结清触发数':len(c),'触发F':int(z.f_label.sum()),'触发绿开绿收':int(z.green_label.sum()),'触发D2绿开绿收':int(z.d2green_label.sum()),'避开盈利':int((r>0).sum()),'避开亏损':int((r<0).sum()),'避开亏超7':int((r< -7).sum()),'避开亏超10':int((r< -10).sum()),'避开盈利合计百分比':float(r[r>0].sum()),'避开亏损合计百分比':float(r[r<0].sum()),'触发平均收益%':r.mean(),'同年基准平均收益%':allr.mean(),'原结清笔数':len(base),'原盈利':int((allr>0).sum()),'原亏超7':int((allr< -7).sum()),'原亏超10':int((allr< -10).sum())})
r=pd.DataFrame(rows);r.to_csv(O/'按年策略影响.csv',index=False,encoding='utf-8-sig');print(r[(r.测试年=='全部')&(r.触发方式=='触发10')].to_string(index=False),flush=True)
