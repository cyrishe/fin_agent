"""Exposure, costs and single-best-trade diagnostics for the fixed ranking rules."""
import pandas as pd
from scripts.experiment_daily20_risk_co13 import REPORT,CO8,read
from scripts.report_independent_0940_netloss_co12 import policy_metrics
from scripts.experiment_b1_calibrated_risk_co11 import sized_account


def run():
    daily=pd.concat([read(REPORT/'rank_gate_daily.csv.gz'),read(REPORT/'smallk_daily.csv.gz')],ignore_index=True)
    marks={day:g for day,g in read(CO8/'valuation_paths.csv.gz').groupby('signal_date')}
    rows=[]
    for name,g in daily.groupby('model'):
        g=g.sort_values('signal_date');avg=float(g.loc[g.baseline_trade,'exposure'].mean())
        best=g.loc[g.trade].sort_values('net_main').iloc[-1].signal_date
        variants=[('same_average_position_B1',g.assign(exposure=g.baseline_trade*avg),0.),
                  ('extra_10bp',g,.001),('exclude_best_trade',g[g.signal_date.ne(best)],0.),
                  ('exclude_zero_entry_volume',g[g.signal_date.ne('2025-11-07')],0.)]
        for label,z,cost in variants:
            rows.append(dict(model=name,diagnostic=label,excluded_best_day=best,average_position=avg,
                             **policy_metrics(sized_account(z,marks,extra=cost))))
    result=pd.DataFrame(rows);result.to_csv(REPORT/'rank_gate_robustness.csv',index=False)
    lines=['# 风险排名过滤的费用与集中度诊断','',
        '事后诊断，不用于重选K。固定平均仓位参考原B1，保持相同均值仓位但不选日；单笔剔除各方案实际成交中最好的一笔；零成交量买价代理日期共同剔除为2025-11-07。', '',
        '|方案|同均值仓位B1净收益%|额外10bp后收益%|剔除最好一笔后收益%|被剔除最好日|','|---|---:|---:|---:|---|']
    for name,g in result.groupby('model'):
        r=g.set_index('diagnostic')
        lines.append(f'|{name}|{r.loc["same_average_position_B1","net_nav_pct"]:+.2f}|{r.loc["extra_10bp","net_nav_pct"]:+.2f}|{r.loc["exclude_best_trade","net_nav_pct"]:+.2f}|{r.iloc[0].excluded_best_day}|')
    (REPORT/'RANK_GATE_ROBUSTNESS.md').write_text('\n'.join(lines)+'\n')
    print(result[result.model.eq('20d_rank_top500')][['diagnostic','net_nav_pct','settled_max_drawdown_pct']].to_string(index=False))


if __name__=='__main__':run()
