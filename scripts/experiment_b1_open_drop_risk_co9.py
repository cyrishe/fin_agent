"""CO9: chronological, high-recall opening-loss gate on immutable B1 picks."""
from __future__ import annotations
import argparse
import json
import platform
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from threadpoolctl import threadpool_limits

from scripts.experiment_b1_market_trade_gate_co8 import OUT as SOURCE, MARKET, STOCK, read, sha, save_json, net
from scripts.report_b1_market_trade_gate_co8 import account, metrics, random_reference

OUT = Path('outputs/stock_automl/b1_open_drop_risk_co9')
REPORT = Path('docs/stock_automl_runs/20261015_b1_open_drop_risk_co9')
ADDED = ['prior3_return','prior5_vol','prior10_vol','prior5_downside','prior5_worst','prior5_up_fraction']
FEATURES = MARKET + STOCK + ADDED
ARMS = ['all_logistic','extreme_logistic','extreme_tree']
THRESHOLDS = list(np.linspace(0,1,21)) + [2.]


def labels(r):
    r=pd.Series(r)
    return r.le(-.02+1e-12),r.ge(.02-1e-12)


def history_features(daily,day,calendar):
    """Never access rows on or after the decision date."""
    h=daily[daily.trade_date.lt(day)].sort_values('trade_date').tail(10)
    i=calendar.index(day)
    result={c:np.nan for c in ADDED}
    for n in (3,5,10):
        a=h.tail(n)
        if len(a)!=n or a.trade_date.tolist()!=calendar[max(0,i-n):i]:continue
        r=(a.close/a.preclose-1).to_numpy(float)
        if not np.isfinite(r).all() or (a.preclose<=0).any():continue
        if n==3:result['prior3_return']=float(np.prod(1+r)-1)
        if n in (5,10):result[f'prior{n}_vol']=float(np.std(r))
        if n==5:result.update(prior5_downside=float(np.sqrt(np.mean(np.minimum(r,0)**2))),
                            prior5_worst=float(r.min()),prior5_up_fraction=float((r>0).mean()))
    return result


