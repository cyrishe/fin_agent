"""Predeclared second-layer version and training-row sensitivity checks."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits

from scripts.experiment_b1_market_trade_gate_co8 import OUT, REPORT, STOCK, MARKET, read, fit_model, predict, choose_gate, save_json
from scripts.report_b1_market_trade_gate_co8 import account, metrics


def run(mode):
    rows=[];fold_rows=[]
    folds=json.loads((OUT/'folds.json').read_text())
    for fold in folds:
        if fold['arm']!='combined':continue
        folder=OUT/'models'/fold['month']/'combined'
        train=read(folder/'training.csv.gz');val=read(folder/'validation.csv.gz');test=read(folder/'test.csv.gz')
        original_train=len(train)
        if mode=='drop2pct':
            keep=train.signal_date.map(lambda s:int(hashlib.sha256(('CO8:'+s).encode()).hexdigest()[:8],16)%100>=2)
            train=train[keep].copy()
        model=fit_model(train,MARKET+STOCK)
        v=val.copy();v['score']=predict(model,v);threshold,grid=choose_gate(v)
        score=predict(model,test)
        baseline=test.selected&test.amount_gate.eq(1)
        original=baseline&test.score.ge(test.threshold_overlay)
        changed=baseline&(score>=threshold)
        fold_rows.append({'month':fold['month'],'mode':mode,'sklearn':sklearn.__version__,
                          'training_rows':len(train),'rows_removed':original_train-len(train),
                          'original_threshold':fold['threshold_overlay'],'threshold':threshold,
                          'max_score_difference':float(np.max(np.abs(score-test.score))),
                          'changed_trade_decisions':int((original!=changed).sum())})
        test['original_trade']=original;test['trade']=changed;test['baseline_trade']=baseline
        test['score_sensitive']=score;test['threshold_sensitive']=threshold;test['month']=fold['month']
        rows.append(test)
    d=pd.concat(rows,ignore_index=True)
    marks={day:g for day,g in read(OUT/'valuation_paths.csv.gz').groupby('signal_date')}
    result=account(d,marks)
    result.drop(columns='intratrade_path').to_csv(OUT/(mode+'_daily.csv.gz'),index=False,float_format='%.17g')
    pd.DataFrame(fold_rows).to_csv(REPORT/(mode+'_sensitivity.csv'),index=False)
    save_json(REPORT/(mode+'_summary.json'),dict(mode=mode,sklearn=sklearn.__version__,
              total_training_memberships_removed=sum(f['rows_removed'] for f in fold_rows),
              changed_trade_decisions=int((d.trade!=d.original_trade).sum()),
              threshold_changed_months=sum(f['threshold']!=f['original_threshold'] for f in fold_rows),
              max_score_difference=max(f['max_score_difference'] for f in fold_rows),**metrics(result)))
    print(mode,sklearn.__version__,'changed decisions',int((d.trade!=d.original_trade).sum()),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['drop2pct','version191']);a=p.parse_args()
    with threadpool_limits(limits=1):run(a.mode)
