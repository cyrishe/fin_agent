from pathlib import Path
import json,joblib,hashlib
import pandas as pd,numpy as np
from sklearn.base import clone
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022')
f=pd.read_pickle(R/'panel306.pkl');p=pd.read_pickle(R/'predictions306.pkl')
info=json.loads((R/'model_info.json').read_text())
s=pd.read_pickle('/data/cyris/work/b2_model_economics_r73_20261022/joined_98.pkl').set_index('id')
tr=f.split.eq('train');rows=[];models={};details=[]
# Frozen extension after user clarified economic objective. No fitting or cutoff selection on 2026.
protocol={'target':'original D2-close net return<0; sample weight=abs(net return)/training mean abs return. Weighted probability balances avoided monetary losses vs forgone monetary profits, not floor recall.',
'models':'Clone four fitted preprocessing/model configurations; refit all preprocessing on train152 and logistic C0.1 with profit/loss weights.',
'thresholds':'0.5 OR strict >training score quantile 0.60/0.70/0.80/0.90. All combinations disclosed.',
'selection':'maximize 2025 equal-ticket original-close cancellation net contribution; if <=0 choose no new filter. No minimum recall or requirement to catch every consecutive floor. Fixed quantile classification sensitivity also fully disclosed.',
'limits':'2026 already observed; 20 extra candidate policies increase validation selection uncertainty. Original-close objective differs from current morning7 sale; test both. Flow historical vintage incomplete. No production change.'}
(R/'economic_extension_protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
for name in ['D1','D1_news','D1_flow','D1_news_flow']:
 cols=info[name]['features'];m=clone(joblib.load(R/('model_'+name+'.joblib')))
 y=f.loc[tr,'original_net'].lt(0);sw=f.loc[tr,'original_net'].abs();sw=sw/sw.mean()
 m.fit(f.loc[tr,cols],y,model__sample_weight=sw)
 score=m.predict_proba(f[cols])[:,1];joblib.dump(m,R/('economic_'+name+'.joblib'))
 models[name]={'features':cols,'sha256':hashlib.sha256((R/('economic_'+name+'.joblib')).read_bytes()).hexdigest(),'thresholds':{}}
 for t in ['half','q60','q70','q80','q90']:
  th=.5 if t=='half' else float(np.quantile(score[tr],int(t[1:])/100))
  key='fit_econ_'+name+'_'+t;flag=score>th
  models[name]['thresholds'][t]=th;p[key]=flag.astype(int);p[key+'_score']=score
  for split in ['train','valid','test']:
   g=f.loc[f.split.eq(split)];a=flag[f.split.eq(split)]
   net=g.original_net.to_numpy();n=net[a]
   rows.append({'family':'money_weighted','model':name,'policy':key,'threshold_rule':t,'threshold':th,'split':split,'n':len(g),'alerts':int(a.sum()),'losers_avoided':int((n<0).sum()),'winners_missed':int((n>0).sum()),'forgone_1w':float(n[n>0].sum()*1e4),'avoided_1w':float(-n[n<0].sum()*1e4),'net_1w':float(-n.sum()*1e4),'floor_caught':int(g.loc[a,'y_union'].sum()),'continuous_caught':int(g.loc[a,'连续跌停'].sum())})
# Same fixed risk classifiers: evaluate costs at more selective train cutoffs, no test tuning.
for name,mi in info.items():
 score=p['fit_'+name+'_score'].to_numpy()
 for q in [.60,.70,.75,.80,.90]:
  th=float(np.quantile(score[tr],q));a=score>th
  p['fit_sens_'+name+'_q'+str(int(q*100))]=a.astype(int)
  for split in ['train','valid','test']:
   g=f[f.split.eq(split)];flag=a[f.split.eq(split)];net=g.original_net.to_numpy()[flag]
   rows.append({'family':'floor_probability','model':name,'policy':name+'_q'+str(int(q*100)),'threshold_rule':'q'+str(int(q*100)),'threshold':th,'split':split,'n':len(g),'alerts':int(flag.sum()),'losers_avoided':int((net<0).sum()),'winners_missed':int((net>0).sum()),'forgone_1w':float(net[net>0].sum()*1e4),'avoided_1w':float(-net[net<0].sum()*1e4),'net_1w':float(-net.sum()*1e4),'floor_caught':int(g.loc[flag,'y_union'].sum()),'continuous_caught':int(g.loc[flag,'连续跌停'].sum())})
table=pd.DataFrame(rows);table.to_csv(R/'经济目标与过滤强度_完整对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
v=table[(table.family=='money_weighted')&(table.split=='valid')].sort_values(['net_1w','alerts'],ascending=[False,True])
chosen=v.iloc[0].policy if v.iloc[0].net_1w>0 else None
(R/'economic_selection.json').write_text(json.dumps({'chosen':chosen,'validation':v.to_dict('records'),'models':models},ensure_ascii=False,indent=2))
rv=table[(table.family=='floor_probability')&(table.split=='valid')].sort_values(['net_1w','alerts'],ascending=[False,True])
(R/'risk_threshold_selection.json').write_text(json.dumps({'chosen_validation_only':rv.iloc[0].policy,'all_validation':rv.to_dict('records')},ensure_ascii=False,indent=2))
p.to_pickle(R/'predictions306.pkl');p.to_csv(R/'306逐笔组合预测.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print('VALIDATION ONLY SELECTED',chosen)
print(table[table.policy.eq(chosen)].to_string(index=False))
print(v[['model','threshold_rule','alerts','forgone_1w','avoided_1w','net_1w']].to_string(index=False))

