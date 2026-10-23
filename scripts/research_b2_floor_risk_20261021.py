"""Chronological, frozen-choice risk screen on the 306 V2 trades.

Run fit first: it writes the protocol, training/validation evidence and selection.
Run test only after reviewing the frozen selection. No production changes.
"""
from __future__ import annotations
import argparse,hashlib,json,platform,subprocess
from itertools import combinations
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder,StandardScaler,FunctionTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier,export_text
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss
import sklearn

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'docs/stock_automl_runs/20261021_b2_floor_factor_review/workbook_data.json'
OUT=ROOT/'docs/stock_automl_runs/20261021_b2_floor_risk'
NUM={
 'total_mv_yi':'D0总市值亿','float_mv_yi':'D0流通市值亿','d0_turnover_pct':'D0换手率',
 'board_streak_days':'连板数','open_gap':'D1开盘涨幅','body5':'D1四K实体',
 'd0_normal_volume_ratio':'D0对板前量比','d0_amount_100m':'D0成交额亿',
 'streak_return':'本轮连板涨幅','board_turnover_sum':'板段累计换手',
 'market_limit_count':'市场涨停家数','market_median_return':'市场涨幅中位',
 'market_reseal_fraction':'市场连板延续率','close_sealed_minutes':'D0收在板上分钟数'}
CAT='opened_after_touch'
CORE=['total_mv_yi','d0_turnover_pct','board_streak_days','open_gap','body5',CAT]
SEED=20261021

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def clean(obj):return json.loads(pd.Series([obj]).to_json(orient='values',force_ascii=False,double_precision=15))[0]
def dump(name,obj):
    (OUT/name).write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def save_csv(name,frame):frame.to_csv(OUT/name,index=False,encoding='utf-8-sig')

def load():
    f=pd.DataFrame(json.loads(SOURCE.read_text())['rows']).sort_values('d0_date').reset_index(drop=True)
    f['y']=(f.open_floor.eq('是')|f.close_floor.eq('是')).astype(int)
    f['split']=np.select([f.d0_date.lt('2025-01-01'),f.d0_date.lt('2026-01-01')],['train','valid'],default='test')
    f.loc[(f.split=='train')&f['卖出日'].ge('2025-01-01'),'split']='purged'
    f.loc[(f.split=='valid')&f['卖出日'].ge('2026-01-01'),'split']='purged'
    assert len(f)==306 and f.y.sum()==67 and f.d0_date.is_unique
    assert f.observation_bars.eq(4).all() and f.entry_clock.ge('09:35').all()
    assert f[[x for x in CORE if x!=CAT]].notna().all().all()
    assert set(f[CAT])=={'是','否','分钟内顺序不明'}
    # Whitelist rather than selecting numerics from a table containing outcomes.
    metadata={x['key']:x for x in json.loads(SOURCE.read_text())['columns']}
    for key in [*NUM,CAT]:assert metadata[key]['known'] in ('D0收盘','D1前四K结束')
    assert f[f.split=='train']['卖出日'].max()<'2025-01-01'
    assert f[f.split=='valid']['卖出日'].max()<'2026-01-01'
    return f

