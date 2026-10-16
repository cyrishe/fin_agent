from pathlib import Path
import pandas as pd,json,shutil,tarfile,hashlib
p=Path('outputs/stock_automl/b2_four_five_clock_20261016');d=Path('docs/stock_automl_runs/20261016_b2_four_five_clock');d.mkdir(parents=True,exist_ok=True)
s=pd.read_csv(p/'summary.csv');m=pd.read_csv(p/'monthly.csv');f=pd.read_pickle(p/'picks.pkl')
names={'five_original':'五K原开价代理','four_original':'四K原开价代理','five_lock_delay':'五K锁定后延一K（原截止）','four_B2':'当前B2四K锁定后延一K','five_scan_0936_0940':'五K从09:36扫描至09:40','four_scan_0935_0940':'四K从09:35扫描至09:40','four_scan_0936_0940':'四K同窗09:36至09:40','five_lock_delay_to0940':'五K锁定延一K扩至09:40','four_lock_delay_to0940':'四K锁定延一K扩至09:40'}
text='''# R25：四K、五K执行时钟核对与冻结规则回放

## 结论

旧表的09:36—09:40是分钟结束标签，其开价实际属于09:35—09:39，不能表述为实际09:36—09:40买入。B2最早开价09:35，最晚09:39。原五K延后方案是先按原时序锁定股票，再等同股下一根合格K，并非从09:36开始重新扫描全部Top3。两者会改变买价、最终股票和是否交易。

本轮不重训、不调参、不替换生产B2。冻结各月四K/五K规则，原Top3、实体>-8%拦截后不替补、D2收盘卖出；统一100万元、空仓半现金/有持仓全余款、买卖各0.1%。区间2026-01-01至10-14。月份按账户净值计算，包含9月末信号的10月交易。末笔未平仓按收盘估值。四K和五K使用各自冻结形态规则，因此主比较同时包含观察长度和规则差异。

## 时钟与执行

- 前四根完整K结束于09:34，前五根结束于09:35。
- 新五K直接扫描：开价时钟09:36—09:40，对应结束标签09:37—09:41；逐分钟扫描原Top3，通过形态者中最早开板优先，同分钟D0排名优先。选定后实体门槛失败则当天轮空。
- 新四K直接扫描：开价时钟09:35—09:40，对应结束标签09:36—09:41，其他同上。
- 四K同窗对照也从09:36开始，用于分离额外一分钟窗口的影响。
- 锁定延后一K：先按原激活窗口锁定唯一股票，再等同股下一根符合条件的分钟开价；扩至09:40版本仅扩大买入截止，不重新选择股票。
- 这里的开价和当分钟volume>0均来自历史K，volume只有分钟完成才完整可知。即使延后一K，也不能证明实际服务拿到整根K后仍能成交在该根开价；不是tick/盘口级回测。需要实际报价和订单日志才能确定延迟、排队、冲击及全额成交。

## 统一费率总效果

|方案|买入笔|账户收益|最大回撤|夏普|三日再板命中率|标签已知数|已平仓盈利率|
|---|---:|---:|---:|---:|---:|---:|---:|
'''
for r in s.itertuples():text+=f'|{names[r.variant]}|{r.entries}|{r.total_return:+.2%}|{r.max_drawdown:.2%}|{r.sharpe:.2f}|{r.three_day_hit:.2%}|{r.three_day_known}|{r.closed_win_rate:.2%}|\n'
text+='\n三日再板标签为D1—D3任一天收盘封板，包括D1，不是买入后未来三个完整交易日必定涨停；未知标签不填0。上述命中率只统计最终选择的股票，不是全体候选准确率。形态规则继承原6个月因果折外训练+2个月验证后冻结，逐月测试。此次已看过的历史窗口仅为执行敏感性回放，不称新盲测。\n\n## 逐月账户收益\n\n|月份|五K09:36—09:40|四K09:35—09:40|当前B2|\n|---|---:|---:|---:|\n'
piv=m.pivot(index='month',columns='variant',values='return_')
for idx,r in piv.iterrows():text+=f'|{idx}|{r.five_scan_0936_0940:+.2%}|{r.four_scan_0935_0940:+.2%}|{r.four_B2:+.2%}|\n'
text+='\n## 选股差异\n\n'
for a,b in [('five_original','five_scan_0936_0940'),('five_scan_0936_0940','four_scan_0935_0940'),('five_scan_0936_0940','four_B2')]:
 x=f[f.variant.eq(a)].set_index('d1_date');y=f[f.variant.eq(b)].set_index('d1_date');idx=x.index.intersection(y.index);same=x.loc[idx].symbol.eq(y.loc[idx].symbol)
 text+=f'- {names[a]} vs {names[b]}：同时交易{len(idx)}天，同股{same.sum()}天、异股{(~same).sum()}天；仅前者{len(x.index.difference(y.index))}天，仅后者{len(y.index.difference(x.index))}天。\n'
text+='\n## 核验与保存\n\n重新读取完整分钟源并逐股检查前11根标签09:31—09:41，按既有来源过滤。四个旧方案逐笔股票、买价、时间完全复现（106/134/98/122笔）。全部运行输入来源哈希与冻结规则哈希见audit.json。所有对照收益均未用来重选月度规则。脚本scripts/audit_b2_four_five_clock_20261016.py；完整本地运行目录outputs/stock_automl/b2_four_five_clock_20261016。归档本地已保存，云盘待同步。\n'
(d/'README.md').write_text(text)
for file in ['summary.csv','monthly.csv','audit.json']:shutil.copy2(p/file,d/file)
# Human-readable daily details with actual open clock, plus traceable bar label.
l=pd.read_pickle(p/'ledger.pkl');l['buy_clock']=(pd.to_datetime(l.entry_time,format='%H:%M')-pd.Timedelta(minutes=1)).dt.strftime('%H:%M')
cols=['variant','d0_date','symbol','d0_rank','entry_date','buy_clock','entry_time','buy','exit_date','sell','return_','mark_date','mark_price','cost','pnl']
l[cols].rename(columns={'variant':'方案','d0_date':'选股日','symbol':'代码','d0_rank':'排名','entry_date':'买入日','buy_clock':'买入时钟','entry_time':'K结束标签','buy':'买价','exit_date':'卖出日','sell':'卖价','return_':'净收益','mark_date':'估值日','mark_price':'估价','cost':'投入','pnl':'已实现盈亏'}).to_csv(p/'逐笔时钟对照_R25_20261016.csv',index=False,encoding='utf-8-sig')
shutil.copy2(d/'README.md',p/'README.md')
print(d/'README.md')
