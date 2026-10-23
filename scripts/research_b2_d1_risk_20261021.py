"""Frozen train/validation selection for early and D1-close floor warnings."""
import argparse,json,platform,subprocess,copy
import joblib,numpy as np,pandas as pd,sklearn
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler,OneHotEncoder,FunctionTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier,export_text
from sklearn.metrics import roc_auc_score,average_precision_score
from scripts import research_b2_floor_risk_20261021 as old
from scripts.build_b2_d1_risk_20261021 import ROOT,OUT,D0,EARLY,LATE,write

SEED=20261021
GROUPS={'baseline':old.CORE,'early':list(dict.fromkeys([*D0,old.CAT,*EARLY])),
        'close':list(dict.fromkeys([*D0,old.CAT,*EARLY,*LATE]))}

def safe(obj):
    if isinstance(obj,dict):return {k:safe(v) for k,v in obj.items()}
    if isinstance(obj,(tuple,list)):return [safe(v) for v in obj]
    if isinstance(obj,np.ndarray):return safe(obj.tolist())
    if isinstance(obj,(np.integer,np.bool_)):return obj.item()
    if isinstance(obj,(float,np.floating)):return float(obj) if np.isfinite(obj) else None
    return obj

def save(n,obj):write(n,safe(obj))
def load():return pd.read_pickle(OUT/'panel.pkl')
def outcome(f,target):
    h=f.copy();h['y']=h['y_'+target];return h

def freeze_protocol():
    p=dict(experiment='B2_D1_FLOOR_RISK_20261021',primary_target='D2 close floor',secondary_target='D2 open OR close floor',
      groups=GROUPS,partitions='Same as previous study: train 2022-2024 152; validation 2025 54; test 2026 98; 2 cross-year labels purged.',
      causal_times={'early':'D1 09:34, before earliest buy 09:35','close':'D1 close, after purchase; only next-day position risk'},
      candidate_models='Baseline core logistic C1; each extended group logistic C0.1, C1, tree depth2 min_leaf20. No class weighting.',
      preprocessing='Training-only 2%/98% winsorization, median imputation, standardization. Fixed three-category open-state onehot.',
      model_threshold='Strict > training score Q75; lossless binary64 JSON. No validation refit.',
      single_rules='Per extended group, numerical train Q25/Q50/Q75 both directions; flags>=12 and<=40%, lift>1; top3 distinct factors ranked excess positives, precision, ID.',
      selection='Select separately for each target and decision time: validation flags>=6 and<=35%, caught>=3, lift>=1.25. Maximize remaining-risk reduction, then fewer flags, prefer single, ID. None if none eligible.',
      test='Freeze all choices before opening test output. Report all candidates, and primary D2-close vs secondary union separately. Do not change selection after test.',
      limitations=['2026 outcomes were viewed in previous study; retrospective follow-up holdout, not fresh blind test.',
        'Only frozen V2 executed trades; D1-close cannot prevent D1 buys or imply same-day sale.',
        'No sellability/account replay; original_net is hypothetical original-exit-price opportunity cost.',
        'Original market cap estimates/publication-time caveats remain.'],
      bootstrap='3000 held-out month-cluster draws; uncertainty diagnostic not multiple-testing corrected',seed=SEED)
    dest=OUT/'protocol.json'
    if dest.exists():assert json.loads(dest.read_text())==p
    else:write('protocol.json',p)
    print('protocol frozen',old.sha(dest))

def pipeline(kind,cols,tr):
    numeric=[c for c in cols if c!=old.CAT]
    lower=tr[numeric].quantile(.02).to_numpy();upper=tr[numeric].quantile(.98).to_numpy()
    transform=ColumnTransformer([
      ('numeric',Pipeline([('winsor',FunctionTransformer(np.clip,kw_args={'a_min':lower,'a_max':upper},feature_names_out='one-to-one')),
                           ('impute',SimpleImputer(strategy='median',keep_empty_features=True)),('scale',StandardScaler())]),numeric),
      ('category',OneHotEncoder(categories=[['否','是','分钟内顺序不明']],handle_unknown='ignore',sparse_output=False),[old.CAT])])
    model=DecisionTreeClassifier(max_depth=2,min_samples_leaf=20,random_state=SEED) if kind=='tree' else LogisticRegression(C=.1 if kind=='lr01' else 1,max_iter=5000,random_state=SEED)
    return Pipeline([('features',transform),('model',model)])

