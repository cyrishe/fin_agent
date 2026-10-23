from pathlib import Path
import json,hashlib,joblib
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,roc_auc_score,confusion_matrix,balanced_accuracy_score
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
t=pd.read_csv(R/'首板前量价资金趋势_709候选.csv',dtype={'股票':str,'D0选中日':str,'首板日':str,'D3日':str})
cols=json.loads((R/'feature_columns.json').read_text())
volume=[c for c in cols if any(k in c for k in ['volume','amount','turnover','prehistory_days'])]
y=np.select([t['三日急跌标签'].eq(1),t['三日持续大涨标签'].eq(1)],['急跌','持续大涨'],default='中间状态')
assert set(y)=={'急跌','持续大涨','中间状态'}
train=(t.D0选中日<'2025-01-01')&(t.D3日<'2025-01-01')
valid=(t.D0选中日>='2025-01-01')&(t.D0选中日<'2026-01-01')&(t.D3日<'2026-01-01')
test=t.D0选中日>='2026-01-01'
eps=t['股票']+'|'+t['首板日'];cnt=eps[train].value_counts();w=(1/eps[train].map(cnt)).to_numpy();w/=w.mean()
def pipe(model,scale=False):
 steps=[('impute',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True))]
 if scale:steps.append(('scale',StandardScaler()))
 steps.append(('model',model))
 return Pipeline(steps)
specs=[
 ('volume_lr',volume,pipe(LogisticRegression(C=.25,max_iter=3000,random_state=42),True)),
 ('full_lr_c025',cols,pipe(LogisticRegression(C=.25,max_iter=3000,random_state=42),True)),
 ('full_lr_c1',cols,pipe(LogisticRegression(C=1,max_iter=3000,random_state=42),True)),
 ('full_rf',cols,pipe(RandomForestClassifier(n_estimators=300,max_depth=5,min_samples_leaf=16,max_features=.5,random_state=42,n_jobs=2))),
 ('full_hgb',cols,pipe(HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=7,min_samples_leaf=20,learning_rate=.05,l2_regularization=10,early_stopping=False,random_state=42))),
]
records=[];fitted=[]
for name,used,model in specs:
 model.fit(t.loc[train,used],y[train],model__sample_weight=w)
 classes=list(model.classes_)
 def report(mask,split):
  proba=model.predict_proba(t.loc[mask,used])
  idx={c:i for i,c in enumerate(classes)}
  ap={c:float(average_precision_score(y[mask]==c,proba[:,idx[c]])) for c in ['急跌','持续大涨']}
  auc={c:float(roc_auc_score(y[mask]==c,proba[:,idx[c]])) for c in ['急跌','持续大涨']}
  base={c:float(np.mean(y[mask]==c)) for c in ['急跌','持续大涨']}
  macro_lift=float(np.mean([ap[c]-base[c] for c in ap]))
  pred=np.array(classes)[np.argmax(proba,axis=1)]
  return {'candidate':name,'split':split,'n':int(mask.sum()),'ap_crash':ap['急跌'],'ap_strong':ap['持续大涨'],
    'auc_crash':auc['急跌'],'auc_strong':auc['持续大涨'],'base_crash':base['急跌'],'base_strong':base['持续大涨'],
    'mean_ap_minus_base':macro_lift,'balanced_accuracy':float(balanced_accuracy_score(y[mask],pred)),
    'predicted_crash':int(sum(pred=='急跌')),'predicted_strong':int(sum(pred=='持续大涨'))}
 rv=report(valid,'validation')
 records.append(rv);fitted.append((rv,name,used,model,report))
selected=max(fitted,key=lambda z:(z[0]['mean_ap_minus_base'],z[0]['balanced_accuracy']))
_,name,used,model,report=selected
for _,cand,c,mdl,rep in fitted:
 if cand==name:
  records.extend([rep(train,'train'),rep(test,'test')])
 else:
  records.append(rep(test,'test_diagnostic'))
pd.DataFrame(records).to_csv(R/'统一三分类候选指标.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
proba=model.predict_proba(t[used]);idx={c:i for i,c in enumerate(model.classes_)}
pred=np.array(model.classes_)[np.argmax(proba,axis=1)]
out=t[['股票','首板日','D0选中日','V2放行','三日急跌标签','三日持续大涨标签','D2开收跌停','D2D3连续收盘跌停']].copy()
out['真实类别']=y;out['预测类别_实验']=pred
for c in ['急跌','中间状态','持续大涨']:out[f'{c}评分']=proba[:,idx[c]]
out.to_csv(R/'统一三分类逐笔预测.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
modelpath=R/f'unified_{name}_train2022_24.joblib';joblib.dump(model,modelpath)
def cm(mask):
 return pd.DataFrame(confusion_matrix(y[mask],pred[mask],labels=['急跌','中间状态','持续大涨']),
  index=['急跌','中间状态','持续大涨'],columns=['急跌','中间状态','持续大涨']).to_dict()
info={'selection':'max validation mean(AP - class prevalence) for crash and strong; tie balanced accuracy',
 'selected':name,'classes':list(model.classes_),'model_sha256':hashlib.sha256(modelpath.read_bytes()).hexdigest(),
 'train_counts':pd.Series(y[train]).value_counts().to_dict(),'validation_counts':pd.Series(y[valid]).value_counts().to_dict(),'test_counts':pd.Series(y[test]).value_counts().to_dict(),
 'validation':report(valid,'validation'),'test':report(test,'test'),
 'validation_confusion':cm(valid),'test_confusion':cm(test)}
(R/'unified_selection.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n')
manifest=json.loads((R/'manifest.json').read_text())
for p in [R/'unified.py',R/'统一三分类候选指标.csv',R/'统一三分类逐笔预测.csv',R/'unified_selection.json',modelpath]:
 manifest['files'][p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
(R/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(info,ensure_ascii=False,indent=2))
