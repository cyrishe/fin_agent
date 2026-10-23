from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd,joblib
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,roc_auc_score,brier_score_loss
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
t=pd.read_csv(R/'首板前量价资金趋势_709候选.csv',dtype={'股票':str,'D0选中日':str,'首板日':str,'D3日':str})
features=json.loads((R/'feature_columns.json').read_text())
volume=[c for c in features if any(k in c for k in ['volume','amount','turnover','prehistory_days'])]
# The full model adds preboard directional price/volume proxies and first-board price path.
assert len(volume)<len(features)
train=(t['D0选中日']<'2025-01-01')&(t['D3日']<'2025-01-01')
valid=(t['D0选中日']>='2025-01-01')&(t['D0选中日']<'2026-01-01')&(t['D3日']<'2026-01-01')
test=(t['D0选中日']>='2026-01-01')&(t['D0选中日']<'2027-01-01')
purged=~(train|valid|test)
assert int(train.sum())==419 and int(valid.sum())==165 and int(test.sum())==120 and int(purged.sum())==5
episode=t['股票']+'|'+t['首板日']
episode_count=episode[train].value_counts()
weight=1/episode[train].map(episode_count).to_numpy()
weight=weight/np.mean(weight)
def pipe(estimator,scaled=True):
 steps=[('impute',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True))]
 if scaled:steps.append(('scale',StandardScaler()))
 steps.append(('model',estimator))
 return Pipeline(steps)
def metric(y,p):
 return {'n':len(y),'positives':int(np.sum(y)),'base_rate':float(np.mean(y)),
         'average_precision':float(average_precision_score(y,p)),
         'roc_auc':float(roc_auc_score(y,p)) if len(np.unique(y))==2 else None,
         'brier':float(brier_score_loss(y,p))}
def candidate_specs():
 return [
  ('volume_lr_c025',volume,pipe(LogisticRegression(C=.25,max_iter=3000,random_state=42))),
  ('full_lr_c025',features,pipe(LogisticRegression(C=.25,max_iter=3000,random_state=42))),
  ('full_lr_c1',features,pipe(LogisticRegression(C=1,max_iter=3000,random_state=42))),
  ('full_rf',features,pipe(RandomForestClassifier(n_estimators=300,max_depth=5,min_samples_leaf=16,max_features=.5,random_state=42,n_jobs=2),False)),
  ('full_hgb',features,pipe(HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=7,min_samples_leaf=20,learning_rate=.05,l2_regularization=10,early_stopping=False,random_state=42),False)),
 ]
metrics=[];pred=t[['股票','首板日','D0选中日','D1日','D2日','D3日','V2放行','本轮D0已连板天数_仅审计','D0至D3收盘涨跌幅','D0至D3最低收盘涨跌幅','D2开收跌停','D3收盘跌停','D2D3连续收盘跌停','三日急跌标签','三日持续大涨标签']].copy()
summary={'split':{'train_rows':int(train.sum()),'valid_rows':int(valid.sum()),'test_rows':int(test.sum()),'purged_boundary_rows':int(purged.sum()),'train_episodes':int(episode[train].nunique()),'valid_episodes':int(episode[valid].nunique()),'test_episodes':int(episode[test].nunique())},
 'features':{'full_count':len(features),'volume_count':len(volume),'full':features,'volume':volume},
 'targets':{},'selection_metric':'2025 validation average precision; ties by validation ROC AUC; 2026 untouched until selection','alert_threshold':'2025 validation 80th percentile of selected model score'}
for target,label in [('crash','三日急跌标签'),('strong','三日持续大涨标签')]:
 y=t[label].to_numpy(int)
 results=[]
 for name,cols,model in candidate_specs():
  model.fit(t.loc[train,cols],y[train],model__sample_weight=weight)
  pv=model.predict_proba(t.loc[valid,cols])[:,1]
  mv=metric(y[valid],pv)
  results.append({'name':name,'columns':cols,'model':model,'validation':mv,'pv':pv})
  metrics.append({'target':target,'candidate':name,'split':'validation',**mv})
 selected=max(results,key=lambda z:(z['validation']['average_precision'],z['validation']['roc_auc']))
 name=selected['name'];cols=selected['columns'];model=selected['model']
 path=R/f'{target}_{name}_train2022_24.joblib';joblib.dump(model,path)
 scores=model.predict_proba(t[cols])[:,1]
 pred[f'{target}_score']=scores
 pred[f'{target}_alert']=((scores>=np.quantile(selected['pv'],.8)) & (valid|test)).astype(int)
 threshold=float(np.quantile(selected['pv'],.8))
 for period,mask in [('train',train),('validation',valid),('test',test),('test_v2',test & t.V2放行.eq(1))]:
  m=metric(y[mask],scores[mask])
  alerted=scores[mask]>=threshold
  m.update({'alert_n':int(np.sum(alerted)),'alert_positive':int(np.sum(y[mask][alerted])),
            'alert_precision':float(np.mean(y[mask][alerted])) if np.sum(alerted) else None,
            'alert_recall':float(np.sum(y[mask][alerted])/np.sum(y[mask])) if np.sum(y[mask]) else None})
  summary['targets'].setdefault(target,{})[period]=m
  metrics.append({'target':target,'candidate':name,'split':period,**m})
 # Report frozen test metrics for every candidate, but never re-select by them.
 for z in results:
  if z['name']==name:continue
  pt=z['model'].predict_proba(t.loc[test,z['columns']])[:,1]
  metrics.append({'target':target,'candidate':z['name'],'split':'test_diagnostic',**metric(y[test],pt)})
 summary['targets'][target]['selected']=name
 summary['targets'][target]['threshold']=threshold
 summary['targets'][target]['model_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 summary['targets'][target]['validation_candidates']={z['name']:z['validation'] for z in results}
 print(target,'selected',name,'val AP',round(selected['validation']['average_precision'],4),'test AP',round(summary['targets'][target]['test']['average_precision'],4),'test AUC',round(summary['targets'][target]['test']['roc_auc'],4),'test alerts',summary['targets'][target]['test']['alert_n'],'caught',summary['targets'][target]['test']['alert_positive'])
m=pd.DataFrame(metrics)
m.to_csv(R/'模型候选与分期指标.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
pred['实验高分组']=np.select([pred.crash_alert.eq(1),pred.strong_alert.eq(1)],['急跌模型高分','上涨模型高分'],default='其余')
pred.to_csv(R/'逐笔冻结预测_709候选.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
# Check labels independently from model table and stop if one of the targets is mistaken.
assert pred['D2开收跌停'].sum()==161
assert pred.loc[pred.V2放行.eq(1),'D2开收跌停'].sum()==67
assert pred['三日急跌标签'].sum()==265 and pred['三日持续大涨标签'].sum()==106
assert not (pred.crash_alert.eq(1)&pred.strong_alert.eq(1)&pred['实验高分组'].eq('上涨模型高分')).any()
(R/'selection.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
manifest=json.loads((R/'manifest.json').read_text())
for p in [R/'模型候选与分期指标.csv',R/'逐笔冻结预测_709候选.csv',R/'selection.json',R/'model.py']+list(R.glob('*_train2022_24.joblib')):
 manifest['files'][p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
(R/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('model feature total',len(features),'volume',len(volume),'full metrics saved')
