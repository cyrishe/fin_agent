from pathlib import Path
import pandas as pd,json,hashlib,shutil
p=Path('outputs/stock_automl/b2_d2_tail_vwap_20261016');d=Path('docs/stock_automl_runs/20261016_b2_d2_tail_vwap');d.mkdir(exist_ok=True)
s=pd.read_csv(p/'summary.csv');m=pd.read_csv(p/'monthly.csv');tr=pd.read_csv(p/'trades.csv');v=pd.read_csv(p/'minute_verification.csv')
t='''# R30：固定8%目标价与14:50—15:00成交均价

沿用R29冻结的三套买入（线上B2、R26四K/五K均价），2026-01-01至10-14。D2止盈目标为买入价×1.08，按0.01向上取整，高开超过目标也只记目标价；费率各0.1%，100万元原资金轮换不变，卖出资金次晨才再用。最后未到D2的持仓仍按D1收盘估值。

尾盘均价=14:51—15:00结束的10根完整分钟K成交额之和/成交股数之和，表示14:50—15:00，包含集合竞价。服务器原volume为手，×100转股；归档原volume为股。核对240根、日线收盘/最高/最低/成交量以及VWAP落在窗口价格范围内。

可顺序执行主口径：截至14:50结束K的最高价触及目标，按目标价模拟卖出；否则14:50启动最后10分钟VWAP退出，取消整笔止盈判定。避免在14:58才首次涨到目标，却事后撤销14:50以来已经发生的卖出。只需10分钟平均卖出，不模拟时间先后不明的分钟内组合委托。

另列“全天8%否则尾盘均价_事后对照”，仅为识别用户字面条件在晚触发交易的差异，不能作为顺序执行结果。全天目标+收盘和单独尾盘VWAP提供独立对照。均价是市场成交均价代理，未证明按该均价可全额成交；集合竞价量可能使VWAP接近收盘，不能等同每分钟等量卖出均价。无新模型、无阈值优化、无生产变更。

## 总体

|方案|卖出方式|收益|最大回撤|夏普|
|---|---|---:|---:|---:|
'''
for r in s.to_dict('records'):t+=f"|{r['方案']}|{r['卖出']}|{r['total_return']:+.2%}|{r['max_drawdown']:.2%}|{r['sharpe']:.2f}|\n"
for name,g in m.groupby('方案'):
 t+=f'\n## {name}按月\n\n|月份|收盘收益|全天目标后收盘收益|目标后尾盘收益|收盘回撤|目标后尾盘回撤|\n|---|---:|---:|---:|---:|---:|\n'
 for month,q in g.groupby('month'):
  a=q[q.卖出.eq('收盘')].iloc[0];b=q[q.卖出.eq('1450前8%否则尾盘均价')].iloc[0];c=q[q.卖出.eq('全天8%否则收盘')].iloc[0]
  t+=f'|{month}|{a.return_:+.2%}|{c.return_:+.2%}|{b.return_:+.2%}|{a.drawdown:.2%}|{b.drawdown:.2%}|\n'
t+='\n## 触发时点\n\n'+tr.groupby('方案')[['早触发','晚触发']].sum().to_string()+'\n\n云盘待同步。\n'
(d/'README.md').write_text(t);(p/'README.md').write_text(t)
for x in ['summary.csv','monthly.csv','trades.csv','minute_verification.csv']:shutil.copy2(p/x,d/x)
(p/'manifest.json').write_text(json.dumps({str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [p/'raw.json',Path('scripts/replay_b2_tail_vwap_20261016.py')]},indent=2))
print(m[m.方案.eq('B2') & m.卖出.isin(['全天8%否则收盘','1450前8%否则尾盘均价'])][['month','卖出','return_','drawdown']].to_string(index=False))
