"""Frozen R12 scores: D0 Top1, D1 selected, D0 final seal after 09:40.

Single-variable thresholds are chosen using training returns only; no model refit.
Return sums and additive drawdown are trade statistics, not an account NAV.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import scripts.export_limit_rebound_short_mixed_r12 as detail
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha

SOURCE=Path('outputs/stock_automl/limit_rebound_short_mixed_r12_20261014')
OUT=Path('outputs/stock_automl/limit_rebound_single_split_r13_20261014')
DOC=Path('docs/stock_automl_runs/20261014_limit_rebound_single_split_r13')
GROUPS={'chronological':'A连续时间','mixed':'B随机月份'}
PARTS={'train':'训练','valid':'验证','test':'测试'}
FEATURES={'d1_gap':'D1开盘涨幅','market_cap_100m':'总市值亿',
          'd0_turnover_pct':'D0换手率','board_streak_days':'原连板数','d0_amount_100m':'D0成交额亿'}
EXITS={'d2_gross':'D2收盘','d3_gross':'D3收盘','d4_gross':'D4收盘','exit_gross':'策略卖出'}


def specification():
    p=OUT/'specification.json'
    spec={'experiment':'R13_single_variable', 'models':'R12 frozen, no refit',
      'filter':'D0 rank=1 AND D1 selected=1 AND verified D0 final seal minute >10 (09:40)',
      'month':'D0 calendar month; calendar months assigned by R12; zero-trade months count as zero',
      'drawdown':'largest peak-to-trough decline of cumulative sum of per-trade returns, ordered by D0; percentage points, NOT portfolio drawdown',
      'objective':'mean monthly sum of returns minus full-period additive maximum drawdown',
      'features':FEATURES,'exits':EXITS,
      'thresholds':'training 10th..90th percentiles; zero for D1 gap; all observed integer streak values except max; both > and <=',
      'train_minimum':'max(10, ceil(25% of clean base trades)); at least 3 active months',
      'confirmation':'train objective exceeds unfiltered base; valid >=5 and test >=10 trades, each >=2 active months; each mean monthly sum >0; at least half assigned months positive; full-period drawdown <= same-part base',
      'anomalies':'R12 anomaly rows excluded after original selections; no replacements',
      'date_limit':'R12 outcomes observed through 2026-10-14; 2024 cannot satisfy minute condition',
      'fees':0,'seed':20261014}
    if p.exists():
        assert json.loads(p.read_text())==spec
    else: dump(p,spec)
    return spec


def prepare():
    specification()
    f=detail.chosen_frames()
    f=f[f.d0_rank.eq(1)&f.selected].copy()
    # Join the exact market-cap values already delivered, avoiding new source revisions.
    lookup={}
    for group in GROUPS:
        for r in json.loads((SOURCE/group/'workbook_rows.json').read_text()):
            lookup[(group,r['集合'],r['日序'],r['D0名次'])]=r
    caps=[];estimated=[]
    for r in f.itertuples():
        old=lookup[(r.experiment_group,PARTS[r.part],int(r.day_seq),int(r.d0_rank))]
        assert abs(old['D0评分']-r.score)<1e-14
        caps.append(old['总市值亿']);estimated.append(old['市值估算'])
    f['market_cap_100m']=caps;f['market_cap_estimated']=estimated
    f['d1_gap']=f.d1_open/f.d1_preclose-1
    demand=f[['d0_date','symbol','d0_cap','close','open','volume']].drop_duplicates(['d0_date','symbol'])
    demand.columns=['d1_date','symbol','d1_cap','d1_close','d1_open','d1_volume']
    # Reuse the audited minute reader with D0 quotes, in a separate cache directory.
    detail.OUT=OUT
    jobs_by_day=list(demand.groupby('d1_date')); found=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(detail.one_day,day,g):day for day,g in jobs_by_day}
        for i,job in enumerate(as_completed(jobs),1):
            found.extend(job.result())
            if i%25==0: print('D0 minute days',i,'/',len(jobs_by_day),flush=True)
    facts=pd.DataFrame(found).rename(columns={'d1_date':'d0_date'})
    facts.to_csv(OUT/'d0_minute_facts.csv.gz',index=False,compression='gzip')
    f=f.merge(facts[['d0_date','symbol','minute_complete','minute_reason','final_close_cap_min']],
              on=['d0_date','symbol'],how='left',validate='many_to_one')
    f['late_seal']=f.minute_complete.eq(1)&f.final_close_cap_min.gt(10)
    f['month']=f.d0_date.str[:7]
    f.to_pickle(OUT/'candidate_audit.pkl')
    f.to_csv(OUT/'candidate_audit.csv.gz',index=False,compression='gzip')
    coverage=[]
    for (group,part),g in f.groupby(['experiment_group','part']):
        coverage.append({'group':group,'part':part,'top1_selected':len(g),
             'minutes_complete':int(g.minute_complete.eq(1).sum()),
             'missing_2024':int(g.minute_reason.eq('2024_no_archive').sum()),
             'missing_other':int((g.minute_complete.ne(1)&g.minute_reason.ne('2024_no_archive')).sum()),
             'after_0940':int(g.late_seal.sum()),'clean':int((g.late_seal&~g.anomaly).sum()),
             'clean_months':sorted(g.loc[g.late_seal&~g.anomaly,'month'].unique().tolist())})
    dump(OUT/'coverage.json',coverage)
    print(json.dumps(coverage,ensure_ascii=False),flush=True)


def months_for(group,part):
    p=SOURCE/group/f'selected_{part}_all.pkl'
    return sorted(m for m in pd.read_pickle(p).d0_date.str[:7].unique() if m>='2025-01')


def max_drawdown(values):
    path=np.r_[0.,np.cumsum(np.asarray(values,dtype=float))]
    return float(np.max(np.maximum.accumulate(path)-path))


def metrics(f,col,months):
    f=f.sort_values(['d0_date','symbol'])
    x=f[col].dropna(); eligible=f.loc[x.index]
    sums=eligible.groupby('month')[col].sum().reindex(months,fill_value=0.)
    total=float(x.sum()); dd=max_drawdown(x)
    return {'n':len(x),'months':len(months),'active_months':int(eligible.month.nunique()),
       'mean':float(x.mean()) if len(x) else None,'total':total,
       'monthly_total_mean':float(sums.mean()),'mdd':dd,
       'monthly_win_rate':float(sums.gt(0).mean()),'worst_month':float(sums.min()),
       'score':float(sums.mean())-dd}


def monthly(f,col,months,context):
    rows=[]
    for m in months:
        g=f[f.month.eq(m)].sort_values(['d0_date','symbol']); x=g[col].dropna()
        rows.append({**context,'月份':m,'笔数':len(x),'每笔均涨':float(x.mean()) if len(x) else None,
                     '总涨幅':float(x.sum()),'最大回撤':max_drawdown(x)})
    return rows


def condition(f,feature,threshold,side):
    # Missing observations belong to neither side.
    return f[feature].notna() & (f[feature].gt(threshold) if side=='>' else f[feature].le(threshold))


def thresholds(train,feature):
    x=train[feature].dropna()
    if x.empty: return []
    vals=np.unique(x.quantile(np.arange(.1,1.,.1)).to_numpy())
    if feature=='board_streak_days': vals=np.sort(x.unique())[:-1]
    if feature=='d1_gap': vals=np.unique(np.r_[vals,0.])
    return [float(t) for t in vals if (x.gt(t).any() and x.le(t).any())]


def fit():
    specification()
    f=pd.read_pickle(OUT/'candidate_audit.pkl')
    # Only training return rows enter threshold construction or optimization.
    train=f[f.part.eq('train')&f.late_seal&~f.anomaly].copy()
    selected=[]; trials=[]
    for group in GROUPS:
        g=train[train.experiment_group.eq(group)]; months=months_for(group,'train')
        for col in EXITS:
            base=metrics(g,col,months); min_n=max(10,math.ceil(base['n']*.25))
            for feature in FEATURES:
                choices=[]
                for threshold in thresholds(g,feature):
                    for side in ('>','<='):
                        m=metrics(g[condition(g,feature,threshold,side)],col,months)
                        row={'group':group,'exit':col,'feature':feature,'threshold':threshold,'side':side,
                             'train':m,'eligible':m['n']>=min_n and m['active_months']>=3}
                        trials.append(row)
                        if row['eligible']: choices.append(row)
                if choices:
                    winner=max(choices,key=lambda r:(r['train']['score'],r['train']['monthly_total_mean'],r['train']['n']))
                    selected.append({**winner,'id':f'{group}_{col}_{feature}',
                                     'train_baseline':base,'train_improves':winner['train']['score']>base['score']+1e-12})
                else:
                    selected.append({'group':group,'exit':col,'feature':feature,
                        'id':f'{group}_{col}_{feature}','unavailable':'训练样本或月份不足，未选分裂点','train_baseline':base})
    frozen={'spec_sha256':sha(OUT/'specification.json'),'data_sha256':sha(OUT/'candidate_audit.pkl'),
            'rules':selected}
    dump(OUT/'training_trials.json',trials)
    dump(OUT/'thresholds_frozen.json',frozen)
    print('frozen training-only choices',len(selected),flush=True)


def confirmed(m,base,part):
    return (m['n'] >= (5 if part=='valid' else 10) and m['active_months']>=2
            and m['monthly_total_mean']>0 and m['monthly_win_rate']>=.5
            and m['mdd']<=base['mdd']+1e-12)


def evaluate():
    frozen=json.loads((OUT/'thresholds_frozen.json').read_text())
    assert frozen['data_sha256']==sha(OUT/'candidate_audit.pkl')
    f=pd.read_pickle(OUT/'candidate_audit.pkl'); f=f[f.late_seal&~f.anomaly].copy()
    summary=[]; permonth=[]; paired=[]; bases=[]; results=[]
    for group,title in GROUPS.items():
        for part,partcn in PARTS.items():
            g=f[f.experiment_group.eq(group)&f.part.eq(part)]; months=months_for(group,part)
            for col,exitcn in EXITS.items():
                m=metrics(g,col,months)
                bases.append({'组别':title,'集合':partcn,'卖出':exitcn,**cn_metrics(m)})
                permonth+=monthly(g,col,months,{'规则':'基础条件','组别':title,'集合':partcn,'卖出':exitcn})
            # Requested illustrative zero split, not used to choose other variables.
            for side in ('>','<='):
                z=g[condition(g,'d1_gap',0.,side)]
                for col,exitcn in EXITS.items():
                    paired+=monthly(z,col,months,{'规则':f'D1开盘涨幅{side}0','组别':title,'集合':partcn,'卖出':exitcn})
    for rule in frozen['rules']:
        group=rule['group'];col=rule['exit'];feature=rule['feature']
        base={'组别':GROUPS[group],'变量':FEATURES[feature],'卖出':EXITS[col]}
        if 'unavailable' in rule:
            summary.append({**base,'条件':rule['unavailable'],'双集通过':0});results.append(rule);continue
        threshold=rule['threshold'];side=rule['side']; label=rule_label(rule)
        output={**base,'条件':label,'训练优于基础':int(rule['train_improves'])}
        part_results={}
        for part,partcn in PARTS.items():
            g=f[f.experiment_group.eq(group)&f.part.eq(part)];months=months_for(group,part)
            selected=g[condition(g,feature,threshold,side)];m=metrics(selected,col,months);baseline=metrics(g,col,months)
            if part=='train': assert m==rule['train']
            part_results[part]=m
            output.update({partcn+k:v for k,v in cn_metrics(m).items()})
            output[partcn+'基础月均总涨']=baseline['monthly_total_mean'];output[partcn+'基础回撤']=baseline['mdd']
            if part!='train': output[partcn+'通过']=int(confirmed(m,baseline,part))
            permonth+=monthly(selected,col,months,{'规则':label,'组别':GROUPS[group],'集合':partcn,'卖出':EXITS[col]})
            opposite='<=' if side=='>' else '>'
            other=g[condition(g,feature,threshold,opposite)]
            paired+=monthly(other,col,months,{'规则':f'{FEATURES[feature]}{opposite}{format_threshold(feature,threshold)}（另一侧）',
                               '组别':GROUPS[group],'集合':partcn,'卖出':EXITS[col]})
        output['双集通过']=int(rule['train_improves'] and output['验证通过'] and output['测试通过'])
        summary.append(output);results.append({**rule,'parts':part_results,'passes':bool(output['双集通过'])})
    rows={'基础表现':bases,'分裂点检验':summary,'逐月表现':permonth,'另一侧与零界':paired}
    dump(OUT/'workbook_data.json',rows)
    dump(OUT/'evaluation.json',{'frozen_sha256':sha(OUT/'thresholds_frozen.json'),'rules':results})
    for name,data in rows.items(): pd.DataFrame(data).to_csv(OUT/f'{name}.csv',index=False)
    report(rows,results)
    print('rules',len(results),'passes',sum(r.get('passes',False) for r in results),flush=True)
    print(pd.DataFrame(bases).to_string(index=False),flush=True)


def cn_metrics(m):
    return {'笔数':m['n'],'有交易月':m['active_months'],'每笔均涨':m['mean'],
            '月均总涨':m['monthly_total_mean'],'最大回撤':m['mdd'],
            '正收益月占比':m['monthly_win_rate'],'最差月':m['worst_month']}


def format_threshold(feature,t):
    if feature=='d1_gap': return f'{t*100:.8g}%'
    if feature=='d0_turnover_pct': return f'{t:.8g}%'
    return f'{t:.8g}'


def rule_label(r): return FEATURES[r['feature']]+r['side']+format_threshold(r['feature'],r['threshold'])


def report(rows,rules):
    coverage=json.loads((OUT/'coverage.json').read_text());DOC.mkdir(parents=True,exist_ok=True)
    lines=['# R13：D0 前一、次日入选、最终封板晚于 09:40 的单变量检验','',
      '2026-10-14。复用 R12 两个已经保存的模型及其原始评分；没有重训。基础条件严格为 D0名次=1 且 D1入选=1，另要求 D0 最终封板的一分钟 K 结束时间晚于 09:40。不是 D1 封板时间，也不是激活者第一名却 D0 排名第二至第五的样本。',
      '', '## 统计和选点口径','',
      '按 D0 年月分组；每笔涨幅的分母均为 D1 开盘假设买价。分别看 D2、D3、D4 收盘和 D2～D4 首次封板收盘、否则 D4 收盘的策略退出。剔除原先识别的异常，不替换入选股票。未实证开盘成交、未扣交易费用。',
      '', '月总涨幅=当月各笔涨幅之和。回撤=按 D0 顺序累加各笔涨幅后，历史峰值至后续低点的最大差，单位为百分点。月均总涨幅包括该集合所有分配月份，无交易月计零。它们是独立交易的统计量，持仓可能重叠，不是资金账户的收益和回撤。月均每笔涨幅在无交易月份留空。',
      '', '每个变量、每种卖出方式各找一个分裂点及保留方向，分别独立检验，不叠加变量。涨幅只检验用户指定的 D1 开盘涨幅。其余为 D0 总市值、换手率、原连板数、成交额。总市值沿用 R12 历史值及其历史股本估算值。',
      '', '候选点只取训练集的 10%～90% 分位点，D1 开涨另含零，连板数取训练集实际整数。比较 > 与 ≤ 两边。至少保留 max(10,训练基础笔数×25%向上取整) 笔且覆盖 3 个有交易月份。目标=月均总涨幅−整个训练期最大回撤；并列依次取月均总涨幅高、笔数多者。所有点先落盘冻结，才读取验证和测试收益作验证。',
      '', '预先固定“记录为待跟踪规则”的门槛：训练目标优于未加变量的基础条件；验证至少 5 笔、测试至少 10 笔，各至少 2 个有交易月份；两集合月均总涨幅均为正，至少半数分配月份为正，且回撤均不大于各自基础条件。该门槛不随结果调整。它是筛选标准，不是统计显著性结论。',
      '', '## 数据覆盖','', '|组别|集合|Top1且入选|分钟完整|缺2024分钟|其他分钟缺失|09:40后|剔异常后|', '|---|---|---:|---:|---:|---:|---:|---:|']
    for c in coverage:
        lines.append(f'|{GROUPS[c["group"]]}|{PARTS[c["part"]]}|{c["top1_selected"]}|{c["minutes_complete"]}|{c["missing_2024"]}|{c["missing_other"]}|{c["after_0940"]}|{c["clean"]}|')
    lines+=['','B 组 2024 年无法核实分钟条件，因此本次训练/验证交易规则只使用原划分中有分钟事实的 2025、2026 年部分，未把 2024 年调入其它集合。B 组仍是随机月份的历史泛化检验，不能称向未来回测。R12 测试时期此前已研究过，本次历史验证不是新盲测。','',
      '## 通过双集门槛的规则','']
    passed=[r for r in rules if r.get('passes')]
    if not passed: lines.append('没有规则同时通过预先固定的训练改善与验证、测试门槛。全部最优训练分裂点及失败结果仍完整保留。')
    for r in passed:
        lines.append(f'- {GROUPS[r["group"]]}，{EXITS[r["exit"]]}，{rule_label(r)}：验证月均总涨 {r["parts"]["valid"]["monthly_total_mean"]:.2%} / 回撤 {r["parts"]["valid"]["mdd"]:.2%}；测试 {r["parts"]["test"]["monthly_total_mean"]:.2%} / {r["parts"]["test"]["mdd"]:.2%}。')
    lines+=['','## 结果解释','',
      '“双集通过”只表示达到上述比较门槛，不等于收益高、绝对回撤小，也不代表在验证和测试均超过基础条件的收益。A 组满足门槛的 D2 市值/换手率规则测试每笔收益接近零；D4 连板规则的累计涨幅回撤仍大。B 组训练只有 14 笔基础交易，多条规则训练收益仍为负；两组验证基础均只有 7 笔。当前只能把通过项登记为探索性候选，不能认定稳定有效。',
      '', '2026 年 10 月只观察到当前可完整退出的 D0 日期，该月属于不完整月份，仍列在逐月表并参与当前月均数。无交易月份的单笔平均值为空，总涨幅及回撤为零；不能把空缺单笔平均值当成一次零收益交易。',
      '', '完整结果见同名 Excel：基础表现、分裂点检验、逐月表现、另一侧与零界。后者包括 D1 开盘涨幅 >0 与 ≤0 的逐月结果及选定分裂点另一侧。基础事实、全部训练尝试、冻结阈值、三集合结果与数据哈希在 `'+str(OUT)+'`。']
    if (DOC/'ACCOUNT_DRAWDOWN.md').exists():
        lines[1:1]=['', '> **回撤口径修正**：本页的累计涨幅峰谷差不能作为账户最大回撤。账户风险请见[账户净值复核](ACCOUNT_DRAWDOWN.md)及“账户回撤V2”表；旧双集通过状态不代表通过账户风险检验。']
    (DOC/'README.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','fit','evaluate']);a=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    globals()[a.stage]()