def protocol():
    return dict(experiment='B2_FLOOR_RISK_20261021',source_sha256=sha(SOURCE),seed=SEED,
      target='D2 opening OR closing at historical lower price limit; not all intraday touches',
      partitions={'train':'D0 2022-05 through 2024-12; exclude exits >=2025-01-01',
                  'valid':'D0 2025; exclude exits >=2026-01-01','test':'D0 2026-01 through 2026-09; label exits through 2026-10-09'},
      numeric_features=NUM,categorical_feature=CAT,model_features=CORE,
      single_rules='Train Q25/Q50/Q75 thresholds, both directions; board counts floor threshold; three independent D0-open categories',
      shortlist='Train flags >=12 and <=40% of train, lift>1; rank excess positives, then precision; keep best five distinct factors',
      pairs='AND only, between shortlisted single rules; train >=12 and <=40%, positive lift',
      models={'logistic':{'C':1,'penalty':'l2','class_weight':None,'max_iter':2000},
              'tree':{'max_depth':2,'min_samples_leaf':20,'class_weight':None}},
      model_preprocessing='Training-only imputation, log1p total_mv and nonnegative turnover/board; standardized numeric; explicit three open-state categories',
      model_threshold='Training score Q75; flag strictly above threshold',
      selection='Validation flags >=6 and <=35%; >=3 true positives; precision/base>=1.25; maximize reduction of remaining risk rate, then fewer flags, lower complexity, id. None if no eligible rule.',
      refit='No refit on validation; frozen training models/thresholds tested as-is',
      tests='One held-out run of frozen candidates; winner remains validation-selected, never selected by test',
      uncertainty='Wilson risk intervals; 3000 month-cluster bootstrap draws on held-out predictions; diagnostic, no multiple-search significance claim',
      limitations=['Retrospective: V2 and this historical cohort already researched; not virgin prospective evidence.',
        'Cohort fixed by V2 monthly gate; no account replay, no claim of executable limit-down sales.',
        'Historical market cap dates can be revised; 96 earlier-share estimates, no future-dated shares.',
        'D1 full-day and all D2 columns excluded from X; original_net used only for after-the-fact opportunity-cost diagnostics.'])

def wilson(k,n):
    if not n:return [None,None]
    z=1.95996398454;p=k/n;d=1+z*z/n;mid=(p+z*z/(2*n))/d;half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [mid-half,mid+half]

def metrics(f,flag):
    flag=np.asarray(flag,bool);y=f.y.to_numpy();n=len(f);k=int(y.sum());m=int(flag.sum());tp=int(y[flag].sum());fp=m-tp
    risk=k/n;remain=(k-tp)/(n-m) if n>m else None
    # Returns below remain old price diagnostics, never account return or executable fills.
    return dict(n=n,positives=k,base_rate=risk,flagged=m,caught=tp,false_flags=fp,missed=k-tp,
      precision=tp/m if m else None,recall=tp/k if k else None,false_positive_rate=fp/(n-k),
      reject_fraction=m/n,lift=(tp/m)/risk if m and risk else None,
      retained_risk=remain,retained_risk_reduction=risk-remain if remain is not None else None,
      flagged_risk_ci=wilson(tp,m),retained_risk_ci=wilson(k-tp,n-m),
      flagged_original_profit_count=int(((f.original_net.to_numpy()>0)&flag).sum()),
      false_flag_original_profit_count=int(((f.original_net.to_numpy()>0)&flag&(y==0)).sum()),
      original_net_mean_flagged=float(f.loc[flag,'original_net'].mean()) if m else None,
      original_net_mean_retained=float(f.loc[~flag,'original_net'].mean()) if n>m else None)

def predict_rule(rule,f):
    if rule['kind']=='and':return predict_rule(rule['a'],f)&predict_rule(rule['b'],f)
    if rule['kind']=='model':
        model=joblib.load(OUT/rule['file']);return model.predict_proba(f[CORE])[:,1]>rule['threshold']
    if rule['op']=='==':return f[rule['feature']].eq(rule['value']).to_numpy()
    if rule['op']=='<=':return f[rule['feature']].le(rule['value']).to_numpy()
    return f[rule['feature']].gt(rule['value']).to_numpy()

def describe_rule(r):
    if r['kind']=='and':return describe_rule(r['a'])+' 且 '+describe_rule(r['b'])
    if r['kind']=='model':return r['label']+f"：风险分数>{r['threshold']:.4f}"
    if r['feature']==CAT:return 'D0触板后打开='+r['value']
    val=r['value'];k=r['feature'];is_pct=k in ('d0_turnover_pct','open_gap','body5','streak_return','board_turnover_sum','market_median_return','market_reseal_fraction')
    v=f'{val*100:.2f}%' if is_pct else f'{val:.4g}'
    return NUM[k]+r['op']+v

