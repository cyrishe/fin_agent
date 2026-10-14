"""Revalue frozen R13 trades as a cash-funded overlapping portfolio.

No threshold selection is performed here. All observations use saved daily prices.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_limit_rebound_single_split_r13 import (
    OUT as OLD, SOURCE, GROUPS, PARTS, EXITS, FEATURES, condition, months_for, rule_label,
)
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, sha

OUT=OLD/'account_drawdown_v2'
DOC=Path('docs/stock_automl_runs/20261014_limit_rebound_single_split_r13/ACCOUNT_DRAWDOWN.md')


def nav_drawdown(values):
    nav=np.asarray(values,dtype=float)
    return float(np.max(1.-nav/np.maximum.accumulate(nav)))


def replay(frame,exit_col,calendar,months):
    f=frame.sort_values(['d0_date','symbol'])
    assert not f.d1_date.duplicated().any()
    entries={r.d1_date:r for r in f.itertuples()}
    cash=1.; previous_nav=1.; positions=[]; daily=[]; trades=[]; max_positions=0
    dates=[(i,d) for i,d in enumerate(calendar) if d[:7] in months]
    for index,date in dates:
        if date in entries:
            r=entries[date]
            assert len(positions)<4
            budget=min(cash,previous_nav*.25)
            assert budget>0
            gap=int(r.exit_gap) if exit_col=='exit_gross' else int(exit_col[1])
            assert 2<=gap<=4
            prices=[float(getattr(r,f'd{k}_close')) for k in range(1,gap+1)]
            assert np.isfinite(prices).all() and min(prices)>0
            p={'shares':budget/float(r.d1_open),'prices':prices,'start':index,'gap':gap,
               'entry':float(r.d1_open),'cost':budget,'row':r}
            positions.append(p);cash-=budget
            max_positions=max(max_positions,len(positions))
        closing=[]; market_value=0.
        for p in positions:
            age=index-p['start'];assert 0<=age<p['gap']
            price=p['prices'][age];value=p['shares']*price
            if age==p['gap']-1:
                cash+=value
                r=p['row'];gross=price/p['entry']-1
                assert np.isclose(gross,getattr(r,exit_col),atol=1e-12)
                trades.append({'d0_date':r.d0_date,'entry_date':r.d1_date,'exit_date':date,
                               'symbol':r.symbol,'cost':p['cost'],'proceeds':value,'gross':gross})
            else:
                closing.append(p);market_value+=value
        positions=closing
        nav=cash+market_value
        assert cash>=-1e-12 and nav>0
        daily.append({'date':date,'month':date[:7],'nav':nav,'cash':cash,
                      'market_value':market_value,'positions':len(positions)})
        previous_nav=nav
    assert not positions and len(trades)==len(f)
    assert np.isclose(previous_nav,1+sum(t['proceeds']-t['cost'] for t in trades))
    df=pd.DataFrame(daily);monthly=[];prior_month_nav=1.
    for month in months:
        m=df[df.month.eq(month)];assert len(m)
        month_end=float(m.nav.iloc[-1]);g=f[f.d0_date.str[:7].eq(month)]
        monthly.append({'月份':month,'笔数':len(g),'每笔均涨':float(g[exit_col].mean()) if len(g) else None,
              '账户月收益':month_end/prior_month_nav-1.,
              '月内最大回撤':nav_drawdown(np.r_[prior_month_nav,m.nav.to_numpy()])})
        prior_month_nav=month_end
    result={'笔数':len(f),'每笔均涨':float(f[exit_col].mean()) if len(f) else None,
            '账户总收益':previous_nav-1.,'账户月均收益':float(np.mean([m['账户月收益'] for m in monthly])),
            '账户最大回撤':nav_drawdown(np.r_[1.,df.nav.to_numpy()]),
            '正收益月占比':float(np.mean([m['账户月收益']>0 for m in monthly])),
            '最高持仓数':max_positions}
    return result,monthly,daily,trades


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    frozen=json.loads((OLD/'thresholds_frozen.json').read_text())
    old=json.loads((OLD/'evaluation.json').read_text()); old_results={r['id']:r for r in old['rules']}
    assert old['frozen_sha256']==sha(OLD/'thresholds_frozen.json')
    f=pd.read_pickle(OLD/'candidate_audit.pkl');f=f[f.late_seal&~f.anomaly].copy()
    calendar=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
    spec={'source_thresholds_sha256':sha(OLD/'thresholds_frozen.json'),
       'source_data_sha256':sha(OLD/'candidate_audit.pkl'),
       'allocation':'start NAV=1; each new entry min(25% previous closing NAV, available cash); max 4 overlapping positions; no borrowing',
       'prices':'D1 daily open buy; daily closes mark all open positions; original D2/D3/D4/strategy exit unchanged',
       'order':'open buys use existing cash; closing sale cash available at next opening',
       'months':'account month-end NAV divided by preceding month-end NAV; idle cash zero return',
       'drawdown':'1-current closing NAV / historical peak closing NAV; monthly DD resets peak to preceding month-end',
       'costs':'same gross-price assumptions as R13; no fees, slippage or lot rounding',
       'thresholds_refitted':False,'anomalies':'same R13 exclusions, no replacements'}
    dump(OUT/'specification.json',spec)
    base=[];rules=[];month_rows=[];daily_rows=[];trade_rows=[];evidence=[]
    jobs=[]
    for group in GROUPS:
        for exit_col in EXITS:
            jobs.append({'id':f'{group}_{exit_col}_baseline','group':group,'exit':exit_col,'baseline':True})
    jobs+=frozen['rules']
    for rule in jobs:
        if 'unavailable' in rule: continue
        group=rule['group'];exit_col=rule['exit'];is_base=rule.get('baseline',False)
        label='基础条件' if is_base else rule_label(rule)
        ctx={'组别':GROUPS[group],'卖出':EXITS[exit_col],'规则':label}
        result={'组别':GROUPS[group],'变量':'—' if is_base else FEATURES[rule['feature']],
                '条件':label,'卖出':EXITS[exit_col]}
        if not is_base: result['旧筛选通过']=int(old_results[rule['id']].get('passes',False))
        for part,partcn in PARTS.items():
            g=f[f.experiment_group.eq(group)&f.part.eq(part)]
            if not is_base: g=g[condition(g,rule['feature'],rule['threshold'],rule['side'])]
            m,monthly,daily,trades=replay(g,exit_col,calendar,months_for(group,part))
            if not is_base:
                previous=old_results[rule['id']]['parts'][part]
                assert m['笔数']==previous['n']
                assert m['每笔均涨'] is None and previous['mean'] is None or np.isclose(m['每笔均涨'],previous['mean'])
            result.update({partcn+k:v for k,v in m.items()})
            if is_base: base.append({**ctx,'集合':partcn,**m})
            month_rows += [{**ctx,'集合':partcn,**row} for row in monthly]
            daily_rows += [{'rule':rule['id'],'part':part,**row} for row in daily]
            trade_rows += [{'rule':rule['id'],'part':part,**row} for row in trades]
            evidence.append({'rule':rule['id'],'part':part,**m})
        if not is_base: rules.append(result)
    dump(OUT/'workbook_data.json',{'规则复核':rules,'基础表现':base,'账户逐月':month_rows})
    dump(OUT/'results.json',evidence)
    pd.DataFrame(daily_rows).to_csv(OUT/'daily_nav.csv.gz',index=False,compression='gzip')
    pd.DataFrame(trade_rows).to_csv(OUT/'cash_ledger.csv.gz',index=False,compression='gzip')
    pd.DataFrame(rules).to_csv(OUT/'规则复核.csv',index=False)
    report(base,rules)
    print(pd.DataFrame(base).query('集合 == "测试"')[['组别','卖出','笔数','每笔均涨','账户总收益','账户月均收益','账户最大回撤']].to_string(index=False))
    print(pd.DataFrame(rules).query('组别 == "A连续时间" and 旧筛选通过 == 1')[['条件','卖出','测试账户总收益','测试账户月均收益','测试账户最大回撤']].to_string(index=False))


def report(base,rules):
    lines=['# R13：账户净值与最大回撤修正','',
      '原表“最大回撤”为各笔涨幅相加后的最大峰谷差，单位是百分点；不能当作账户净值最大回撤。将它直接称为最大回撤容易造成误解，旧表不能用于判断资金回撤。本次以现金及持仓市值构造账户曲线，保留原 40 个分裂点、原入选股票、原买卖价格和异常排除，不重新选择分裂点。',
      '', '## 账户口径','',
      '- 每个模型、集合及规则分别以净值 1 开始，资金不跨训练、验证、测试传递。',
      '- 每次 D1 开盘投入前一交易日收盘净值的 25%，现金不足时以剩余现金为限；最多同时四笔持仓，不借款。允许小数股，未模拟整手限制。',
      '- 每日以收盘价计持仓市值，现金和未卖持仓全部计入净值；D2/D3/D4 或原策略卖出价格不变。收盘卖出后的现金用于后续开盘。',
      '- 最大回撤 = 1 − 当日收盘净值 / 历史最高收盘净值，初始净值 1 也计入峰值。未计算盘中净值的更深回撤。',
      '- 账户月收益按月末净值相对上月末计算，月均收益为各月收益的算术平均；月内回撤以月初净值为起点。无交易时资金持现金。',
      '- 每笔均涨及笔数保持原口径，按 D0 月份归属；账户损益按实际持有日期归属。2026 年 10 月是不完整月份。',
      '- 沿用原价格毛收益，未扣手续费、滑点，开盘成交和跌停卖出不能由日线保证。',
      '', '## 基础条件测试结果','',
      '|组别|卖出|笔数|每笔均涨|账户总收益|账户月均收益|账户最大回撤|',
      '|---|---|---:|---:|---:|---:|---:|']
    for r in base:
        if r['集合']=='测试': lines.append('|'+ '|'.join([r['组别'],r['卖出'],str(r['笔数']),*[f'{r[c]:.2%}' for c in ['每笔均涨','账户总收益','账户月均收益','账户最大回撤']]])+'|')
    lines+=['','## 对原筛选结论的影响','',
      '旧分裂点是按“月均单笔涨幅总和减累计和回撤”选出的，不能再称为账户收益与账户回撤的最优解。新版只复核原规则，不用已看过的验证和测试重新调点。`旧筛选通过`仅保存此前登记状态，不表示通过本次账户风险检验。',
      '', 'A 组基础验证只有 7 笔；B 组训练基础仅 14 笔、验证 7 笔，且随机月份含未来训练。风险换算后仍需结合收益和样本量判断，不能只因数字变小就认定策略可用。',
      '', f'账户净值、现金账本与完整规则结果保存在 `{OUT}`。']
    DOC.write_text('\n'.join(lines)+'\n')


if __name__=='__main__': run()
