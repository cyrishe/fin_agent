"""R31 D2 supplement: evaluate the actual exit without refitting or reselection."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scripts.experiment_b2_validation_r31 import OUT as SOURCE, NAMES, MONTHS
from scripts.report_b2_validation_r31 import table
from scripts.replay_limit_rebound_cash_r23 import dump, sha

OUT=Path('outputs/stock_automl/b2_d2_metrics_r31_20261019')
DOC=Path('docs/stock_automl_runs/20261019_b2_validation_r31')


def metrics(group):
    closed=group[group.exit_date.notna()].copy()
    assert closed.return_.notna().all() and closed.d2_up.notna().all()
    assert np.allclose(closed.return_, closed.sell/closed.buy*.999/1.001-1, atol=1e-12, rtol=0)
    assert closed.sell.eq(closed.d2_close).all()
    d12=closed.d1_up.eq(True)|closed.d2_up.eq(True)
    return dict(entries=len(group),closed=len(closed),pending=len(group)-len(closed),
        wins=int(closed.return_.gt(0).sum()),win_rate=float(closed.return_.gt(0).mean()),
        mean=float(closed.return_.mean()),median=float(closed.return_.median()),
        bad5=int(closed.return_.lt(-.05).sum()),bad5_rate=float(closed.return_.lt(-.05).mean()),
        d2_limit=int(closed.d2_up.eq(True).sum()),d2_limit_rate=float(closed.d2_up.eq(True).mean()),
        d12_limit=int(d12.sum()),d12_limit_rate=float(d12.mean()),
        only_d3=int((closed.label_3.eq(1)&~d12).sum()))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    ledger=pd.read_pickle(SOURCE/'ledger.pkl')
    results=[]
    for period in ['总体',*MONTHS]:
        for variant in NAMES:
            group=ledger[ledger.variant.eq(variant)]
            if period!='总体':group=group[group.entry_date.str.startswith(period)]
            results.append(dict(period=period,variant=variant,**metrics(group)))
    dump(OUT/'d2_metrics.json',results)
    original=json.loads((SOURCE/'workbook_data.json').read_text())
    for sheet in original.values():sheet['render']=False
    rows=[]
    for r in results:
        rows.append([r['period'] if r['period']=='总体' else r['period']+'-01',NAMES[r['variant']],
                     r['closed'],r['pending'],r['win_rate'],r['mean'],r['median'],r['bad5_rate'],
                     r['d2_limit_rate'],r['d12_limit_rate'],r['only_d3']])
    new=table(['买入月份','方案','已卖笔','待卖笔','D2胜率','D2均收益','D2中位收益','亏超5%','D2封板率','两日再板率','仅D3再板笔'],
              rows,percent=[4,5,6,7,8,9],dates=[0])
    new['note']='D2收益已扣买卖各0.1%；仅统计已卖笔；月度按买入月份；两日再板含D1、D2，仍不等于盈利。'
    dump(OUT/'workbook_data.json',{'D2评估':new,**original})
    spec=dict(source_ledger=str(SOURCE/'ledger.pkl'),source_sha256=sha(SOURCE/'ledger.pkl'),
        scope='Actual purchases Apr1-Sep30 2026; D2 next market day after purchase; closed only; pending excluded.',
        fees=.001,monthly='Buy-month cohort, unlike marked-to-market calendar-month account returns.',
        target_changes=False,retrained=False,primary='D2 net per-trade mean, median, win rate, loss below -5%; account monthly returns and drawdown remain primary.',
        ancillary='D2 sealed close, D1-or-D2 sealed close, only-D3 sealed close. Daily close labels do not establish a post-entry executable limit event.')
    dump(OUT/'specification.json',spec)
    md=['# R31 补充：按D2实际卖出评价','',
        '三日再板率43.18%与44.30%是同一2026年4—9月实际买入股票的比较，联合一月验证低约1.12个百分点。当前D1买入、D2收盘卖出，因此D3才涨停不属于本笔持仓收益。', '',
        'D2评价仅用已卖出的交易，买卖各0.1%费用；一笔未卖的日线一月组不当作亏损或零收益。月度按买入月份归属，和账户持仓日历月收益不同。', '',
        '|方案|已卖笔|D2费后胜率|笔均收益|中位收益|亏超5%|D2封板率|D1—D2再板率|仅D3再板笔|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in results[:4]:
        md.append(f"|{NAMES[r['variant']]}|{r['closed']}|{r['win_rate']:.2%}|{r['mean']:+.2%}|{r['median']:+.2%}|{r['bad5_rate']:.2%}|{r['d2_limit_rate']:.2%}|{r['d12_limit_rate']:.2%}|{r['only_d3']}|")
    md+=['','## 判断','',
        '联合一月验证D2费后胜率53.41%，原方案50.63%；笔均+1.55%对+0.87%，中位+1.29%对+0.13%；亏损超过5%的比例22.73%对22.78%，基本不变。优势体现在D2收益分布，不能说大亏发生率已有改善。',
        'D1—D2再板率分别42.05%与41.77%，基本相同。仅D3再板的原方案2笔、联合一月1笔，说明三日指标包含持仓外事件，但不能仅凭这几笔解释全部收益差异。',
        '主评价建议使用D2费后笔均收益、中位收益、胜率和亏超5%比例，并保留账户逐月收益/回撤。三日再板率与D2封板率作辅助。封板不等于盈利，实际收益还取决于买价；没有封板也可能盈利。',
        '目前日线训练目标仍为D1—D3再板，第二层选规仍使用该标签及D2—D4退出收益代理。本轮仅补充指标，未改标签、选规、月模型或交易。若调整训练/选规目标，应另立实验，在训练/验证上决定、测试上报告。','',
        '新工作簿首张为D2评估，含总体及六个月四组结果；原六张表数据保留。']
    (DOC/'D2_METRICS.md').write_text('\n'.join(md)+'\n')
    dump(DOC/'d2_metrics.json',results)
    print(pd.DataFrame(results[:4]).to_string(index=False))


if __name__=='__main__':main()