def prepare():
    OUT.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    d=read(SOURCE/'dataset.csv.gz')
    daily=read(SOURCE/'daily_input.csv.gz');daily['symbol6']=daily.stk_code.str[:6]
    calendar=sorted(daily.trade_date.unique())
    daily=daily[daily.symbol6.isin(d.symbol6.dropna())]
    groups={s:g for s,g in daily.groupby('symbol6')}
    rows=[];sources={};raw=[]
    for item in d.to_dict('records'):
        item.update({c:np.nan for c in ADDED});item['next_open']=np.nan;item['open_return']=np.nan
        if item['selected']:
            item.update(history_features(groups[item['symbol6']],item['signal_date'],calendar))
            p=SOURCE/'days'/item['next_date']/'paths.csv.gz'
            b=read(p);b=b[b.symbol6.eq(item['symbol6'])&b.clock.eq('09:31:00')&b.valid]
            if len(b)!=1 or not b.open.gt(0).all():raise ValueError('Unknown opening label: '+item['signal_date'])
            item['next_open']=float(b.open.iloc[0]);item['open_return']=item['next_open']/item['entry_1442']-1
            raw.append(dict(signal_date=item['signal_date'],next_date=item['next_date'],**b.iloc[0].to_dict()))
            sources[str(p)]=sha(p)
        rows.append(item)
    d=pd.DataFrame(rows);d['big_drop'],d['big_rise']=labels(d.open_return)
    d['net_main']=d.gross_1442.map(net);d['baseline_trade']=d.selected&d.amount_gate.eq(1)
    assert np.isfinite(d.loc[d.selected,'open_return']).all()
    d.to_csv(OUT/'dataset.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(raw).to_csv(OUT/'opening_label_rows.csv.gz',index=False,float_format='%.17g')
    shutil.copy2(SOURCE/'valuation_paths.csv.gz',OUT/'valuation_paths.csv.gz')
    save_json(OUT/'source_manifest.json',dict(
        sources={str(SOURCE/'dataset.csv.gz'):sha(SOURCE/'dataset.csv.gz'),
                 str(SOURCE/'daily_input.csv.gz'):sha(SOURCE/'daily_input.csv.gz'),**sources},
        fixed_pick_columns_identical=True,feature_latest_daily='T-1',opening_price_proxy='next 09:31 bar open'))
    print('Prepared',len(d),'days',int(d.selected.sum()),'fixed picks',flush=True)


def training_subset(train,arm):
    t=train[train.selected&train.open_return.notna()&~train.corporate_action.fillna(False)].copy()
    if arm.startswith('extreme'):t=t[t.big_drop|t.big_rise].copy()
    return t


def fit_model(t,arm):
    counts=t.big_drop.value_counts()
    if counts.get(True,0)<3 or counts.get(False,0)<3:return None
    x=t[FEATURES].replace([np.inf,-np.inf],np.nan)
    lo=x.quantile(.01);hi=x.quantile(.99);median=x.median().fillna(0.)
    x=x.clip(lo,hi,axis=1).fillna(median);mean=x.mean();std=x.std(ddof=0).replace(0,1).fillna(1.)
    if arm.endswith('tree'):
        m=DecisionTreeClassifier(max_depth=2,min_samples_leaf=5,class_weight={0:1,1:2},random_state=91015)
    else:m=LogisticRegression(C=.1,solver='lbfgs',max_iter=2000,class_weight={0:1,1:2})
    m.fit((x-mean)/std,t.big_drop.astype(int))
    return dict(model=m,lo=lo,hi=hi,median=median,mean=mean,std=std,features=FEATURES)


def score(m,d):
    if m is None:return np.zeros(len(d))
    x=d[m['features']].replace([np.inf,-np.inf],np.nan).clip(m['lo'],m['hi'],axis=1).fillna(m['median'])
    return m['model'].predict_proba((x-m['mean'])/m['std'])[:,1]


def choose_threshold(v,retention=.3,min_kept=5,available=True):
    v=v[v.baseline_trade&v.open_return.notna()&~v.corporate_action.fillna(False)].copy()
    n=len(v);events=int(v.big_drop.sum());grid=[]
    for threshold in THRESHOLDS:
        block=v.risk_score.ge(threshold)
        tp=int((block&v.big_drop).sum());kept=int((~block).sum())
        grid.append(dict(threshold=threshold,validation_n=n,validation_events=events,
                         caught=tp,recall=tp/events if events else 0.,kept=kept,
                         allowed=kept>=min_kept and kept>=retention*n))
    reason='selected'
    if not available:chosen=2.;reason='insufficient_training_classes'
    elif n<15 or events<3:chosen=2.;reason='insufficient_validation_events'
    else:
        valid=[r for r in grid if r['allowed']];target=[r for r in valid if r['recall']>=.9]
        if target:best=max(target,key=lambda r:(r['kept'],r['threshold']))
        else:best=max(valid,key=lambda r:(r['recall'],r['kept'],r['threshold']));reason='recall_target_unmet'
        chosen=best['threshold']
    for r in grid:r.update(chosen=r['threshold']==chosen,reason=reason)
    return chosen,grid,reason


def fit():
    frozen=json.loads((REPORT/'pre_run_freeze.json').read_text())
    for p,h in frozen['files'].items():assert sha(Path(p))==h,p
    d=read(OUT/'dataset.csv.gz');predictions=[];grids=[];folds=[];max_error=0.
    for month in sorted(d.signal_date.str[:7].unique()):
        test=d[d.signal_date.str.startswith(month)].copy();past=d[d.signal_date.lt(test.signal_date.min())].tail(100)
        if len(past)<100:continue
        train=past.iloc[:60];val=past.iloc[60:].copy()
        assert train.label_available_time.max()<val.decision_time.min()
        assert val.label_available_time.max()<test.decision_time.min()
        for arm in ARMS:
            t=training_subset(train,arm);m=fit_model(t,arm)
            v=val.copy();v['risk_score']=score(m,v);z=test.copy();z['risk_score']=score(m,z)
            folder=OUT/'models'/month/arm;folder.mkdir(parents=True,exist_ok=True)
            joblib.dump(m,folder/'model.joblib',compress=3)
            for phase,f in [('training_all',train),('training_used',t),('validation',v),('test',z)]:
                f.to_csv(folder/(phase+'.csv.gz'),index=False,float_format='%.17g')
            loaded=joblib.load(folder/'model.joblib')
            for phase in ('validation','test'):
                f=read(folder/(phase+'.csv.gz'))
                max_error=max(max_error,float(np.max(np.abs(score(loaded,f)-f.risk_score))))
            for mode,retention,min_kept in [('main',.3,5),('aggressive',.1,3)]:
                threshold,grid,reason=choose_threshold(v,retention,min_kept,m is not None)
                for r in grid:r.update(month=month,arm=arm,mode=mode)
                grids+=grid
                q=z.copy();q['model']=arm+'_'+mode;q['threshold']=threshold
                q['trade']=q.baseline_trade&q.risk_score.lt(threshold);q['month']=month
                q['reason']=reason;predictions.append(q)
                folds.append(dict(month=month,arm=arm,mode=mode,training_n=len(t),training_events=int(t.big_drop.sum()),
                    validation_n=int(v.baseline_trade.sum()),threshold=threshold,reason=reason,
                    training_label_max=train.label_available_time.max(),validation_start=val.decision_time.min(),
                    validation_label_max=val.label_available_time.max(),test_start=test.decision_time.min()))
            print(month,arm,'fit rows',len(t),'events',int(t.big_drop.sum()),flush=True)
    pd.concat(predictions,ignore_index=True).to_csv(OUT/'predictions.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(grids).to_csv(REPORT/'validation_thresholds.csv',index=False)
    pd.DataFrame(folds).to_csv(REPORT/'folds.csv',index=False)
    assert max_error<1e-12
    save_json(REPORT/'run_manifest.json',dict(python=platform.python_version(),sklearn=sklearn.__version__,
        numpy=np.__version__,scipy=scipy.__version__,pandas=pd.__version__,joblib=joblib.__version__,
        commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_in_working_tree=True,
        max_reloaded_score_error=max_error,model_count=len(folds)//2,features=FEATURES,
        frozen_files=frozen['files']))


def risk_metrics(d):
    b=d[d.baseline_trade];blocked=~b.trade;event=b.big_drop.astype(bool)
    tp=int((blocked&event).sum());total=int(event.sum());flagged=int(blocked.sum());neg=int((~event).sum())
    kept=int(b.trade.sum())
    return dict(open_drop_events=total,open_drops_caught=tp,open_drops_missed=total-tp,
                open_drop_recall_pct=100*tp/total if total else np.nan,
                block_precision_pct=100*tp/flagged if flagged else np.nan,
                false_positive_pct=100*int((blocked&~event).sum())/neg if neg else np.nan,
                retained_drop_rate_pct=100*(total-tp)/kept if kept else np.nan,
                open_big_winners_blocked=int((blocked&b.big_rise).sum()),
                net_big_winners_blocked=int((blocked&b.net_main.ge(.02)).sum()))


def bootstrap(base,primary):
    rows=[];rng=np.random.default_rng(91015);n=len(base)
    delta=(primary.account_return-base.account_return).to_numpy()*100
    event=(base.baseline_trade&base.big_drop).to_numpy();caught=event&~primary.trade.to_numpy()
    for block in (5,10):
        means=[];recalls=[]
        for _ in range(2000):
            starts=rng.integers(0,n,size=int(np.ceil(n/block)))
            ids=np.concatenate([(s+np.arange(block))%n for s in starts])[:n]
            means.append(delta[ids].mean())
            if event[ids].sum():recalls.append(caught[ids].sum()/event[ids].sum()*100)
        rows.append(dict(block_days=block,replicates=2000,daily_delta_pp=float(delta.mean()),
                         daily_delta_ci95=np.quantile(means,[.025,.975]).tolist(),
                         recall_ci95=np.quantile(recalls,[.025,.975]).tolist()))
    return rows


def report():
    pred=read(OUT/'predictions.csv.gz');marks={d:g for d,g in read(OUT/'valuation_paths.csv.gz').groupby('signal_date')}
    baseline=pred[pred.model.eq('extreme_logistic_main')].copy();baseline['trade']=baseline.baseline_trade
    baseline['model']='amount_baseline'
    allpred=pd.concat([baseline,pred],ignore_index=True);summaries=[];monthly=[];daily=[];accounts={}
    for name,g in allpred.groupby('model',sort=False):
        g=g.sort_values('signal_date').reset_index(drop=True);a=account(g,marks);accounts[name]=a
        daily.append(a.drop(columns='intratrade_path'))
        for month,x in a.groupby('month'):monthly.append(dict(model=name,month=month,**metrics(x),**risk_metrics(x)))
        for scope,x in [('all',a),('B1_Feb_Jul_2026',a[a.month.between('2026-02','2026-07')])]:
            x=account(x,marks);summaries.append(dict(model=name,scope=scope,**metrics(x),**risk_metrics(x)))
    s=pd.DataFrame(summaries);mo=pd.DataFrame(monthly)
    s.to_csv(REPORT/'summary.csv',index=False);mo.to_csv(REPORT/'monthly.csv',index=False)
    pd.concat(daily,ignore_index=True).to_csv(REPORT/'daily.csv.gz',index=False,float_format='%.17g')
    b=accounts['amount_baseline'];p=accounts['extreme_logistic_main']
    rand=random_reference(b,p);rand.to_csv(OUT/'random_same_month.csv.gz',index=False)
    exposure=p.trade.sum()/b.trade.sum();low=account(b,marks,exposure=exposure)
    diagnostic=dict(exposure_fraction=float(exposure),constant_exposure=metrics(low),
        random_median_nav_pct=float(rand.net_nav_pct.median()),
        random_fraction_at_least_primary=float(rand.net_nav_pct.ge(metrics(p)['net_nav_pct']).mean()),
        bootstrap=bootstrap(b,p))
    save_json(REPORT/'controls.json',diagnostic)
    bt=b[b.baseline_trade].copy()
    bt['opening_group']=np.select([bt.big_drop,bt.big_rise],['drop_at_least_2pct','rise_at_least_2pct'],default='middle')
    bt.groupby('opening_group').agg(n=('signal_date','size'),mean_open=('open_return','mean'),
        mean_net0940=('net_main','mean'),positive0940=('net_main',lambda x:int(x.gt(0).sum()))).to_csv(REPORT/'opening_vs_0940.csv')
    folds=read(REPORT/'folds.csv')
    lines=['# CO9：两极样本次晨大跌风险识别','',
           '主标签：次晨09:31分钟open相对前日14:42买入代理下跌至少2%；策略仍固定次晨09:40退出。B1选股及原成交额规则不变。测试为2025-08至2026-10-13的288日，包含扩展回放，历史已经反复研究。','',
           '主方案为两极Logistic、验证保留至少30%交易；激进方案保留至少10%。加权及两极拟合输出是风险分数，不是真实概率。所有验证和测试均保留中间样本。','',
           '|方案|净收益%|结算回撤%|笔数|大跌拦截/总数|召回%|拦截精度%|避亏/误拦盈利|',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in s[s.scope.eq('all')].itertuples():
        lines.append(f'|{r.model}|{r.net_nav_pct:.2f}|{r.settled_max_drawdown_pct:.2f}|{r.trades}|{r.open_drops_caught}/{r.open_drop_events}|{r.open_drop_recall_pct:.1f}|{r.block_precision_pct:.1f}|{r.losses_avoided}/{r.profits_missed}|')
    lines+=['','## 主方案逐月','',
            '|月份|原净收益%|新净收益%|原/新回撤%|原/新笔数|拦截大跌/总数|避亏/误拦盈利|',
            '|---|---:|---:|---:|---:|---:|---:|']
    for r in mo[mo.model.eq('extreme_logistic_main')].itertuples():
        old=mo[mo.model.eq('amount_baseline')&mo.month.eq(r.month)].iloc[0]
        lines.append(f'|{r.month}|{old.net_nav_pct:.2f}|{r.net_nav_pct:.2f}|{old.settled_max_drawdown_pct:.2f}/{r.settled_max_drawdown_pct:.2f}|{old.trades}/{r.trades}|{r.open_drops_caught}/{r.open_drop_events}|{r.losses_avoided}/{r.profits_missed}|')
    lines+=['','## 样本和验证','',
            f'每折前60日训练、后40日验证、下一月测试；不重拟合。主方案两极训练样本数范围{folds[folds.arm.eq("extreme_logistic")].training_n.min()}–{folds[folds.arm.eq("extreme_logistic")].training_n.max()}。',
            '所有标签成熟边界及模型回读已检查，具体见run_manifest.json；特征来自CO8时点审计和仅T−1以前日线。最终态档案不能证明历史实时送达，首分钟open不等于已验证集合竞价成交价。费用和成交代理沿用CO8。',
            '逐月阈值及证据不足回退：folds.csv；全部验证网格：validation_thresholds.csv；原B1盈利区间与所有变体：summary.csv；逐日决定：daily.csv.gz。',
            '同月等笔数随机轮空及等资金暴露对照：controls.json；开盘风险与实际09:40收益的差异：opening_vs_0940.csv。本轮未修改生产配置。','']
    (REPORT/'README.md').write_text('\n'.join(lines))
    print(s[s.scope.eq('all')][['model','net_nav_pct','settled_max_drawdown_pct','trades','open_drops_caught','open_drop_events','open_drop_recall_pct','profits_missed']].to_string(index=False),flush=True)


def freeze():
    files=[REPORT/'DESIGN.md',OUT/'dataset.csv.gz',Path(__file__),
           Path('scripts/experiment_b1_market_trade_gate_co8.py'),Path('scripts/report_b1_market_trade_gate_co8.py')]
    p=REPORT/'pre_run_freeze.json'
    if p.exists():raise RuntimeError('Do not replace pre-run freeze')
    save_json(p,dict(created_utc=datetime.now(timezone.utc).isoformat(),files={str(p):sha(p) for p in files}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','freeze','fit','report']);args=parser.parse_args()
    with threadpool_limits(limits=1):globals()[args.phase]()