def model(name):
    pos=['total_mv_yi','d0_turnover_pct','board_streak_days'];signed=['open_gap','body5']
    p=Pipeline([('impute',SimpleImputer(strategy='median')),('log',FunctionTransformer(np.log1p,feature_names_out='one-to-one')),('scale',StandardScaler())])
    s=Pipeline([('impute',SimpleImputer(strategy='median')),('scale',StandardScaler())])
    t=ColumnTransformer([('positive',p,pos),('signed',s,signed),('open_state',OneHotEncoder(categories=[['否','是','分钟内顺序不明']],handle_unknown='ignore',sparse_output=False),[CAT])])
    estimator=LogisticRegression(C=1,max_iter=2000,random_state=SEED) if name=='logistic' else DecisionTreeClassifier(max_depth=2,min_samples_leaf=20,random_state=SEED)
    return Pipeline([('features',t),('estimator',estimator)])

def fit():
    OUT.mkdir(parents=True,exist_ok=True);p=protocol();pfile=OUT/'protocol.json'
    if pfile.exists():assert json.loads(pfile.read_text())==clean(p)
    else:dump('protocol.json',p)
    if (OUT/'test_results.json').exists():raise RuntimeError('Test already opened; do not refit or reselect this experiment.')
    f=load();tr=f[f.split=='train'];va=f[f.split=='valid']
    saved=['sequence','symbol','d0_date','d1_date','卖出日','split',*NUM,CAT,'mv_source','share_age','y']
    for part in ('train','valid','test','purged'):
        save_csv(f'{part}_samples.csv',f.loc[f.split==part,saved])
    candidates=[]
    for key in NUM:
        values=np.unique(tr[key].quantile([.25,.5,.75]).to_numpy())
        if key=='board_streak_days':values=np.unique(np.floor(values))
        for value in values:
            for op in ('<=','>'):candidates.append(dict(kind='single',feature=key,op=op,value=float(value)))
    for value in ['否','是','分钟内顺序不明']:candidates.append(dict(kind='single',feature=CAT,op='==',value=value))
    scored=[]
    for i,r in enumerate(candidates):
        r['id']=f'S{i+1:03}';m=metrics(tr,predict_rule(r,tr));scored.append(dict(rule=r,metrics=m))
    eligible=[x for x in scored if 12<=x['metrics']['flagged']<=len(tr)*.4 and x['metrics']['lift']>1]
    eligible.sort(key=lambda x:(-(x['metrics']['caught']-x['metrics']['base_rate']*x['metrics']['flagged']),-x['metrics']['precision'],x['rule']['id']))
    chosen=[];seen=set()
    for x in eligible:
        if x['rule']['feature'] not in seen:chosen.append(x['rule']);seen.add(x['rule']['feature'])
        if len(chosen)==5:break
    finalists=list(chosen)
    for a,b in combinations(chosen,2):
        r=dict(kind='and',a=a,b=b,id=f"A_{a['id']}_{b['id']}");m=metrics(tr,predict_rule(r,tr))
        if 12<=m['flagged']<=len(tr)*.4 and m['lift']>1:finalists.append(r)
    model_info={}
    for name,label in [('logistic','逻辑回归'),('tree','两层决策树')]:
        fitted=model(name);fitted.fit(tr[CORE],tr.y)
        scores=fitted.predict_proba(tr[CORE])[:,1];file=name+'.joblib';joblib.dump(fitted,OUT/file)
        r=dict(kind='model',id=name,label=label,file=file,threshold=float(np.quantile(scores,.75)))
        finalists.append(r);est=fitted.named_steps['estimator'];names=fitted.named_steps['features'].get_feature_names_out()
        model_info[name]=dict(file=file,sha256=sha(OUT/file),features=names.tolist(),parameters=est.get_params())
        if name=='tree':model_info[name]['tree']=export_text(est,feature_names=list(names))
        else:model_info[name]['coefficients']=dict(zip(names,est.coef_[0].tolist()))
    validation=[]
    for r in finalists:
        mt=metrics(tr,predict_rule(r,tr));mv=metrics(va,predict_rule(r,va))
        ok=mv['flagged']>=6 and mv['reject_fraction']<=.35 and mv['caught']>=3 and mv['lift']>=1.25
        validation.append(dict(id=r['id'],description=describe_rule(r),rule=r,train=mt,valid=mv,eligible=ok))
    choices=[x for x in validation if x['eligible']]
    choices.sort(key=lambda x:(-x['valid']['retained_risk_reduction'],x['valid']['flagged'],x['rule']['kind']!='single',x['id']))
    selection=dict(selected_id=choices[0]['id'] if choices else None,selected=choices[0] if choices else None,
                   candidates=validation,train_single_rules=scored,model_info=model_info,
                   partitions={p:dict(n=int((f.split==p).sum()),date_min=f.loc[f.split==p,'d0_date'].min(),date_max=f.loc[f.split==p,'d0_date'].max()) for p in ('train','valid','test','purged')},
                   protocol_sha256=sha(pfile),code_sha256=sha(__file__),source_sha256=sha(SOURCE))
    dump('selection_frozen.json',selection)
    dump('runtime.json',dict(python=platform.python_version(),sklearn=sklearn.__version__,numpy=np.__version__,pandas=pd.__version__,git_base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()))
    print(json.dumps(dict(partitions=selection['partitions'],shortlisted=len(validation),selection=selection['selected_id'],validation=[dict(id=x['id'],description=x['description'],train=x['train'],valid=x['valid'],eligible=x['eligible']) for x in validation]),ensure_ascii=False))

