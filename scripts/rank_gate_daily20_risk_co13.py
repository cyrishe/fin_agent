"""User addendum: veto only when the fixed B1 pick is among market-wide highest risk K."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.experiment_daily20_risk_co13 import OUT,REPORT,CO12,REPORT12,CO8,KEY,read,sha,save_json,check_freeze
from scripts.report_independent_0940_netloss_co12 import policy_metrics
from scripts.experiment_b1_calibrated_risk_co11 import sized_account
from scripts.report_b1_market_trade_gate_co8 import bootstrap_pair

KS=(100,250,500)


def risk_ranks(scores):
    d=scores.copy()
    if d.duplicated(KEY).any():raise ValueError('Duplicate stock-day risk')
    if not np.isfinite(d.p_severe).all():raise ValueError('Missing/nonfinite market risk')
    # Retain a tied boundary group unless every member fits within K.
    # This prevents arbitrary stock-code tie-breaking from deciding trades.
    d['risk_rank_max']=d.groupby('signal_date').p_severe.rank(method='max',ascending=False)
    d['risk_pool_size']=d.groupby('signal_date').p_severe.transform('size')
    return d


def freeze():
    path=REPORT/'rank_gate_pre_run_freeze.json'
    if path.exists():raise RuntimeError('Rank gate already frozen')
    files=[Path('scripts/rank_gate_daily20_risk_co13.py'),Path('tests/automl/test_rank_gate_daily20_risk_co13.py'),REPORT/'RANK_GATE_DESIGN.md',
           REPORT/'pre_run_freeze.json']+sorted((CO12/'models').glob('*/test.csv.gz'))
    save_json(path,dict(created_utc=datetime.now(timezone.utc).isoformat(),files={str(p):sha(p) for p in files},
        K=list(KS),ranking_score='p_severe',universe='all same-day feature-available market stocks, regardless of future label',
        boundary_ties='rank(method=max) <= K',extra_training=False,
        user_requested_while_daily_training_running=True,exploratory_no_new_primary_selected=True))


def run():
    check_freeze()
    for p,h in json.loads((REPORT/'rank_gate_pre_run_freeze.json').read_text())['files'].items():assert sha(Path(p))==h
    source={'60d':pd.concat([read(p) for p in sorted((CO12/'models').glob('*/test.csv.gz'))],ignore_index=True),
            '20d':read(OUT/'predictions.csv.gz')}
    assert set(source['60d'].sample_id)==set(source['20d'].sample_id)
    old=read(REPORT12/'daily.csv.gz');baseline=old[old.model.eq('baseline')].copy()
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    summary=[];monthly=[];daily=[];cases=[];rankstats=[];accounts={};intervals=[]
    for window,scores in source.items():
        ranked=risk_ranks(scores)
        for day,g in ranked.groupby('signal_date'):
            rankstats.append(dict(window=window,signal_date=day,pool=len(g),**{f'top{k}_effective':int(g.risk_rank_max.le(k).sum()) for k in KS}))
        cols=[*KEY,'p_severe','conditional_severity','tail_expected_loss','risk_rank_max','risk_pool_size']
        base=baseline.drop(columns=[c for c in cols if c not in KEY and c in baseline]).merge(ranked[cols],on=KEY,how='left',validate='one_to_one').sort_values('signal_date').reset_index(drop=True)
        assert base.loc[base.baseline_trade,'risk_rank_max'].notna().all()
        for k in KS:
            name=f'{window}_rank_top{k}';blocked=base.baseline_trade&base.risk_rank_max.le(k)
            a=sized_account(base.assign(exposure=(base.baseline_trade&~blocked).astype(float)),marks);accounts[name]=a
            for scope,g in [('all',a),('B1_Feb_Jul_2026',a[a.month.between('2026-02','2026-07')])]:
                summary.append(dict(model=name,scope=scope,**policy_metrics(sized_account(g,marks))))
            for month,g in a.groupby('month'):monthly.append(dict(model=name,month=month,**policy_metrics(g)))
            daily.append(a.assign(model=name).drop(columns='intratrade_path'))
            cases.append(a.loc[blocked,[*KEY,'net_main','p_severe','risk_rank_max','risk_pool_size']].assign(model=name))
    for k in KS:intervals.append(bootstrap_pair(accounts[f'60d_rank_top{k}'],accounts[f'20d_rank_top{k}']).assign(K=k))
    sums=pd.DataFrame(summary);months=pd.DataFrame(monthly);blocked=pd.concat(cases,ignore_index=True)
    sums.to_csv(REPORT/'rank_gate_summary.csv',index=False);months.to_csv(REPORT/'rank_gate_monthly.csv',index=False)
    pd.concat(daily).to_csv(REPORT/'rank_gate_daily.csv.gz',index=False,float_format='%.17g')
    blocked.to_csv(REPORT/'rank_gate_blocked_trades.csv',index=False)
    pd.DataFrame(rankstats).to_csv(REPORT/'rank_gate_market_counts.csv',index=False)
    pd.concat(intervals).to_csv(REPORT/'rank_gate_paired_intervals.csv',index=False)
    pd.concat([read(REPORT/'summary.csv'),sums],ignore_index=True).to_csv(REPORT/'all_schemes_summary.csv',index=False)
    pd.concat([read(REPORT/'monthly.csv'),months],ignore_index=True).to_csv(REPORT/'all_schemes_monthly.csv',index=False)
    base_month=read(REPORT/'monthly.csv');base_month=base_month[base_month.model.eq('baseline')].set_index('month')
    lines=['# CO13补充：B1仅在全市场风险前K名时轮空','',
        'K=100、250、500在完整结果产生前固定。风险分数为次晨09:40净亏>2%的校准概率，按当日全市场预测排序；B1身份不参与排序或训练。排名不使用未来标签、当日未来成交量或次日是否交易。', '',
        '只对原成交额规则允许的B1交易检查：进入风险前K名才轮空，其余原样交易。边界并列者只有整组能落在前K名内才拦截，避免用股票代码任意决定轮空；实际拦截池可能少于K。', '',
        '排名是相对风险，并非概率阈值：市场整体风险很低时也可能出现前K名；不因事后表现再加概率门槛。本轮与60日方案使用相同K与并列规则。', '',
        '## 总体','', '|方案|净收益%|最大回撤%|交易笔数|拦截笔数|避亏/其中大亏|误拦盈利|剩余交易每笔净均值%|','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in sums[sums.scope.eq('all')].itertuples():
        lines.append(f'|{r.model}|{r.net_nav_pct:+.2f}|{r.settled_max_drawdown_pct:.2f}|{r.trades}|{r.baseline_trades-r.trades}|{r.losses_avoided}/{r.large_losses_avoided}|{r.profits_missed}|{r.net_trade_mean_pct:+.3f}|')
    lines+=['','## 逐月','', '每格为 **净收益% / 最大回撤% / 交易笔数 / 避亏笔数 / 误拦盈利笔数**。10月仅至13日。']
    def cell(r):return f'{r.net_nav_pct:+.2f} / {r.settled_max_drawdown_pct:.2f} / {int(r.trades)} / {int(r.losses_avoided)} / {int(r.profits_missed)}'
    for k in KS:
        lines+=['',f'### 仅拦截风险前{k}名','','|月份|原B1|60日/月更|20日/日更|','|---|---:|---:|---:|']
        for month,g in months.groupby('month'):
            r=g.set_index('model');lines.append(f'|{month}|{cell(base_month.loc[month])}|{cell(r.loc[f"60d_rank_top{k}"])}|{cell(r.loc[f"20d_rank_top{k}"])}|')
    lines+=['','## 被拦截的逐笔交易','',
        '`rank_gate_blocked_trades.csv`保存所有被拦的B1股票、概率、风险排名和实际收益；`rank_gate_market_counts.csv`保存每日实际排名池大小与边界并列处理。与其余方案合并的月表是`all_schemes_monthly.csv`。','']
    (REPORT/'RANK_GATE_RESULTS.md').write_text('\n'.join(lines))
    save_json(REPORT/'rank_gate_audit.json',dict(same_market_sample_ids=True,B1_picks_unchanged=True,
        no_future_labels_in_ranking=True,K=list(KS),formal_days=baseline.signal_date.nunique(),
        ranks_use_calibrated_probability_only=True,source_sha256={w:sha(OUT/'predictions.csv.gz') if w=='20d' else 'see_rank_gate_pre_run_freeze' for w in source}))
    print(sums[sums.scope.eq('all')][['model','net_nav_pct','settled_max_drawdown_pct','trades','losses_avoided','large_losses_avoided','profits_missed']].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['freeze','run']);globals()[p.parse_args().phase]()
