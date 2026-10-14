"""CO7: chronological B1 TopK out-of-fold risk learning, with mild vetoes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import warnings
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import roc_auc_score, brier_score_loss
from threadpoolctl import threadpool_limits
from scripts.build_automl_close_open_conditional_b1 import OUT, REPORT, SOURCE, FEATURES, FACTORS, KEY, sha
from scripts.experiment_automl_four_class_subset_selection import fit_tree
from scripts.run_automl_four_class_pipeline import infer, select_inference_candidates
from scripts.experiment_automl_close_open_simple_rolling import preprocess, transform
from scripts.report_automl_close_open_simple_rolling import attach_amounts
from scripts.analyze_automl_cost_robustness import net_return

PROBS=('p_lt0','p_0to1','p_1to3','p_ge3')
META=PROBS+('probability_margin','eligible_count','selection_rank')
CONTEXT=('day_max_drawdown','last5_return','last30_close_position','last30_range','day_range','relative_turnover20','day_vwap_distance','afternoon_return')
ARMS={'top1_core':(1,list(FEATURES)+list(META)),
      'top5_core':(5,list(FEATURES)+list(META)),
      'top5_context':(5,list(FEATURES)+list(META)+list(CONTEXT)),
      'top5_all76':(5,list(FACTORS)+list(META))}
THRESHOLDS=(1.01,.60,.50,.45,.40,.35)
C=.03


def read(path):return pd.read_csv(path,dtype={'symbol6':str},float_precision='round_trip')


def rank_topk(scored,k=5):
    d=scored[scored.predicted_class.isin(['ge3','1to3'])].copy()
    d['class_priority']=d.predicted_class.map({'ge3':0,'1to3':1})
    d['within_score']=np.where(d.predicted_class.eq('ge3'),d.p_ge3,d.p_ge3+d.p_1to3)
    d=d.sort_values(['signal_date','class_priority','within_score','symbol6'],ascending=[True,True,False,True],kind='stable')
    d['selection_rank']=d.groupby('signal_date').cumcount()+1
    return d[d.selection_rank.le(k)].copy()


def stage1():
    if sklearn.__version__!='1.7.1':raise ValueError('Original B1 requires 1.7.1')
    panel=read(OUT/'b1_candidates.csv.gz');panel['name']=''
    dates=read(OUT/'calendar.csv').date.tolist();folds=[];tops=[]
    for i,day in enumerate(dates[20:-1],20):
        folder=OUT/'stage1'/day;folder.mkdir(parents=True,exist_ok=True)
        if (folder/'complete.json').exists():
            folds.append(json.loads((folder/'complete.json').read_text()));tops.append(read(folder/'top5.csv'));continue
        days=dates[i-20:i]
        train=panel[panel.signal_date.isin(days)&panel.stage1_second_high.notna()&panel.next_date.le(day)].copy()
        assert train.signal_date.nunique()==20 and train.signal_date.max()<day
        train['second_high']=train.stage1_second_high
        test=panel[panel.signal_date.eq(day)]
        model=fit_tree(train,day)
        scored=infer(model,select_inference_candidates(test,day))
        ps=np.sort(scored[list(PROBS)].to_numpy(),axis=1)
        scored['probability_margin']=ps[:,-1]-ps[:,-2]
        scored['eligible_count']=scored.predicted_class.isin(['ge3','1to3']).sum()
        selected=rank_topk(scored)
        selected=selected[KEY+list(META)+['predicted_class']]
        scored.to_csv(folder/'scores.csv.gz',index=False,float_format='%.17g')
        selected.to_csv(folder/'top5.csv',index=False,float_format='%.17g')
        train[['sample_id']+KEY+['next_date']].to_csv(folder/'membership.csv.gz',index=False)
        joblib.dump(model,folder/'model.joblib',compress=3)
        meta={'day':day,'train_dates':days,'train_rows':len(train),'test_rows':len(test),
              'label_max':train.next_date.max(),'model_sha256':sha(folder/'model.joblib'),
              'scores_sha256':sha(folder/'scores.csv.gz'),'topk':len(selected)}
        (folder/'complete.json').write_text(json.dumps(meta,indent=2)+'\n')
        folds.append(meta);tops.append(selected)
        if i%10==0:print('stage1',i,len(dates)-1,day,len(train),flush=True)
    top=pd.concat(tops,ignore_index=True).merge(panel.drop(columns='name'),on=KEY,validate='one_to_one')
    top.to_csv(OUT/'oof_top5.csv.gz',index=False,float_format='%.17g')
    (OUT/'stage1_folds.json').write_text(json.dumps(folds,indent=2)+'\n')
    # B1 identity gate: actual archived Top2, never approximate same-pool picks.
    old=read(Path('docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/top2_verified.csv'))
    olddays=[f['test_date'] for f in json.loads(Path('docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20/folds.json').read_text())]
    a=top[top.signal_date.isin(olddays)&top.selection_rank.le(2)].sort_values(['signal_date','selection_rank']).reset_index(drop=True)
    b=old.sort_values(['signal_date','selection_rank']).reset_index(drop=True)
    assert a[KEY+['selection_rank','predicted_class']].equals(b[KEY+['selection_rank','predicted_class']])
    error=float(np.abs(a[list(PROBS)].to_numpy()-b[list(PROBS)].to_numpy()).max())
    assert error<1e-14
    audit={'frozen_B1_days':len(olddays),'top2_rows_matched':len(a),'top1_days_matched':int(a.selection_rank.eq(1).sum()),'maximum_probability_error':error,'stage1_days':len(folds),'oof_topk_rows':len(top)}
    (REPORT/'B1_identity.json').write_text(json.dumps(audit,indent=2)+'\n')
    print('STAGE1 COMPLETE',audit,flush=True)


def amounts():
    from scripts import build_automl_close_open_amounts as amount_builder
    dest=OUT/'amounts';dest.mkdir(exist_ok=True)
    old=Path('outputs/stock_automl/close_open_simple_rolling_r1/amounts')
    for p in old.glob('*.csv.gz'):
        if not (dest/p.name).exists():shutil.copy2(p,dest/p.name)
    amount_builder.OUT=dest
    available=pd.read_csv(SOURCE/'availability.csv').fillna({'local_file':''})
    tasks=[(r.date,r.local_file) for r in available.itertuples() if r.date<='2026-10-13' and not (dest/(r.date+'.csv.gz')).exists()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(amount_builder.one,tasks):print('amount',result,flush=True)


def fit_risk(train,factors):
    p=preprocess(train,factors);x=transform(train,p)
    y=train.net_return.lt(-.02).astype(int).to_numpy()
    w=1/train.groupby('signal_date').signal_date.transform('count').to_numpy();w*=len(w)/w.sum()
    if len(np.unique(y))<2:raise ValueError('Both outcomes required in actual past sample')
    m=LogisticRegression(C=C,solver='lbfgs',max_iter=1500,tol=1e-7)
    with warnings.catch_warnings():
        warnings.simplefilter('error',ConvergenceWarning);m.fit(x,y,sample_weight=w)
    return {'model':m,'preprocess':p,'weight':'equal_total_per_day','C':C}


def risk_probability(model,frame):return model['model'].predict_proba(transform(frame,model['preprocess']))[:,1]


def choose_threshold(validation):
    # Only already-mature, amount-eligible historical Top1 decisions determine
    # the gate. Every candidate includes a no-intervention alternative.
    v=validation[validation.selection_rank.eq(1)&validation.amount_gate.eq(1)&validation.net_return.notna()].copy()
    rows=[];crit=v.net_return.lt(-.02);win=v.net_return.gt(0)
    for threshold in THRESHOLDS:
        block=v.risk.gt(threshold)
        kept=len(v)-int(block.sum());wrong=int((block&win).sum())
        score=float(1.5*(block&crit).sum()-(block&win).sum()-.25*(block&~crit&~win).sum())
        valid=threshold>1 or (len(v)>=15 and kept>=.7*len(v) and wrong<=.2*win.sum())
        rows.append({'threshold':threshold,'trades':len(v),'kept':kept,'critical_blocked':int((block&crit).sum()),
                     'profitable_blocked':wrong,'score':score,'eligible':bool(valid)})
    best=max((r for r in rows if r['eligible']),key=lambda r:(r['score'],r['threshold']))
    return best['threshold'],rows


def diagnostics(frame):
    f=frame[frame.net_return.notna()]
    y=f.net_return.lt(-.02)
    return {'rows':len(f),'critical':int(y.sum()),'auc':float(roc_auc_score(y,f.risk)) if y.nunique()==2 else None,
            'brier':float(brier_score_loss(y,f.risk)) if len(f) else None,
            'mean_prediction':float(f.risk.mean()) if len(f) else None,'actual_rate':float(y.mean()) if len(f) else None}


def stage2():
    top=read(OUT/'oof_top5.csv.gz')
    dates=[r['day'] for r in json.loads((OUT/'stage1_folds.json').read_text())]
    calendar=read(OUT/'calendar.csv').date.tolist()
    am=pd.concat([read(p) for p in sorted((OUT/'amounts').glob('*.gz'))],ignore_index=True)
    top=attach_amounts(top,am,calendar)
    # Exact original gate information is preserved for the frozen B1 Top1.
    old=read(Path('outputs/stock_automl/close_open_simple_rolling_r1/daily_picks_with_gate.csv'))
    old=old[old.model.eq('B1_frozen_original171')&old.selected]
    source_gate=old.set_index(KEY)[['amount_gate','amount','prior20_amount_median','prior20_amount_days']]
    mask=top.selection_rank.eq(1)&top.signal_date.isin(old.signal_date)
    keys=pd.MultiIndex.from_frame(top.loc[mask,KEY])
    assert set(keys).issubset(source_gate.index)
    top.loc[mask,source_gate.columns]=source_gate.reindex(keys).to_numpy()
    top.to_csv(OUT/'oof_with_context.csv.gz',index=False,float_format='%.17g')
    all_predictions=[];threshold_records=[];diags=[];folds=[]
    months=sorted({d[:7] for d in dates})
    for month in months:
        testdays=[d for d in dates if d.startswith(month)];first=testdays[0]
        past=[d for d in dates if d<first][-100:]
        if len(past)<100:continue
        train_days,val_days=past[:60],past[60:]
        for arm,(k,factors) in ARMS.items():
            folder=OUT/'stage2'/month/arm;folder.mkdir(parents=True,exist_ok=True)
            if (folder/'complete.json').exists():
                all_predictions.append(read(folder/'test.csv.gz'));threshold_records+=json.loads((folder/'thresholds.json').read_text());diags+=json.loads((folder/'diagnostics.json').read_text());folds.append(json.loads((folder/'complete.json').read_text()));continue
            def matured(days,cutoff):
                return top[top.signal_date.isin(days)&top.next_date.le(cutoff)&top.net_return.notna()&top.selection_rank.le(k)].copy()
            train=matured(train_days,val_days[0]);val=matured(val_days,first);full=matured(past,first)
            test=top[top.signal_date.isin(testdays)&top.selection_rank.le(k)].copy()
            discovered=fit_risk(train,factors);val['risk']=risk_probability(discovered,val)
            threshold,grid=choose_threshold(val)
            fitted=fit_risk(full,factors);test['risk']=risk_probability(fitted,test)
            for phase,frame,model in [('discovery',train,discovered),('refit',full,fitted)]:
                joblib.dump(model,folder/f'{phase}.joblib',compress=3)
                member=frame[['sample_id']+KEY+['next_date','selection_rank']].copy()
                weight=1/frame.groupby('signal_date').signal_date.transform('count');member['weight']=weight*(len(weight)/weight.sum())
                member.to_csv(folder/f'{phase}_membership.csv.gz',index=False,float_format='%.17g')
            val.to_csv(folder/'validation.csv.gz',index=False,float_format='%.17g')
            test['arm']=arm;test['adaptive_threshold']=threshold
            test.to_csv(folder/'test.csv.gz',index=False,float_format='%.17g')
            grid=[dict(month=month,arm=arm,chosen=r['threshold']==threshold,**r) for r in grid]
            ds=[dict(month=month,arm=arm,scope=scope,rank_scope=which,**diagnostics(frame if which=='all_topk' else frame[frame.selection_rank.eq(1)])) for scope,frame in [('validation',val),('test',test)] for which in ('all_topk','top1')]
            meta={'month':month,'arm':arm,'test_dates':testdays,'train_dates':train_days,'validation_dates':val_days,
                  'train_rows':len(train),'validation_rows':len(val),'refit_rows':len(full),
                  'train_label_max':train.next_date.max(),'validation_label_max':val.next_date.max(),'refit_label_max':full.next_date.max(),
                  'factors':factors,'chosen_threshold':threshold,'discovery_sha256':sha(folder/'discovery.joblib'),'refit_sha256':sha(folder/'refit.joblib')}
            (folder/'thresholds.json').write_text(json.dumps(grid,indent=2)+'\n')
            (folder/'diagnostics.json').write_text(json.dumps(ds,indent=2)+'\n')
            (folder/'complete.json').write_text(json.dumps(meta,indent=2)+'\n')
            all_predictions.append(test);threshold_records+=grid;diags+=ds;folds.append(meta)
            print('stage2',month,arm,len(train),len(val),threshold,flush=True)
    pd.concat(all_predictions,ignore_index=True).to_csv(OUT/'stage2_predictions.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(threshold_records).to_csv(REPORT/'validation_thresholds.csv',index=False)
    pd.DataFrame(diags).to_csv(REPORT/'classification.csv',index=False)
    (OUT/'stage2_folds.json').write_text(json.dumps(folds,indent=2)+'\n')
    manifest={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'sklearn':sklearn.__version__,'script_sha256':sha(Path(__file__)),'design_sha256':sha(REPORT/'DESIGN.md'),
              'input_sha256':sha(OUT/'b1_candidates.csv.gz'),'C':C,'test_months':sorted({f['month'] for f in folds}),
              'stage2_bundles':2*len(folds),'probability_thresholds':list(THRESHOLDS)}
    (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('STAGE2 COMPLETE',manifest,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['stage1','amounts','stage2']);a=p.parse_args()
    with threadpool_limits(limits=1):globals()[a.phase]()
