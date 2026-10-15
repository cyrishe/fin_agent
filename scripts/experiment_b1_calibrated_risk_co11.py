"""CO11: calibrate independent CO10 risk scores, then apply fixed B1 policies."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from scripts.experiment_b1_market_trade_gate_co8 import read, sha, save_json, settle, net
from scripts.report_b1_market_trade_gate_co8 import account, metrics, drawdown, bootstrap_pair

OUT = Path('outputs/stock_automl/b1_calibrated_risk_co11')
REPORT = Path('docs/stock_automl_runs/20261015_b1_calibrated_risk_co11')
CO10 = Path('outputs/stock_automl/independent_open_drop_co10')
CO8 = Path('outputs/stock_automl/b1_market_trade_gate_co8')
ARMS = ['nonfall_hist', 'extreme_hist']
KEY = ['signal_date', 'symbol6']
BINS = [0, .05, .1, .2, .3, .4, .5, .75, 1.000001]
NAMES = {'baseline':'B1原成交额', 'conditional_p30':'子群概率≥30%轮空（主）',
    'conditional_p20':'子群概率≥20%轮空', 'conditional_p40':'子群概率≥40%轮空',
    'conditional_p50':'子群概率≥50%轮空', 'global_p30':'全市场概率≥30%轮空',
    'consensus_p30':'两模型均≥30%轮空', 'position_20_40':'20%半仓／40%轮空',
    'relative_top10':'校准人群风险最高10%轮空'}


def strong(d):
    return d.signal_return.between(.04, .09)


def logit(x):
    p = np.clip(np.asarray(x, float), 1e-6, 1-1e-6)
    return np.log(p/(1-p)).reshape(-1, 1)


def calibrate(d):
    d = d[d.label_known].copy()
    w = 1/d.groupby('signal_date').sample_id.transform('size')
    w = w / w.mean()
    prior = float(np.average(d.big_drop, weights=w))
    result = dict(rows=len(d), events=int(d.big_drop.sum()), prior=prior,
                  slope=0., intercept=float(logit([prior])[0, 0]), fallback=None)
    if min(d.big_drop.sum(), (~d.big_drop).sum()) < 30:
        result['fallback'] = 'insufficient_class_count'
    else:
        model = LogisticRegression(C=1., max_iter=1000, solver='lbfgs')
        model.fit(logit(d.risk_score), d.big_drop, sample_weight=w)
        if model.coef_[0, 0] < 0:
            result['fallback'] = 'negative_slope'
        else:
            result.update(slope=float(model.coef_[0, 0]), intercept=float(model.intercept_[0]))
    return result


def probability(model, scores):
    z = model['intercept'] + model['slope']*logit(scores).ravel()
    return 1/(1+np.exp(-np.clip(z, -700, 700)))


def freeze():
    if (REPORT/'pre_run_freeze.json').exists():
        raise RuntimeError('Already frozen')
    files = [Path(__file__), REPORT/'DESIGN.md', CO10/'dataset.csv.gz', CO10/'folds.json',
             CO8/'dataset.csv.gz', CO8/'valuation_paths.csv.gz']
    for f in json.loads((CO10/'folds.json').read_text()):
        if f['arm'] in ARMS:
            root = CO10/'models'/f['month']/f['arm']
            files.extend(root/n for n in ('validation.csv.gz', 'test.csv.gz', 'fold.json', 'model.joblib'))
    files += [Path('scripts/experiment_b1_market_trade_gate_co8.py'),
              Path('scripts/report_b1_market_trade_gate_co8.py')]
    save_json(REPORT/'pre_run_freeze.json', dict(time_utc=datetime.now(timezone.utc).isoformat(),
        files={str(p):sha(p) for p in files}, sklearn=sklearn.__version__, python=platform.python_version(),
        git_base=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        note='Working sources frozen by hashes; reused historical period, exploratory.'))


def verify_freeze():
    assert sklearn.__version__ == '1.7.1', sklearn.__version__
    for path, expected in json.loads((REPORT/'pre_run_freeze.json').read_text())['files'].items():
        assert sha(Path(path)) == expected, path


def reliability(d, pred, arm, phase, scope, month):
    d = d[d.label_known].copy()
    p = d[pred]; y = d.big_drop.astype(float)
    stats = dict(arm=arm, phase=phase, scope=scope, month=month, prediction=pred,
        rows=len(d), events=int(y.sum()), sum_probability=float(p.sum()),
        squared_error_sum=float(((p-y)**2).sum()),
        constant_prior_error_sum=float(((d.calibration_prior-y)**2).sum()))
    d['bin'] = pd.cut(p, BINS, right=False)
    bins = []
    for bin_, g in d.groupby('bin', observed=True):
        bins.append(dict(arm=arm, phase=phase, scope=scope, month=month, prediction=pred,
            bin=str(bin_), rows=len(g), events=int(g.big_drop.sum()),
            sum_probability=float(g[pred].sum())))
    return stats, bins


def run():
    verify_freeze()
    if (OUT/'b1_probabilities.csv.gz').exists():
        raise RuntimeError('Results already exist; use report to recompute accounting')
    cols = ['sample_id', *KEY, 'signal_return', 'label_known', 'big_drop', 'big_rise',
            'open_return', 'label_available_time']
    labels = read(CO10/'dataset.csv.gz', usecols=cols).set_index('sample_id')
    b1 = read(CO8/'dataset.csv.gz')
    folds = [f for f in json.loads((CO10/'folds.json').read_text()) if f['arm'] in ARMS]
    picks = []; checks = []; stats = []; bins = []
    for f in folds:
        root = CO10/'models'/f['month']/f['arm']
        dest = OUT/'calibration'/f['month']/f['arm']; dest.mkdir(parents=True, exist_ok=True)
        val = read(root/'validation.csv.gz').merge(labels.reset_index(), on=['sample_id', *KEY], validate='one_to_one')
        dates = f['validation_dates']; assert len(dates)==40
        cal = val[val.signal_date.isin(dates[:20])].copy()
        check = val[val.signal_date.isin(dates[20:])].copy()
        assert cal[cal.label_known].label_available_time.max() < dates[20]+' 14:40:00'
        assert max(f['train_dates']) < min(dates)
        assert max(dates) < min(f['test_dates'])
        cg = calibrate(cal); cs = calibrate(cal[strong(cal)])
        qg = float(cal.risk_score.quantile(.9)); qs = float(cal.loc[strong(cal), 'risk_score'].quantile(.9))
        info = dict(month=f['month'], arm=f['arm'], global_calibration=cg, strong_calibration=cs,
            global_q90=qg, strong_q90=qs, calibration_dates=dates[:20], check_dates=dates[20:],
            test_dates=f['test_dates'], base_model_sha256=f['model_sha256'],
            label_max=cal[cal.label_known].label_available_time.max())
        save_json(dest/'model.json', info)
        cal.to_csv(dest/'samples.csv.gz', index=False, float_format='%.17g')
        checks.append(info)
        test = read(root/'test.csv.gz').merge(labels.reset_index(), on=['sample_id', *KEY], validate='one_to_one')
        for phase, d in [('check', check), ('test', test)]:
            d['p_global'] = probability(cg, d.risk_score)
            d['p_conditional'] = np.where(strong(d), probability(cs, d.risk_score), d.p_global)
            d['q90'] = np.where(strong(d), qs, qg)
            d['calibration_prior'] = cg['prior']
            for scope, sub in [('market', d), ('strong4to9', d[strong(d)])]:
                for pred in ['risk_score', 'p_global', 'p_conditional']:
                    a, b = reliability(sub, pred, f['arm'], phase, scope, f['month'])
                    stats.append(a); bins.extend(b)
            selected = b1[KEY].merge(d, on=KEY, how='inner', validate='one_to_one')
            for pred in ['risk_score', 'p_global', 'p_conditional']:
                a, b = reliability(selected, pred, f['arm'], phase, 'B1_selected', f['month'])
                stats.append(a); bins.extend(b)
            selected['arm']=f['arm']; selected['phase']=phase; selected['month']=f['month']; picks.append(selected)
        print('calibrated', f['month'], f['arm'], 'rows', len(cal), 'strong', cs['rows'], flush=True)
    pd.concat(picks, ignore_index=True).to_csv(OUT/'b1_probabilities.csv.gz', index=False, float_format='%.17g')
    pd.DataFrame(stats).to_csv(REPORT/'probability_metrics_monthly.csv', index=False)
    pd.DataFrame(bins).to_csv(REPORT/'reliability_bins_monthly.csv', index=False)
    save_json(REPORT/'calibration_audit.json', checks)
    report()


def exposures(d):
    p = d.p_conditional; e = d.p_extreme
    output = {'baseline':np.ones(len(d))}
    for cutoff in (.2, .3, .4, .5):
        output['conditional_p'+str(round(cutoff*100))] = (~p.ge(cutoff)).astype(float).to_numpy()
    output['global_p30'] = (~d.p_global.ge(.3)).astype(float).to_numpy()
    output['consensus_p30'] = (~(p.ge(.3)&e.ge(.3))).astype(float).to_numpy()
    output['position_20_40'] = np.where(p.ge(.4), 0., np.where(p.ge(.2), .5, 1.))
    output['relative_top10'] = (~d.risk_score.ge(d.q90)).astype(float).to_numpy()
    return {k:v*d.baseline_trade.to_numpy() for k,v in output.items()}


def sized_account(d, marks, extra=0.):
    cap=100_000.; rows=[]
    for r in d.to_dict('records'):
        before=cap; path=[cap]; complete=True
        x=float(r['exposure']); r['trade']=bool(x>0)
        if x>0:
            gross=r['gross_1442']; assert np.isfinite(gross)
            budget=cap*x
            buy=budget/1.00026 if budget/1.00026*.00025>=5 else (budget-5)/1.00001
            cap=settle(cap,gross,x,extra)
            m=marks.get(r['signal_date'],pd.DataFrame())
            complete=bool(r['valuation_path_complete']) and not m.empty
            if complete:
                sale=buy*m.close.to_numpy()/r['entry_1442']
                path.extend((before-budget+sale-np.maximum(sale*.00025,5)-sale*.00051-buy*extra).tolist())
            path.append(cap)
        r.update(nav_before=before,nav_after=cap,account_return=cap/before-1,
                 intratrade_path=path,mark_complete=complete)
        rows.append(r)
    return pd.DataFrame(rows)


def extended_metrics(d):
    v=metrics(d); blocked=d[d.baseline_trade&~d.trade]
    v.update(mean_exposure_on_baseline=float(d.loc[d.baseline_trade,'exposure'].mean()),
        mean_account_return_on_trades_pct=float(d.loc[d.trade,'account_return'].mean()*100),
        opening_drops_avoided=int(blocked.big_drop.sum()), opening_winners_missed=int(blocked.big_rise.sum()),
        missing_probabilities_on_baseline=int((d.baseline_trade&d.p_conditional.isna()).sum()))
    return v


def random_positions(base, actual, repetitions=1000):
    rng=np.random.default_rng(111015); rows=[]
    monthids=[g.index.to_numpy() for _,g in base[base.baseline_trade].groupby('month')]
    for _ in range(repetitions):
        xs=actual.exposure.to_numpy().copy()
        for ids in monthids:xs[ids]=rng.permutation(xs[ids])
        cap=100_000.; path=[cap]
        for gross,x in zip(base.gross_1442,xs):
            if x>0:cap=settle(cap,gross,x)
            path.append(cap)
        rows.append(((cap/100_000-1)*100,drawdown(path)))
    return np.asarray(rows)


def report():
    b1=read(CO8/'dataset.csv.gz'); pred=read(OUT/'b1_probabilities.csv.gz')
    main=pred[pred.phase.eq('test')&pred.arm.eq('nonfall_hist')]
    other=pred[pred.phase.eq('test')&pred.arm.eq('extreme_hist')]
    dates=sorted({d for f in json.loads((CO10/'folds.json').read_text()) for d in f['test_dates']})
    base=b1[b1.signal_date.isin(dates)].merge(main[[*KEY,'p_global','p_conditional','risk_score','q90','big_drop','big_rise','open_return']],
        on=KEY,how='left',validate='one_to_one')
    base=base.merge(other[[*KEY,'p_conditional']].rename(columns={'p_conditional':'p_extreme'}),on=KEY,how='left',validate='one_to_one')
    base=base.sort_values('signal_date').reset_index(drop=True)
    base['baseline_trade']=base.selected&base.amount_gate.eq(1)
    base['net_main']=base.gross_1442.map(net);base['month']=base.signal_date.str[:7]
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    summaries=[]; monthly=[]; daily=[]; controls=[]; intervals=[]; sensitivity=[]; accounts={}
    for name,x in exposures(base).items():
        d=sized_account(base.assign(exposure=x),marks); accounts[name]=d
        daily.append(d.assign(model=name).drop(columns='intratrade_path'))
        for month,g in d.groupby('month'):
            monthly.append(dict(model=name,month=month,**extended_metrics(g)))
        for scope,mask in [('all',np.ones(len(d),bool)),('B1_Feb_Jul_2026',d.month.between('2026-02','2026-07')),
                           ('2025',d.month.str.startswith('2025')),('2026',d.month.str.startswith('2026'))]:
            sub=sized_account(d[mask],marks)
            summaries.append(dict(model=name,scope=scope,**extended_metrics(sub)))
        if name=='baseline':
            ref=account(base.assign(trade=base.baseline_trade),marks)
            assert np.max(np.abs(ref.nav_after-d.nav_after))<1e-9
            assert abs(metrics(d)['net_nav_pct']-(-43.37174055687569))<1e-9
        else:
            fraction=float(d.loc[d.baseline_trade,'exposure'].mean())
            constant=sized_account(base.assign(exposure=base.baseline_trade.astype(float)*fraction),marks)
            rnd=random_positions(base,d)
            controls.append(dict(model=name,constant_exposure=fraction,constant_nav_pct=metrics(constant)['net_nav_pct'],
                constant_dd_pct=metrics(constant)['settled_max_drawdown_pct'],random_median_nav_pct=float(np.median(rnd[:,0])),
                random_nav_low_pct=float(np.quantile(rnd[:,0],.025)),random_nav_high_pct=float(np.quantile(rnd[:,0],.975)),
                fraction_random_at_least_actual=float((rnd[:,0]>=metrics(d)['net_nav_pct']).mean())))
            for comparison,comp in [('baseline',accounts['baseline']),('constant_exposure',constant)]:
                v=bootstrap_pair(comp,d);v['model']=name;v['comparison']=comparison;intervals.append(v)
        s=sized_account(base.assign(exposure=x),marks,extra=.001)
        sensitivity.append(dict(model=name,extra_cost_bp=10,**extended_metrics(s)))
    pd.DataFrame(summaries).to_csv(REPORT/'summary.csv',index=False)
    pd.DataFrame(monthly).to_csv(REPORT/'monthly.csv',index=False)
    pd.concat(daily).to_csv(REPORT/'daily.csv.gz',index=False,float_format='%.17g')
    pd.DataFrame(controls).to_csv(REPORT/'exposure_random_controls.csv',index=False)
    pd.concat(intervals).to_csv(REPORT/'paired_block_intervals.csv',index=False)
    pd.DataFrame(sensitivity).to_csv(REPORT/'cost_sensitivity.csv',index=False)
    # Biggest avoided contribution is diagnostic only; never used to choose a policy.
    removed=[]; b=accounts['baseline']
    for name,d in accounts.items():
        if name=='baseline':continue
        delta=d.account_return-b.account_return; i=int(delta.idxmax()); day=d.loc[i,'signal_date']
        for policy,g in [('baseline',b),('overlay',d)]:
            a=sized_account(g[g.signal_date.ne(day)],marks)
            removed.append(dict(model=name,excluded_day=day,policy=policy,**extended_metrics(a)))
    pd.DataFrame(removed).to_csv(REPORT/'largest_contribution_exclusion.csv',index=False)
    known=base[base.selected&base.second_high_return.notna()].second_high_return
    save_json(REPORT/'top1_classification.json',dict(known=len(known),target_pct=float(known.ge(.03-1e-10).mean()*100),
        acceptable_pct=float((known.ge(.01-1e-10)&known.lt(.03-1e-10)).mean()*100),
        error_pct=float(known.lt(.01-1e-10).mean()*100),same_fixed_picks=True))
    print(pd.DataFrame(summaries).query("scope=='all'")[[ 'model','trades','net_nav_pct','settled_max_drawdown_pct','losses_avoided','profits_missed']].to_string(index=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['freeze','run','report'])
    globals()[parser.parse_args().phase]()
