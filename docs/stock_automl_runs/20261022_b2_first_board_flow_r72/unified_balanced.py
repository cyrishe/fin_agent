from pathlib import Path
import json,hashlib,joblib
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,roc_auc_score,confusion_matrix,balanced_accuracy_score,f1_score
R=Path('/data/cyris/work/b2_first_board_flow_r72_20261022')
t=pd.read_csv(R/'首板前量价资金趋势_709候选.csv',dtype={'股票':str,'D0选中日':str,'首板日':str,'D3日':str})
cols=json.loads((R/'feature_columns.json').read_text())
y=np.select([t['三日急跌标签'].eq(1),t['三日持续大涨标签'].eq(1)],['急跌','持续大涨'],default='中间状态')
train=(t.D0选中日<'2025-01-01')&(t.D3日<'2025-01-01')
valid=(t.D0选中日>='2025-01-01')&(t.D0选中日<'2026-01-01')&(t.D3日<'2026-01-01')
test=t.D0选中日>='2026-01-01'
eps=t['股票']+'|'+t['首板日'];w=(1/eps[train].map(eps[train].value_counts())).to_numpy();w/=w.mean()
def pipe(model,scale=False):
 a=[('impute',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True))]
 if scale:a.append(('scale',StandardScaler()))
 a.append(('model',model));return Pipeline(a)
specs=[
 ('balanced_lr_c025',pipe(LogisticRegression(C=.25,class_weight='balanced',max_iter=3000,random_state=42),True)),
 ('balanced_lr_c1',pipe(LogisticRegression(C=1,class_weight='balanced',max_iter=3000,random_state=42),True)),
 ('balanced_rf',pipe(RandomForestClassifier(n_estimators=300,max_depth=5,min_samples_leaf=16,max_features=.5,class_weight='balanced_subsample',random_state=42,n_jobs=2))),
 ('balanced_hgb',pipe(HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=7,min_samples_leaf=20,learning_rate=.05,l2_regularization=10,class_weight='balanced',early_stopping=False,random_state=42))),
]
def report(mask,model,name,split):
 p=model.predict_proba(t.loc[mask,cols]);classes=list(model.classes_)
 pred=np.array(classes)[np.argmax(p,axis=1)]
 d={'candidate':name,'split':split,'n':int(mask.sum()),'macro_f1':float(f1_score(y[mask],pred,labels=['急跌','中间状态','持续大涨'],average='macro',zero_division=0)),
    'balanced_accuracy':float(balanced_accuracy_score(y[mask],pred))}
 for c in ['急跌','持续大涨']:
  i=classes.index(c)
  d[f'{c}_ap']=float(average_precision_score(y[mask]==c,p[:,i]))
  d[f'{c}_auc']=float(roc_auc_score(y[mask]==c,p[:,i]))
  d[f'{c}_predicted']=int(sum(pred==c))
  d[f'{c}_correct']=int(sum((pred==c)&(y[mask]==c)))
 return d
fitted=[];rows=[]
for name,m in specs:
 m.fit(t.loc[train,cols],y[train],model__sample_weight=w)
 r=report(valid,m,name,'validation');rows.append(r);fitted.append((r,name,m))
selected=max(fitted,key=lambda z:(z[0]['macro_f1'],z[0]['balanced_accuracy']))
_,name,m=selected
for _,cand,mdl in fitted:
 rows.append(report(test,mdl,cand,'test' if cand==name else 'test_diagnostic'))
pd.DataFrame(rows).to_csv(R/'统一三分类平衡候选指标.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
proba=m.predict_proba(t[cols]);classes=list(m.classes_);pred=np.array(classes)[np.argmax(proba,axis=1)]
out=t[['股票','首板日','D0选中日','V2放行','三日急跌标签','三日持续大涨标签','D2开收跌停','D2D3连续收盘跌停']].copy()
out['真实类别']=y;out['预测类别_实验']=pred
for c in ['急跌','中间状态','持续大涨']:out[f'{c}评分']=proba[:,classes.index(c)]
out.to_csv(R/'统一三分类平衡逐笔预测.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
path=R/f'unified_balanced_{name}_train2022_24.joblib';joblib.dump(m,path)
def cm(mask):
 return confusion_matrix(y[mask],pred[mask],labels=['急跌','中间状态','持续大涨']).tolist()
obj={'selection':'max 2025 validation macro F1, tie balanced accuracy; class-balanced candidate models',
 'selected':name,'model_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
 'validation':report(valid,m,name,'validation'),'test':report(test,m,name,'test'),
 'class_order':['急跌','中间状态','持续大涨'],'validation_confusion':cm(valid),'test_confusion':cm(test)}
(R/'unified_balanced_selection.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
manifest=json.loads((R/'manifest.json').read_text())
for p in [R/'unified_balanced.py',R/'统一三分类平衡候选指标.csv',R/'统一三分类平衡逐笔预测.csv',R/'unified_balanced_selection.json',path]:
 manifest['files'][p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
(R/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(obj,ensure_ascii=False,indent=2))