def cluster_ci(test,flag):
    rng=np.random.default_rng(SEED);months=test.d0_date.str[:7].unique();indices=[np.flatnonzero(test.d0_date.str[:7].eq(m).to_numpy()) for m in months];diff=[];lifts=[]
    y=test.y.to_numpy();flag=np.asarray(flag)
    for _ in range(3000):
        ix=np.concatenate([indices[i] for i in rng.integers(0,len(indices),len(indices))]);a=y[ix];b=flag[ix]
        if not b.any() or b.all() or a.sum()==0:continue
        diff.append(float(a.mean()-a[~b].mean()));lifts.append(float(a[b].mean()/a.mean()))
    return dict(months=len(months),valid_draws=len(diff),retained_risk_reduction_ci=np.quantile(diff,[.025,.975]).tolist() if diff else None,lift_ci=np.quantile(lifts,[.025,.975]).tolist() if lifts else None)

def test():
    frozen=OUT/'selection_frozen.json';sel=json.loads(frozen.read_text());selection_hash=sha(frozen)
    assert sha(SOURCE)==sel['source_sha256'] and sha(OUT/'protocol.json')==sel['protocol_sha256']
    assert sha(__file__)==sel['code_sha256'],'Protocol code changed after fit'
    for info in sel['model_info'].values():assert sha(OUT/info['file'])==info['sha256']
    f=load();te=f[f.split=='test'].copy();results=[];predictions=[]
    for item in sel['candidates']:
        rule=item['rule'];flag=predict_rule(rule,te);result=dict(id=item['id'],description=item['description'],selected=item['id']==sel['selected_id'],train=item['train'],valid=item['valid'],test=metrics(te,flag),uncertainty=cluster_ci(te,flag))
        if rule['kind']=='model':
            score=joblib.load(OUT/rule['file']).predict_proba(te[CORE])[:,1]
            result['test_score_metrics']=dict(auc=roc_auc_score(te.y,score),average_precision=average_precision_score(te.y,score),brier=brier_score_loss(te.y,score))
        results.append(result)
        for r,blocked in zip(te.to_dict('records'),flag):predictions.append(dict(id=item['id'],D0=r['d0_date'],股票=r['symbol'],risk=int(r['y']),flagged=int(blocked),open_floor=r['open_floor'],close_floor=r['close_floor'],original_net=r['original_net']))
    selected=next((r for r in results if r['selected']),None)
    # Descriptive differences are deliberately generated only after selection freezes.
    numeric=[];categorical=[];bins=[]
    tr=f[f.split=='train']
    for part in ('train','valid','test','all'):
        g=f if part=='all' else f[f.split==part]
        for key,label in NUM.items():
            a=g.loc[g.y==1,key].dropna();b=g.loc[g.y==0,key].dropna()
            auc=mannwhitneyu(a,b).statistic/(len(a)*len(b))
            numeric.append(dict(split=part,feature=key,label=label,n_risk=len(a),n_control=len(b),risk_median=a.median(),control_median=b.median(),risk_mean=a.mean(),control_mean=b.mean(),cliffs_delta=2*auc-1))
            edges=np.unique(tr[key].quantile([.25,.5,.75]).to_numpy());label_bins=np.searchsorted(edges,g[key].to_numpy(),side='left');label_bins[g[key].isna().to_numpy()]=-1
            for idx in range(len(edges)+1):
                h=g.iloc[np.flatnonzero(label_bins==idx)];n=len(h);k=int(h.y.sum())
                bins.append(dict(split=part,feature=key,label=label,lower=float(edges[idx-1]) if idx else None,upper=float(edges[idx]) if idx<len(edges) else None,n=n,risk=k,rate=k/n if n else None,ci=wilson(k,n)))
        for state,h in g.groupby(CAT):categorical.append(dict(split=part,state=state,n=len(h),risk=int(h.y.sum()),rate=h.y.mean(),ci=wilson(int(h.y.sum()),len(h))))
    sensitivity=[];monthly=[];subtargets=[]
    if sel['selected']:
        rule=sel['selected']['rule'];flag=predict_rule(rule,te)
        for name,mask in [('当日市值',te.mv_source.eq('D0当日历史值')),('估算市值',te.mv_source.ne('D0当日历史值')),('上半年',te.d0_date.lt('2026-07-01')),('第三季度',te.d0_date.ge('2026-07-01'))]:
            if mask.sum() and te.loc[mask,'y'].nunique()==2:sensitivity.append(dict(group=name,metrics=metrics(te[mask],flag[mask])))
        for month,h in te.groupby(te.d0_date.str[:7]):monthly.append(dict(month=month,metrics=metrics(h,predict_rule(rule,h))))
        for label,column in [('开盘跌停','open_floor'),('收盘跌停','close_floor'),('全天封死','all_locked')]:
            h=te.copy();h['y']=h[column].eq('是').astype(int);subtargets.append(dict(target=label,metrics=metrics(h,flag)))
    result=dict(selected=selected,candidates=results,selection_sha256=selection_hash,source_sha256=sha(SOURCE),
        split_counts={p:dict(n=len(g),risk=int(g.y.sum()),rate=g.y.mean()) for p,g in f.groupby('split')},
        numeric=numeric,categorical=categorical,bins=bins,sensitivity=sensitivity,monthly=monthly,subtargets=subtargets,
        selection_unchanged=sha(frozen)==selection_hash)
    dump('test_results.json',result);save_csv('test_predictions.csv',pd.DataFrame(predictions));save_csv('factor_differences.csv',pd.DataFrame(numeric));save_csv('factor_bins.csv',pd.DataFrame(bins));save_csv('d0_open_categories.csv',pd.DataFrame(categorical))
    print(json.dumps(clean(dict(selected=selected,split_counts=result['split_counts'],candidates=[{k:x[k] for k in ('id','description','test')} for x in results],sensitivity=sensitivity)),ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['fit','test']);args=parser.parse_args()
    {'fit':fit,'test':test}[args.stage]()
