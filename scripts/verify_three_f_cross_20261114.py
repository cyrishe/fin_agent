from pathlib import Path
import pandas as pd,numpy as np,hashlib,json
P=Path('outputs/三模型全月_20261113');T=P/'D0交叉排除_20261114';audits=[]
for y in range(2020,2027):
 d=T/str(y);c=pd.read_pickle(d/'候选与方案.pkl.gz');o=pd.read_csv(P/'账户'/str(y)/'每日选股.csv',dtype={'symbol':str}); old=c[c.选中次数.gt(0)];assert set(zip(old.d0_date,old.symbol))==set(zip(o.d0_date,o.symbol))
 q=pd.read_csv(d/'全部候选逐笔.csv',dtype={'symbol':str});q=q[(q['mode']=='10万限量')&(q.strategy=='原卖法')];e=pd.read_pickle(d/'卖出分钟事件.pkl.gz');e=e[(e['mode']=='10万限量')&(e.strategy=='原卖法')];assert not q.duplicated(['d0','symbol']).any();assert (e.shares<=e.bar_volume*.1+1e-7).all();assert (e.day.astype(str)>e.buy_day.astype(str)).all();cash=e.assign(cash=lambda x:x.shares*x.price*.999).groupby(['d0','symbol']).cash.sum().rename('重算现金').reset_index();m=q.merge(cash,on=['d0','symbol'],how='left');z=m[m.结果状态.eq('全部卖出')];err=np.max(np.abs(100*(z.重算现金/100000-1)-z.收益_pct)) if len(z) else 0;assert err<1e-8;assert ((~c['双命中'])|c['选中池F触发']).all() and ((~c['双命中'])|c['全池F触发']).all();
 for typ in ['原池_双命中','原池_任一命中','扩池_双命中','扩池_任一命中']:
  assert not (c[typ]&c[typ.split('_')[1]]).any()
  if typ.startswith('扩池'):assert c[c[typ]].groupby('d0_date').size().max()<=5
 prev=P/'D0交叉排除_20261114'/'2020模型' if y==2020 else P/'D0预测弱势与撤单_20261114'; pred=pd.read_pickle(prev/'逐候选前推风险分.pkl.gz'); pred=pred[pred.year.eq(y)]; errors=[]
 for lab,col in [('选中池_F','选中池F风险分'),('全涨停池_F','全池F风险分')]:
  t=old.merge(pred[pred.实验.eq(lab)],left_on=['d0_date','symbol'],right_on=['d0','symbol'],validate='one_to_one'); error=(t[col]-t.风险分).abs().max();assert error<1e-10;errors.append(float(error))
 audits.append({'score_max_error':max(errors),'year':y,'original_ids_verified':len(old),'added':len(q),'sold':len(z),'max_return_error_pctpoints':float(err),'sales':len(e),'remaining_statuses':q.结果状态.value_counts().to_dict()})
files=['scripts/research_three_f_risk_2020_20261114.py','scripts/replay_three_f_cross_prepare_20261114.py','scripts/replay_three_f_cross_requests_20261114.py','scripts/replay_three_f_cross_added_20261114.py','scripts/replay_three_f_cross_summary_20261114.py','scripts/replay_three_f_cross_diff_20261114.py','scripts/verify_three_f_cross_20261114.py'];hashes={s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in files};(T/'核验.json').write_text(json.dumps({'checks':audits,'scripts_sha256':hashes},ensure_ascii=False,indent=2));print(json.dumps(audits,ensure_ascii=False,indent=2))