def predict(rule,f):
    if rule['kind']=='model':return joblib.load(OUT/rule['file']).predict_proba(f[rule['columns']])[:,1]>rule['threshold']
    if rule['op']=='<=':return f[rule['feature']].le(rule['value']).to_numpy()
    return f[rule['feature']].gt(rule['value']).to_numpy()

def eligible(m):return m['flagged']>=6 and m['reject_fraction']<=.35 and m['caught']>=3 and m['lift']>=1.25
def choose(items):
    good=[x for x in items if eligible(x['valid'])]
    good.sort(key=lambda x:(-x['valid']['retained_risk_reduction'],x['valid']['flagged'],x['rule']['kind']!='single',x['id']))
    return good[0]['id'] if good else None

def fit():
    if (OUT/'test_results.json').exists():raise RuntimeError('Test already read; no refit on this experiment.')
    freeze_protocol();f=load();candidates=[];all_rules=[];choice={};model_info={}
    assert f.split.value_counts().to_dict()=={'train':152,'test':98,'valid':54,'purged':2}
    for target in ['close','union']:
        g=outcome(f,target);tr=g[g.split=='train'];va=g[g.split=='valid']
        for stage,cols in GROUPS.items():
            rules=[]
            for kind in (['lr1'] if stage=='baseline' else ['lr01','lr1','tree']):
                name=f'{target}_{stage}_{kind}';m=pipeline(kind,cols,tr);m.fit(tr[cols],tr.y)
                joblib.dump(m,OUT/(name+'.joblib'))
                score=m.predict_proba(tr[cols])[:,1]
                rule=dict(id=name,kind='model',file=name+'.joblib',columns=cols,threshold=float(np.quantile(score,.75)))
                rules.append(rule);names=m.named_steps['features'].get_feature_names_out().tolist();est=m.named_steps['model']
                info=dict(sha256=old.sha(OUT/rule['file']),features=names,parameters=est.get_params())
                if kind=='tree':info['tree']=export_text(est,feature_names=names)
                else:info['coefficients']=dict(zip(names,est.coef_[0].tolist()));info['intercept']=float(est.intercept_[0])
                model_info[name]=info
            if stage!='baseline':
                explored=[]
                for key in cols:
                    if key==old.CAT:continue
                    for i,value in enumerate(np.unique(tr[key].quantile([.25,.5,.75]).dropna().to_numpy())):
                        for op in ['<=','>']:
                            rule=dict(id=f'{target}_{stage}_{key}_q{i}_{op}',kind='single',feature=key,op=op,value=float(value))
                            mt=old.metrics(tr,predict(rule,tr));explored.append(dict(rule=rule,train=mt))
                all_rules.extend(explored)
                good=[x for x in explored if 12<=x['train']['flagged']<=len(tr)*.4 and x['train']['lift']>1]
                good.sort(key=lambda x:(-(x['train']['caught']-x['train']['base_rate']*x['train']['flagged']),-x['train']['precision'],x['rule']['id']))
                seen=set()
                for x in good:
                    if x['rule']['feature'] not in seen:rules.append(x['rule']);seen.add(x['rule']['feature'])
                    if len(seen)==3:break
            items=[]
            for rule in rules:
                item=dict(id=rule['id'],target=target,stage=stage,rule=rule,
                          train=old.metrics(tr,predict(rule,tr)),valid=old.metrics(va,predict(rule,va)))
                item['eligible']=eligible(item['valid']);items.append(item)
            candidates.extend(items)
            if stage!='baseline':choice[f'{target}_{stage}']=choose(items)
    save('selection_frozen.json',dict(choices=choice,candidates=candidates,all_training_rules=all_rules,models=model_info,
        panel_sha256=old.sha(OUT/'panel.pkl'),protocol_sha256=old.sha(OUT/'protocol.json'),code_sha256=old.sha(__file__),
        builder_sha256=old.sha(ROOT/'scripts/build_b2_d1_risk_20261021.py')))
    save('runtime.json',dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__,sklearn=sklearn.__version__,git_base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()))
    print(json.dumps(safe(dict(choices=choice,validation=[dict(id=x['id'],eligible=x['eligible'],flag=x['valid']['flagged'],catch=x['valid']['caught'],n=x['valid']['n'],positives=x['valid']['positives']) for x in candidates])),ensure_ascii=False))

