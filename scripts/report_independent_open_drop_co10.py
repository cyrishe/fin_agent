"""Readable report and independent membership audit for CO10."""
import json
from pathlib import Path
import shutil
import pandas as pd
import numpy as np
from scripts.experiment_independent_open_drop_co10 import OUT,REPORT,FEATURES,read,sha,save_json

NAMES={'amount_baseline':'原成交额轮空'}
for family,label in [('nonfall','大跌/不跌'),('extreme','大跌/大涨')]:
    for alg,alglabel in [('logistic','逻辑回归'),('hist','提升树')]:
        for mode,mlabel in [('main','标准'),('aggressive','激进')]:
            NAMES[f'{family}_{alg}_{mode}']=f'{label}·{alglabel}·{mlabel}'


def run():
    d=read(OUT/'dataset.csv.gz');folds=json.loads((OUT/'folds.json').read_text())
    rows=[];by_id=d.set_index('sample_id')
    for f in folds:
        folder=OUT/'models'/f['month']/f['arm']
        ids=read(folder/'training_membership.csv.gz').sample_id
        t=by_id.loc[ids]
        assert t.signal_date.isin(f['train_dates']).all()
        assert t.label_known.all()
        assert (t.big_drop|(t.nonfall if f['arm'].startswith('nonfall') else t.big_rise)).all()
        assert not set(f['train_dates'])&set(f['validation_dates'])
        assert not set(f['validation_dates'])&set(f['test_dates'])
        assert t.label_available_time.max()<min(f['validation_dates'])+' 14:40:00'
        v=d[d.signal_date.isin(f['validation_dates'])]
        assert v.label_available_time.dropna().max()<min(f['test_dates'])+' 14:40:00'
        assert sha(folder/'model.joblib')==f['model_sha256']
        assert sha(folder/'training_membership.csv.gz')==f['training_membership_sha256']
        rows.append(dict(month=f['month'],arm=f['arm'],n=len(t),stocks=t.symbol6.nunique(),days=t.signal_date.nunique(),
                         down=int(t.big_drop.sum()),safe=int((~t.big_drop).sum())))
    audits=pd.DataFrame(rows);audits.to_csv(REPORT/'training_membership_audit.csv',index=False)
    frozen=json.loads((REPORT/'pre_run_freeze.json').read_text())
    assert all(sha(Path(p))==h for p,h in frozen['files'].items())
    save_json(REPORT/'audit.json',dict(models=len(rows),all_memberships_and_date_boundaries_passed=True,
        frozen_hashes_unchanged=True,B1_used_in_training_or_threshold=False,
        training_rows_min=int(audits.n.min()),training_rows_max=int(audits.n.max()),features=FEATURES))
    s=read(REPORT/'market_summary.csv');mo=read(REPORT/'market_monthly.csv')
    b=read(REPORT/'b1_summary.csv');bm=read(REPORT/'b1_monthly.csv')
    primary=s[(s.model=='nonfall_hist_main')&(s.scope=='market')].iloc[0]
    test=d[d.signal_date.between('2025-08-01','2026-10-13')&d.label_known]
    winner_total=int(test.big_rise.sum())
    winner_summary=s[s.scope.eq('market')][['model','large_winners_blocked']].copy()
    winner_summary['opening_big_winners_total']=winner_total
    winner_summary['opening_big_winners_blocked_pct']=100*winner_summary.large_winners_blocked/winner_total
    winner_summary.to_csv(REPORT/'opening_winners_missed.csv',index=False)
    bs=b[(b.model=='nonfall_hist_main')&(b.scope=='all')].iloc[0]
    base=b[(b.model=='amount_baseline')&(b.scope=='all')].iloc[0]
    lines=['# CO10：独立全市场次晨大跌模型','',
        '**本轮训练和验证使用沪深市场股票，B1不参与拟合或阈值选择。B1仅在模型冻结后用于下游交易效果检查。**','',
        '主标签为次晨正式开盘价相对前日14:40决策参考价下跌至少2%；安全类为不跌（收益≥0，包含大涨），训练暂不使用−2%到0之间边界样本。验证和测试保留全部已知标签。另比较仅用≥2%大涨作安全类的两极方案。风险分数受类别权重及抽样影响，不是真实概率。','',
        f'冻结输入{len(d):,}条股票日样本、{d.signal_date.nunique()}个日期；主方案测试覆盖{int(primary.known):,}条已知结果，原有{int(primary.events):,}次大跌，拦截{int(primary.caught):,}次，召回{primary.recall_pct:.2f}%，保留{primary.retention_pct:.2f}%机会，拦截精度{primary.precision_pct:.2f}%。主方案同时误拦{int(primary.large_winners_blocked):,}/{winner_total:,}次开盘上涨至少2%的机会；各方案见opening_winners_missed.csv。','',
        '## 全市场时间外测试','',
        '|样本定义/算法/门槛|大跌召回%|拦截精度%|保留机会%|保留样本大跌率%|漏掉大跌数|',
        '|---|---:|---:|---:|---:|---:|']
    for r in s[s.scope.eq('market')].itertuples():
        lines.append(f'|{NAMES[r.model]}|{r.recall_pct:.2f}|{r.precision_pct:.2f}|{r.retention_pct:.2f}|{r.retained_event_pct:.2f}|{r.missed}|')
    lines+=['','精度指被拦截股票中实际大跌的比例；保留机会按股票日计，不能当成账户仓位或实际交易数。标准门槛在验证期至少保留30%，激进门槛至少10%，测试期实际比例不受强行配额约束。','',
        '## 主方案逐月市场表现','',
        '|月份|已知样本|大跌次数|拦截/漏报|召回%|精度%|保留%|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in mo[(mo.model=='nonfall_hist_main')&(mo.scope=='market')].itertuples():
        lines.append(f'|{r.month}|{r.known}|{r.events}|{r.caught}/{r.missed}|{r.recall_pct:.2f}|{r.precision_pct:.2f}|{r.retention_pct:.2f}|')
    lines+=['','## 冻结后接入B1的结果','',
        '仍由B1选股、原成交额门槛先执行；风险模型只拦截，不换股、不递补。14:42买入代理/次晨09:40固定退出，10万元全额现金复利，费用沿用CO8。','',
        '|方案|净收益%|结算回撤%|交易数|避亏|误拦盈利|',
        '|---|---:|---:|---:|---:|---:|']
    for r in b[b.scope.eq('all')].itertuples():
        lines.append(f'|{NAMES[r.model]}|{r.net_nav_pct:.2f}|{r.settled_max_drawdown_pct:.2f}|{r.trades}|{r.losses_avoided}|{r.profits_missed}|')
    lines+=['','### 主方案逐月B1对照','',
        '|月份|原净收益%|新净收益%|原/新回撤%|原/新笔数|避亏/误拦盈利|',
        '|---|---:|---:|---:|---:|---:|']
    for r in bm[bm.model.eq('nonfall_hist_main')].itertuples():
        o=bm[bm.model.eq('amount_baseline')&bm.month.eq(r.month)].iloc[0]
        lines.append(f'|{r.month}|{o.net_nav_pct:.2f}|{r.net_nav_pct:.2f}|{o.settled_max_drawdown_pct:.2f}/{r.settled_max_drawdown_pct:.2f}|{o.trades}/{r.trades}|{r.losses_avoided}/{r.profits_missed}|')
    lines+=['','## 训练与边界','',
        '- 每月前100个市场日，前60日训练、后40日全市场验证、下一月测试；整日划分。模型验证后不重拟合。门槛目标90%召回，仅按召回和保留比例选择，不读取B1收益。',
        '- 每个训练日先归一权重，再给风险类5倍权重；12项特征只用T日14:40已知信息和T−1以前历史。全体原始样本和每折成员、模型、验证测试分数均保留。',
        '- 测试期间未知结果或无法恢复经济权益的跨除权标签不计入分类分母，但当日评分和未知数量保留；不是事后过滤模型输出。正常停牌不补造数据。',
        '- 60个保存模型均回读检查；完整成员与时序审计见audit.json，定向测试见test_receipt.txt。日线开盘仅作标签，当前日最终日线OHLC不作输入。',
        '- 最终态历史档案不能证明实时到达；价格为研究代理，未证明真实成交。2025-08至2026-10-13为已经反复研究的历史，不称项目级盲测。',
        '- 全市场不同股票并非独立样本；按整日5/10日分块的召回区间见market_date_block_intervals.json。信号涨幅4%—9%的固定子群见market_summary.csv，禁止按该结果重新调门槛。',
        '- 等平均暴露和同月等笔数随机轮空见b1_controls.json；原B1盈利区间2026年2–7月单列在b1_summary.csv，避免与15个月扩展样本混淆。',
        '- 生产B1未变更。此前只用B1训练的CO9范围已被用户更正，不能当作本独立模型的训练或结果。','']
    lines += ['## 独立调用','',
        '入口：scripts/predict_independent_open_drop_co10.py --model-dir MODEL_FOLDER --input FEATURES.csv --output DECISIONS.csv。模型目录例如 outputs/stock_automl/independent_open_drop_co10/models/2026-10/nonfall_hist。',
        '输入为signal_date、symbol6、feature_cutoff及上述12项特征；必须使用1.7.1环境。脚本核对模型哈希、预测月份和声明的截止时间，输出risk_score与avoid_for_open_risk。声明检查不能替代上游特征可见性审计，不自动下单。','']
    (REPORT/'README.md').write_text('\n'.join(lines))
    shutil.copy2('/tmp/co10_tests.log',REPORT/'test_receipt.txt')
    print('Audited',len(rows),'models; report written',flush=True)


if __name__=='__main__':run()
