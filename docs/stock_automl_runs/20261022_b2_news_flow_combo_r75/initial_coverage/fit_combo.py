from pathlib import Path
import pandas as pd,numpy as np,json,joblib,hashlib,platform,sklearn
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler,OneHotEncoder,FunctionTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022')
f=pd.read_pickle(R/'panel306.pkl')
sel=json.loads((R/'inputs/selection_frozen.json').read_text())
core=next(x['rule']['columns'] for x in sel['candidates'] if x['id']=='union_early_lr01')
news=['news_days_r5','news_day_change','news_doc_logchange','risk_days_r5','risk_change','risk_share','new_families','valuation_warning','specific_denial','generic_denial','deal_uncertainty','abnormal_days_r5']
fund=['flow_main_frac_D0','flow_main_pre5','flow_main_change','flow_main_trend5','flow_main_negative5','flow_huge_gross_share','flow_huge_frac_D0','flow_large_frac_D0']
groups={'D1':core,'news':news,'flow':fund,'D1_news':core+news,'D1_flow':core+fund,'D1_news_flow':core+news+fund}
protocol={'news_cutoff':'D0 15:00, date-only conservative next calendar day; exact last5 vs preceding5 exchange-availability days','fund_cutoff':'D0 EOD data for D1 prebuy; never D1 EOD input. Historical snapshots not fully PIT verified. Flow models exploratory.','split':'unchanged train152/valid54/test98/purged2','models':'6 fixed logistic C0.1, no class weights; 2/98 train clipping, median, scale; strict score>trainingQ75','primary_target':'D2 open OR close floor','secondary_targets':'D2D3 consecutive close floor; all-day lock; R72 three-day crash','selection':'validation-only: >=6 alerts, <=35% alerts, >=3 target caught, >=1.25 precision lift, nonnegative equal-ticket original-close cancellation benefit; maximize remaining-risk reduction then fewer alerts. None if no eligible candidate. No choosing 2026 winner.','rules':['repeat risk>=2 last5','announcement days last5>=3 and rise>=2 vs preceding5','risk days last3>=2 and increase>=1 vs preceding3','specific denial','generic denial','D0 main net negative','D0 main net/turnover<-5% and drop>5pp vs prior5','(repeat/attention/specific denial) AND main outflow'],'comparisons':'fixed D1 frozen union41/close34, each rule alone / OR union / AND union / incremental half-size beyond union; all reported','limits':'all 2026 outcomes previously observed; no new blind test; price-only liquidation assumptions; flow source timestamps audited but not original vendor vintage archive'}
(R/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
flags={};scores={};rules=[];metrics=[];info={}
for name in ['union_early_lr01','close_early_lr1']:
 rule=next(x['rule'] for x in sel['candidates'] if x['id']==name);m=joblib.load(R/'inputs'/rule['file']);score=m.predict_proba(f[rule['columns']])[:,1]
 flags[name]=score>rule['threshold'];scores[name]=score
 assert int(flags[name][f.split.eq('test')].sum())==(41 if name.startswith('union') else 34)
for name,cols in groups.items():
 tr=f[f.split.eq('train')];nums=[c for c in cols if c!='opened_after_touch']
 lo=tr[nums].quantile(.02).to_numpy();hi=tr[nums].quantile(.98).to_numpy()
 parts=[('numeric',Pipeline([('winsor',FunctionTransformer(np.clip,kw_args={'a_min':lo,'a_max':hi},feature_names_out='one-to-one')),('impute',SimpleImputer(strategy='median',keep_empty_features=True)),('scale',StandardScaler())]),nums)]
 if 'opened_after_touch' in cols:parts.append(('category',OneHotEncoder(categories=[['否','是','分钟内顺序不明']],handle_unknown='ignore',sparse_output=False),['opened_after_touch']))
 m=Pipeline([('features',ColumnTransformer(parts)),('model',LogisticRegression(C=.1,max_iter=5000,random_state=20261021))]);m.fit(tr[cols],tr.y_union)
 score=m.predict_proba(f[cols])[:,1];threshold=float(np.quantile(score[f.split.eq('train')],.75));flags['fit_'+name]=score>threshold;scores['fit_'+name]=score
 joblib.dump(m,R/('model_'+name+'.joblib'))
 info[name]={'features':cols,'threshold':threshold,'sha256':hashlib.sha256((R/('model_'+name+'.joblib')).read_bytes()).hexdigest(),'coef':dict(zip(m.named_steps['features'].get_feature_names_out(),m.named_steps['model'].coef_[0]))}
 for split in ['train','valid','test']:
  mask=f.split.eq(split).to_numpy();g=f[mask];p=score[mask];a=flags['fit_'+name][mask];n=g.original_net.to_numpy()
  mtr={'model':name,'split':split,'n':len(g),'alerts':int(a.sum()),'caught':int(g.loc[a,'y_union'].sum()),'total_risk':int(g.y_union.sum()),'auc':roc_auc_score(g.y_union,p),'ap':average_precision_score(g.y_union,p),'cancel_net_1w':float(-n[a].sum()*1e4),'risk_retained':float(g.loc[~a,'y_union'].mean()),'risk_base':float(g.y_union.mean()),'consecutive_caught':int(g.loc[a,'连续跌停'].sum())}
  mtr['lift']=(mtr['caught']/mtr['alerts'])/(mtr['total_risk']/mtr['n']) if mtr['alerts'] else 0
  metrics.append(mtr)
assert np.max(np.abs(scores['fit_D1']-scores['union_early_lr01']))<1e-10
va=[m for m in metrics if m['split']=='valid' and m['model']!='D1' and m['alerts']>=6 and m['alerts']<=.35*m['n'] and m['caught']>=3 and m['lift']>=1.25 and m['cancel_net_1w']>=0]
va.sort(key=lambda x:(x['risk_retained']-x['risk_base'],x['alerts'],x['model']))
chosen=va[0]['model'] if va else None
(R/'selection.json').write_text(json.dumps({'chosen':chosen,'eligible_validation':va,'note':'Flow-involving candidates are snapshot-only exploratory, not production-eligible irrespective of validation.'},ensure_ascii=False,indent=2))
(R/'model_info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2))
pd.DataFrame(metrics).to_csv(R/'六组模型_分期对照.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
for name in ['r_repeat','r_attention_burst','r_risk_accel','r_specific_denial','r_generic_denial','r_outflow','r_flow_deteriorate','r_news_flow']:
 flags[name]=f[name].eq(1).to_numpy()
base=flags['union_early_lr01']
for name in list(flags):
 if name.startswith('r_'):
  flags['OR_'+name]=base|flags[name];flags['AND_'+name]=base&flags[name]
pred=f[['id','symbol','d0_date','split','y_union','y_close','连续跌停','original_net']].copy()
for name,x in flags.items():pred[name]=x.astype(int)
for name,x in scores.items():pred[name+'_score']=x
pred.to_pickle(R/'predictions306.pkl');pred.to_csv(R/'306逐笔组合预测.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
results=[]
for split in ['train','valid','test']:
 g=f[f.split.eq(split)];ind=f.split.eq(split).to_numpy()
 for name,flag in flags.items():
  a=flag[ind];net=g.original_net.to_numpy()
  results.append({'split':split,'rule':name,'n':len(g),'alerts':int(a.sum()),'caught_union':int(g.loc[a,'y_union'].sum()),'caught_consecutive':int(g.loc[a,'连续跌停'].sum()),'forgone_1w':float(net[a & (net>0)].sum()*1e4),'avoided_1w':float(-net[a & (net<0)].sum()*1e4),'net_1w':float(-net[a].sum()*1e4),'risk_retained':float(g.loc[~a,'y_union'].mean()) if (~a).sum() else None})
pd.DataFrame(results).to_csv(R/'固定规则_分期得失.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
print(pd.DataFrame(metrics).query("split != 'train'").to_string(index=False));print('validation chosen',chosen)
print('baseline match exact; no D1 full-day flow columns in any model')

