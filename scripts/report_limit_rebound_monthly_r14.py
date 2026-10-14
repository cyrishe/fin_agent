"""Join the monthly out-of-sample predictions and report cash-funded returns."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
import pandas as pd

import scripts.export_limit_rebound_short_mixed_r12 as detail
from scripts.export_limit_rebound_d0_top5_r9_detail import records, minute_clock
from scripts.audit_limit_rebound_account_drawdown_r13 import replay
from scripts.experiment_limit_rebound_short_mixed_r12 import top_five,xmatrix,dump,sha
from scripts.experiment_limit_rebound_monthly_r14 import OUT,SOURCE,MONTHS,metrics

DOC=Path('docs/stock_automl_runs/20261014_limit_rebound_monthly_r14')
EXITS={'d2_gross':'D2收盘','d3_gross':'D3收盘','d4_gross':'D4收盘','exit_gross':'策略卖出'}
MODELS={'rf':'随机森林','extra':'极端随机树','hgb':'浅层提升树','logistic':'逻辑回归'}


def model_name(cid):
    family,features=cid.split('_')
    return MODELS[family]+('35因子' if features=='expanded' else '23因子')


def all_predictions():
    paths=[OUT/'chosen_test_top5.pkl',OUT/'reference_test_top5.pkl']
    frames=[pd.read_pickle(p) for p in paths]
    p=OUT/'static_test_top5.pkl'
    if not p.exists():
        panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
        f=panel[panel.d0_date.ge('2026-01-01')].copy()
        frozen=json.loads((SOURCE/'chronological/selection_frozen.json').read_text())
        cid=frozen['selected']['id'];bundle=joblib.load(SOURCE/'chronological'/f'{cid}.joblib')
        top=top_five(f,bundle['model'].predict_proba(xmatrix(f,bundle['features']))[:,1])
        top['forecast_month']=top.d0_date.str[:7];top['model_id']=cid;top['model_policy']='原R12固定模型'
        top.to_pickle(p)
    frames.append(pd.read_pickle(p))
    f=pd.concat(frames,ignore_index=True)
    f['part']='test';f['day_seq']=pd.factorize(f.d0_date,sort=True)[0]+1
    return f


def enrich():
    f=all_predictions()
    chosen=f[f.model_policy.eq('滚动选优')]
    d0=f[(f.model_policy.eq('滚动选优'))|(f.d0_rank.eq(1)&f.selected)]
    a=d0[['d0_date','symbol','d0_cap','close','open','volume']].copy()
    a.columns=['d1_date','symbol','d1_cap','d1_close','d1_open','d1_volume']
    b=chosen[['d1_date','symbol','d1_cap','d1_close','d1_open','d1_volume']].copy()
    b=b[chosen.d1_st.eq(0)].dropna(subset=['d1_date','d1_open'])
    demand=pd.concat([a,b],ignore_index=True)
    for col in ['d1_cap','d1_close','d1_open','d1_volume']:
        assert demand.groupby(['d1_date','symbol'])[col].nunique().le(1).all(),col
    demand=demand.drop_duplicates(['d1_date','symbol'])
    detail.OUT=OUT
    found=[];days=list(demand.groupby('d1_date'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(detail.one_day,day,g):day for day,g in days}
        for i,job in enumerate(as_completed(jobs),1):
            found.extend(job.result())
            if i%25==0:print('minute days',i,'/',len(days),flush=True)
    facts=pd.DataFrame(found)
    facts.to_csv(OUT/'minute_facts.csv.gz',index=False,compression='gzip')
    f=f.merge(facts[['d1_date','symbol','minute_complete','final_close_cap_min']].rename(
          columns={'d1_date':'d0_date','minute_complete':'d0_minute_complete','final_close_cap_min':'d0_final_seal'}),
          on=['d0_date','symbol'],how='left',validate='many_to_one')
    f['d0_minute_complete']=f.d0_minute_complete.fillna(0).astype(int)
    f=f.merge(facts[['d1_date','symbol','minute_complete','first_close_cap_min','final_close_cap_min']],
          on=['d1_date','symbol'],how='left',validate='many_to_one')
    f['minute_complete']=f.minute_complete.fillna(0).astype(int)
    calendar=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
    f['complete_return_window']=f.date_i.add(4).lt(len(calendar))
    f['strict_policy']=f.selected&f.d0_rank.eq(1)&f.d0_minute_complete.eq(1)&f.d0_final_seal.gt(10)
    f.to_pickle(OUT/'enriched_predictions.pkl')
    chosen=f[f.model_policy.eq('滚动选优')].copy()
    for name in ['market_cap_source.csv.gz','market_cap_requested_keys.csv.gz']:
        if not (OUT/name).exists(): shutil.copy2(SOURCE/name,OUT/name)
    chosen=detail.market_caps(chosen)
    excel=records(chosen)
    for row,r in zip(excel,chosen.itertuples(),strict=True):
        row.pop('集合',None);row.pop('日序',None)
        row['D0日期']=r.d0_date;row['D0终封时']=minute_clock(r.d0_final_seal)
        row['D0分钟齐全']=int(r.d0_minute_complete);row['分钟齐全']=int(r.minute_complete)
        row['观察完整']=int(r.complete_return_window)
        # Pending outcome dates are distinct from identified anomalies.
        row['异常']=int(bool(r.anomaly) or pd.isna(r.d1_open)) if r.complete_return_window else None
        if pd.isna(r.d1_open):
            row['D1激活']=None;row['D1入选']=None;row['D1开板']=None
        if pd.isna(r.d1_up):row['D1封板']=None
        if pd.isna(r.d1_st):row['当时ST']=None
        ups=[getattr(r,f'd{k}_up') for k in range(1,5)]
        first=next((i for i,v in enumerate(ups) if pd.notna(v) and v==True),None)
        if first is not None and not all(pd.notna(v) for v in ups[:first+1]):
            row['冷静天数']='待观察';row['冷静末涨']=None
        if not r.complete_return_window or bool(r.anomaly) or pd.isna(r.d1_open):
            for key in ['冷静末涨','D2收涨','D3收涨','D4收涨','策略退出涨']:row[key]=None
        for k,v in row.items():
            if isinstance(v,(float,np.floating)) and not np.isfinite(v):row[k]=None
    dump(OUT/'top5_workbook_rows.json',excel)
    coverage=[]
    for policy,g in f.groupby('model_policy'):
        sel=g[g.selected];strict=sel[sel.d0_rank.eq(1)]
        coverage.append({'model_policy':policy,'all_top5':len(g),'selected':len(sel),
             'd0_rank1':len(strict),'d0_rank1_minute_complete':int(strict.d0_minute_complete.eq(1).sum()),
             'late_after_0940':int(g.strict_policy.sum()),
             'complete_return_selected':int((sel.complete_return_window&~sel.anomaly).sum()),
             'pending_return_selected':int((~sel.complete_return_window).sum()),
             'anomaly_selected':int((sel.complete_return_window&sel.anomaly).sum())})
    dump(OUT/'coverage.json',coverage)
    print(json.dumps(coverage,ensure_ascii=False),flush=True)


def summarize():
    f=pd.read_pickle(OUT/'enriched_predictions.pkl')
    calendar=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
    choices=[];candidates=[];test=json.loads((OUT/'test_metrics.json').read_text())
    for month in MONTHS:
        frozen=json.loads((OUT/month/'selection_frozen.json').read_text());cid=frozen['candidate']['id']
        r=next(r for r in frozen['candidates'] if r['candidate']['id']==cid)
        m=next(x for x in test if x['month']==month and x['policy']=='滚动选优')
        baseline=next(x for x in test if x['month']==month and x['policy']=='滚动固定森林')
        dates=frozen['months']
        choices.append({'预测月':month,'训练起月':dates['train'][0],'训练止月':dates['train'][1],
          '验证起月':dates['valid'][0],'验证止月':dates['valid'][1],'选定模型':model_name(cid),
          '训练样本':r['train']['classification_n'],'训练命中率':r['train']['hit_rate'],
          'CV入选命中':r['cv']['hit_rate'],'CV日命中':r['cv']['hits_per_day'],
          '验证入选命中':r['valid']['hit_rate'],'验证日命中':r['valid']['hits_per_day'],
          '综合分':r['combined_score'],'验证样本':r['valid']['classification_n'],
          '测试入选':m['picks'],'测试标签完整':m['known'],'测试命中':m['hits'],
          '测试命中率':m['hit_rate'],'固定森林命中率':baseline['hit_rate'],'测试AP':m['ap']})
        for c in frozen['candidates']:
            candidates.append({'预测月':month,'模型':model_name(c['candidate']['id']),
                   'CV日命中':c['cv']['hits_per_day'],'验证日命中':c['valid']['hits_per_day'],
                   '综合分':c['combined_score'],'选定':int(c['candidate']['id']==cid)})
    overview=[];monthly=[];daily=[];ledger=[];classifications=[]
    for scope,months,policies in [('2025—2026',MONTHS,['滚动选优','滚动固定森林']),
           ('2026同段',[m for m in MONTHS if m.startswith('2026')],['滚动选优','滚动固定森林','原R12固定模型'])]:
        for policy in policies:
            g=f[f.model_policy.eq(policy)&f.forecast_month.isin(months)]
            for name,mask in [('开盘Top1',g.selected),('D0第一且晚封板',g.strict_policy)]:
                picked=g[mask];known=picked[picked.observed_3]
                trades=picked[picked.complete_return_window&~picked.anomaly]
                assert trades[['d1_close','d2_close','d3_close','d4_close']].notna().all().all()
                cls={'范围':scope,'模型方案':policy,'交易规则':name,'入选':len(picked),
                     '标签完整':len(known),'三日再板':int(known.label_3.sum()),
                     '命中率':float(known.label_3.mean()) if len(known) else None,
                     '收益笔数':len(trades),'待观察':int((~picked.complete_return_window).sum()),
                     '异常剔除':int((picked.complete_return_window&picked.anomaly).sum())}
                classifications.append(cls)
                for exit_col,exit_cn in EXITS.items():
                    stats,m,d,t=replay(trades,exit_col,calendar,months)
                    ctx={'范围':scope,'模型方案':policy,'交易规则':name,'卖出':exit_cn}
                    overview.append({**ctx,**stats,'三日再板率':cls['命中率']})
                    monthly += [{**ctx,**row} for row in m]
                    daily += [{**ctx,**row} for row in d]
                    ledger += [{**ctx,**row} for row in t]
    rows={'月度模型':choices,'效果对照':overview,'逐月收益':monthly,
          '月度前五':json.loads((OUT/'top5_workbook_rows.json').read_text())}
    dump(OUT/'workbook_data.json',rows)
    dump(OUT/'classification_comparison.json',classifications)
    pd.DataFrame(candidates).to_csv(OUT/'all_candidates_comparison.csv',index=False)
    pd.DataFrame(daily).to_csv(OUT/'daily_nav.csv.gz',index=False,compression='gzip')
    pd.DataFrame(ledger).to_csv(OUT/'cash_ledger.csv.gz',index=False,compression='gzip')
    pd.DataFrame(overview).to_csv(OUT/'account_comparison.csv',index=False)
    report(rows,classifications)
    print(pd.DataFrame(classifications).to_string(index=False),flush=True)
    print(pd.DataFrame(overview).query('卖出 == "D2收盘"')[['范围','模型方案','交易规则','笔数','账户总收益','账户月均收益','账户最大回撤']].to_string(index=False),flush=True)


def report(rows,cls):
    DOC.mkdir(parents=True,exist_ok=True)
    lines=['# R14：10 个月训练＋2 个月验证、每月更新模型','',
       '预测范围为 2025 年 1 月至 2026 年 10 月，每月固定一个模型预测当月，不在月中重训。2026 年 10 月尚不完整；D0 特征截至 10 月 13 日，结果报价截至 10 月 14 日，完整 D4 的 D0 最晚为 10 月 8 日。未观察完的记录保持待观察，不当成负例或零收益。',
       '', '## 训练与选择','',
       '- 例如预测 2025 年 1 月：2024 年 1—10 月训练、11—12 月验证；预测 2026 年 10 月：2025 年 10 月—2026 年 7 月训练、2026 年 8—9 月验证。',
       '- 在 10 个月训练段内部做三折前推 CV：前 4 月训练→第 5—6 月验证；前 6 月→第 7—8 月；前 8 月→第 9—10 月。全体同日股票一起切分，不随机打散。',
       '- 七个固定方案：随机森林、极端随机树、浅层提升树各 23/35 因子，逻辑回归 35 因子；参数沿用 R12，sklearn 固定 1.7.1。填缺、标准化、类别权重均只用各自训练行。',
       '- 综合分=50%×CV 折外日命中率＋50%×独立两月验证日命中率。日命中率是当天开盘 Top1 三日再板的命中天数/可评估 D0 天数，轮空计零；并列看两者较低值，再看平均 AP。入选命中率另列。训练拟合内成绩不用于选模型。',
       '- 选定后使用仅在这 10 个月训练的已保存模型预测下一月，不将两月验证并回训练。每月模型、三个 CV 子模型、所有评分与月份映射均保存。所有月选择先冻结，再统一读取下一月测试表现，不用测试换模型。',
       '- 训练月份按 D0 样本日期归属，特征回看可以早于训练起月。类别均衡后的分数用于同日排序，不是校准概率；不同月份更换模型后，分数不宜直接横向比较。',
       '', '## 时间边界与候选池','',
       '训练或历史验证标签的 D4 必须在下一阶段开始前已经发生。训练还剔除截至该截止日仍在连板的事件，防止同一事件跨入评估集。验证评分保留完整 D0 封板候选池，只排除整个观察窗口尚未结束的日期；不能先按个股未来异常或是否继续连板过滤候选再排名。候选池口径在下一月测试结果读取前固定，初步中间评分归档在 pre_candidate_pool_correction。',
       '',
       '预测月保留月初所有交易日，历史价格可以进入随后月份的特征回看，这些是当时已知信息；不要求不同月份完全不用相同历史价格。不同月份训练窗口相互重叠，过去测试月在更晚轮次满足截止要求后可进入训练，这是月度滚动的正常过程。每月的未来结果不进入当月训练或选模。同一历史时期此前研究过，不能称未见过的新盲测。',
       '', '## 两种交易规则与对照','',
       '“开盘Top1”：D0 先固定前五，D1 历史非 ST 且开盘低于涨停价者激活，选 D0 分数最高的一只。“D0第一且晚封板”：进一步限定 D0 名次第一、D1 入选、经分钟价量核验的 D0 最终封板时间晚于 09:40。分钟只用于这项交易过滤和明细时刻，不进入模型特征与选模指标。旧 R13 单变量分裂点不叠加。',
       '',
       '对照一是每月固定使用 35 因子随机森林、同样 10 个月训练，比较是否需要按月选择算法。对照二只在 2026 年共同区间使用原 R12 固定模型，不拿用到 2025 年训练的模型去称 2025 年样本外对照。原 R12 模型在本次相同完整候选池重新回放，数字不等同于原表裁剪边界后的结果。2026 同段比较都以 2026 年首期净值 1 单独开始；全时段净值连续，不在月初重置。',
       '', '## 分类结果','', '|范围|模型方案|交易规则|完整标签|三日再板|命中率|收益笔数|待观察|异常剔除|','|---|---|---|---:|---:|---:|---:|---:|---:|']
    for c in cls:
        lines.append('|'+ '|'.join([c['范围'],c['模型方案'],c['交易规则'],str(c['标签完整']),str(c['三日再板']),f'{c["命中率"]:.2%}',str(c['收益笔数']),str(c['待观察']),str(c['异常剔除'])])+'|')
    lines+=['','## D2 退出账户结果','', '|范围|模型方案|交易规则|笔数|账户总收益|账户月均收益|账户最大回撤|','|---|---|---|---:|---:|---:|---:|']
    for r in rows['效果对照']:
        if r['卖出']=='D2收盘':
            lines.append('|'+ '|'.join([r['范围'],r['模型方案'],r['交易规则'],str(r['笔数']),*[f'{r[c]:.2%}' for c in ['账户总收益','账户月均收益','账户最大回撤']]])+'|')
    lines+=['', '## D0 第一且晚封板：全部退出方式','',
      '|范围|模型方案|卖出|笔数|每笔均涨|账户总收益|账户月均收益|账户最大回撤|',
      '|---|---|---|---:|---:|---:|---:|---:|']
    for r in rows['效果对照']:
        if r['交易规则']=='D0第一且晚封板':
            lines.append('|'+ '|'.join([r['范围'],r['模型方案'],r['卖出'],str(r['笔数']),
                *[f'{r[c]:.2%}' for c in ['每笔均涨','账户总收益','账户月均收益','账户最大回撤']]])+'|')
    lines+=['', '## 结果解释','',
       '三日再板主指标没有显示按月选择算法优于每月固定训练随机森林：全期入选命中分别为 240/423（56.74%）与 249/423（58.87%）。晚封板过滤后的命中率为 47/84 与 58/103；这两种方案的交易次数不同。',
       '',
       '晚封板条件下，滚动选优的全期 D2 账户收益 +63.38%、最大回撤 11.62%；每月固定森林为 +69.52%、17.61%。在共同的 2026 年区间，滚动选优 D2 为 +39.31%、10.95%，原 R12 固定模型为 +10.86%、12.17%。这一交易子集有改善，但不能据此称按月选模总体更优。D3/D4/策略退出的回撤明显更高；三日再板命中包含 D1 当天，不能将命中率当作收益率或可实现利润。',
       '',
       '保留本次所有月度模型作为研究资产，未据测试结果重选参数、算法或退出方式，未替换原基准。',
       '',
       '账户每笔投入前收盘净值 25%，现金不足限于可用现金，最多四笔，不借款；持仓按每日收盘计价，最大回撤为净值相对此前峰值的跌幅。每月换模型但资金及未结束持仓延续；买卖仍按原 D1 开盘、D2/D3/D4 收盘或首次再板退出规则。月收益按资金实际持有日期计算，单笔均值按 D0 归月。持有期不同意味着平均仓位不同。',
       '',
       '异常仍在选股后剔除，不替补。完整持有窗口统一到 D4，再比较四种退出，避免每种规则用不同的最新样本。未扣费用、滑点或整手约束；日线开盘与收盘价仅为成交假设，未证明开盘一定买到或跌停可以卖出。',
       '',
       f'所有模型与证据位于 `{OUT}`。Excel 包含月度模型、效果对照、逐月收益和滚动选优模型的每日前五明细。明细不列股票代码和名称，D0 日期列首；分钟数据缺失时留空，观察未结束的收益留空。']
    (DOC/'README.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['enrich','summarize']);args=parser.parse_args()
    globals()[args.stage]()