def test():
    sel=json.loads((OUT/'selection_frozen.json').read_text());f=load();preds=[];results=[]
    assert old.sha(OUT/'panel.pkl')==sel['panel_sha256'] and old.sha(__file__)==sel['code_sha256']
    assert old.sha(OUT/'protocol.json')==sel['protocol_sha256']
    for name,info in sel['models'].items():assert old.sha(OUT/(name+'.joblib'))==info['sha256']
    for item in sel['candidates']:
        g=outcome(f,item['target']);te=g[g.split=='test'];rule=item['rule'];flag=predict(rule,te)
        record={k:item[k] for k in ['id','target','stage','rule','train','valid']}
        record.update(test=old.metrics(te,flag),uncertainty=old.cluster_ci(te,flag),selected=sel['choices'].get(item['target']+'_'+item['stage'])==item['id'])
        if rule['kind']=='model':
            score=joblib.load(OUT/rule['file']).predict_proba(te[rule['columns']])[:,1]
            record['auc']=roc_auc_score(te.y,score);record['ap']=average_precision_score(te.y,score)
        else:score=np.full(len(te),np.nan)
        results.append(record)
        for x,mark,s in zip(te.to_dict('records'),flag,score):preds.append(dict(candidate=item['id'],stage=item['stage'],target=item['target'],sequence=x['sequence'],symbol=x['symbol'],D0=x['d0_date'],risk=x['y'],flagged=int(mark),score=s,original_net=x['original_net']))
    # Post-selection factor descriptions with fixed training bins and all periods.
    numeric=[];bins=[]
    for target in ['close','union']:
        g=outcome(f,target);tr=g[g.split=='train']
        for part in ['train','valid','test','all']:
            h=g if part=='all' else g[g.split==part]
            for key in [*D0,*EARLY,*LATE]:
                a=h.loc[h.y==1,key].dropna();b=h.loc[h.y==0,key].dropna()
                numeric.append(dict(target=target,split=part,feature=key,risk_n=len(a),control_n=len(b),risk_median=a.median(),control_median=b.median()))
                edges=np.unique(tr[key].quantile([.25,.5,.75]).dropna().to_numpy())
                assigned=np.searchsorted(edges,h[key],side='left');assigned[h[key].isna()]=-1
                for i in range(len(edges)+1):
                    subset=h.iloc[np.flatnonzero(assigned==i)]
                    bins.append(dict(target=target,split=part,feature=key,lower=edges[i-1] if i else None,upper=edges[i] if i<len(edges) else None,n=len(subset),risk=int(subset.y.sum()),rate=subset.y.mean()))
    save('test_results.json',dict(selection_sha256=old.sha(OUT/'selection_frozen.json'),choices=sel['choices'],candidates=results,
         counts={target:{p:dict(n=len(h),risk=int(h['y_'+target].sum())) for p,h in f.groupby('split')} for target in ['close','union']}))
    for name,items in [('test_predictions',preds),('factor_differences',numeric),('factor_bins',bins)]:pd.DataFrame(items).to_csv(OUT/(name+'.csv'),index=False,encoding='utf-8-sig')
    print(json.dumps(safe([x for x in results if x['selected'] or x['stage']=='baseline']),ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['protocol','fit','test']);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    {'protocol':freeze_protocol,'fit':fit,'test':test}[args.stage]()
