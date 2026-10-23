from pathlib import Path
import pandas as pd,numpy as np,json,hashlib,subprocess
R=Path('/data/cyris/work/b2_model_economics_r73_20261022')
s=pd.read_pickle(R/'joined_98.pkl')
a=pd.read_csv(R/'方案收益对照.csv')
t=pd.read_csv(R/'逐笔经济影响.csv')
z=t[(t['mode']=='morning7')&(t.policy=='skip_unified_crash')].copy()
z['预警判断']=np.where(z['是否买入'].eq(1),'未拦截',np.where(z['真实标签'].eq('急跌'),'正确预警','误报'))
z['费后收益率(%)']=z['费后收益率']*100
cols=['股票','D0','D1','D2','模型预测','真实标签','预警判断','是否买入','买价','卖价假设','费后收益率(%)','原每笔1万盈亏元','取消净影响元_每笔1万','原账户仓位元','原账户该笔盈亏元','7触发分钟','7退出时刻','D2开收跌停','D2D3连跌','成交未核实']
z[cols].to_csv(R/'当前卖法_98笔对照.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
z.loc[z['是否买入'].eq(0),cols].to_csv(R/'急跌预警30笔_误报及得失.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
x=t[(t.policy=='skip_unified_crash')&t['是否买入'].eq(0)].copy();x['月份']=x.D0.str[:7]
monthly=x.groupby(['mode','月份'])['取消净影响元_每笔1万'].agg(['count','sum']).reset_index().rename(columns={'count':'拦截数','sum':'净改善元_每笔1万'})
monthly.to_csv(R/'按选中月_拦截得失.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
floor=s[s.close_exit_unverified&s['预测类别_实验'].eq('急跌')].copy()
daily=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl').set_index(['trade_date','stk_code'])
for r in floor.itertuples():
 assert abs(float(daily.loc[(r.d3_date,r.stk_code),'preclose'])-r.d2_close)<.001
floor['D3额外估值损失元_每笔1万']=(floor.d2_close-floor.d3_close)/floor.entry*.999/1.001*10000
floor[['stk_code','d0_date','d2_date','entry','d2_close','d3_close','D2D3连续收盘跌停','all_day_locked','D3额外估值损失元_每笔1万']].to_csv(R/'被拦截6笔跌停_D3估值敏感性.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
# Reconcile exported numbers from independent raw price calculations.
net=(s.sell7.to_numpy()/s.entry.to_numpy())*.999/1.001-1
assert np.allclose(net,z['费后收益率'],rtol=0,atol=5.1e-9)
assert np.allclose(s.d1_close,s.d2_preclose,rtol=0,atol=.001)
for mode in ['close','morning7']:
 for p in a.policy.unique():
  ledger=pd.read_csv(R/f'{mode}_{p}_ledger.csv')
  nav=pd.read_csv(R/f'{mode}_{p}_daily.csv')
  assert abs((ledger.proceeds-ledger.cost).sum()+1e6-nav.nav.iloc[-1])<.01
  assert nav.cash.min()>-.01
  for row in ledger.itertuples():
   assert (row.sell_date,row.sell_time)>(row.buy_date,row.buy_time)
# Completed trigger bar is before the execution bar; earliest eligible subsequent bar only.
for r in s.itertuples():
 b=pd.read_csv(R/'minutes'/f'{r.d2_date}_{r.symbol}.csv.gz')
 b['clock']=pd.to_datetime(b.time).dt.strftime('%H:%M')
 hit=b[(b.clock<='11:30')&(b.high>1.07*r.d2_preclose)]
 assert bool(len(hit))==bool(r.trigger_7)
 if r.trigger_7:
  assert hit.iloc[0].clock==r.trigger_7
  assert r.sell7_time>=r.trigger_7
  idx=hit.index[0]
  assert abs(float(b.loc[idx+1,'open'])-r.sell7)<.001
  assert b.loc[idx+1,'volume']>0
# Direct vs subsequent position sizing contribution identity.
assert np.allclose(a['原仓位取消直接贡献元']+a['后续仓位变化贡献元'],a['相对基准账户变化元'],rtol=0,atol=.01)
frozen={'definition':'imac/B2_V2','definition_sha256':'54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00','model_source':'R72 frozen 2022-24 fit / 2025 select / 2026 retrospective test; no refit or threshold change','database_registration':'not submitted; no production writes','cloud_archive':'not synchronized or independently read back','server':str(R),'minute_reconciliations':98,'seven_triggers':41,'all_close_daily_nav_vs_existing_replay_max_delta':0,'cash_ledger_scenarios_checked':12,'preclose_discrepancies':0,'d3_preclose_discrepancies_cancelled6':0}
frozen['source_model_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/data/cyris/work/b2_first_board_flow_r72_20261022').glob('*.joblib')}
(R/'verification.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2)+'\n')
def money(x):return f'{x:,.2f}'
def acct(mode,p):return a[(a['mode']==mode)&(a.policy==p)].iloc[0]
def table(headers,rows):
 return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
atts=pd.read_csv(R/'取消买入得失归因.csv')
atr=atts[(atts['mode']=='morning7')&(atts.policy=='skip_unified_crash')]
atrtable=table(['拦截组','笔数','原盈利／亏损','放弃盈利','避开亏损','净改善'],[[r['分组'],r['笔数'],str(r['盈利笔数'])+'／'+str(r['亏损笔数']),money(r['放弃盈利元_每笔1万']),money(r['避开亏损元_每笔1万']),money(r['取消净贡献元_每笔1万'])] for _,r in atr.iterrows()])
rows=[]
for p in a.policy.unique():
 c=acct('close',p);m=acct('morning7',p)
 rows.append([m['方案'],int(m['买入笔数']),f"{c['账户收益率']:.2%}",f"{m['账户收益率']:.2%}",f"{m['最大回撤']:.2%}",money(m['相对基准账户变化元']),int(m['收盘封死或无量成交未核实数'])])
ac=table(['用途','买入数','原收盘收益','上午7%收益','上午7%回撤','相对同卖法基准变化元','未核实退出数'],rows)
skip=acct('morning7','skip_unified_crash')
extra=float(floor['D3额外估值损失元_每笔1万'].sum())
report=f"""# R73：模型预警的交易得失

本轮在 fin_agent 服务器完成，冻结 R72 的预测与阈值，补做真实 V2 放行候选的经济评价。未重训、未按 2026 结果调阈值。正式 imac/B2_V2、月度停摆和生产逐日记录未改。

## 先纠正分母与判断方法

此前“26 条急跌中抓到 9 条”是全部 120 条 2026 候选，包含停摆月份；召回率 34.62%，不能称准确率。该集合共报急跌 39 条，精确率 23.08%。

V2 真正放行的集合是 98 条：实际急跌 21 条，预测急跌 30 条，其中正确 9、误报 21、漏报 12。预警精确率 30%，急跌召回率 42.86%。98 条是历史真实候选，不代表 98 笔已经真实成交的账户交易。

仅凭这两个比率无法判断交易价值。此前“不具备交易用途”的表述过早；应改成：本轮固定用法在当前成交假设下没有产生净收益增益，且最终可执行结果仍受跌停退出缺口影响。

## 当前卖法：误报究竟损失多少

卖法为 D2 上午涨幅严格超过 7% 后退出，否则 D2 收盘；具体分钟成交代理见下。以下各笔固定投入 1 万元（含买入费用），卖出亦扣 0.1% 费用。金额可相加，便于区分选股得失；这不是连续账户收益率。

{atrtable}

21 条误报中，15 条本来赚钱，6 条本来亏钱。误报的毛机会损失是 11,227.73 元，抵扣避开的 2,519.83 元亏损，净拖累 8,707.90 元。9 条正确预警合计净贡献 1,671.84 元；30 条全部取消净拖累 7,036.06 元。

正确预警不等于避开亏损：这 9 条中仍有 4 条按实际 D1 买价、D2 卖法盈利。三日急跌标签覆盖到 D3，也包括 D2 开盘或收盘触板，标签参照与买入成本并不相同。例如 000566.SZ（D0 2026-07-02）D2 可按分钟代理价 6.60 退出、成本价 6.36，而 D3 收盘才跌到 5.79；不能用 D3 标签把 D2 已退出的盈利当作避亏。

保留原 D2 收盘卖法时，21 条误报原盈利 13、亏损 8：放弃 8,279.26 元、避亏 2,680.35 元，净拖累 5,598.91 元；9 条正确预警净贡献 1,938.60 元，30 条总拖累 3,660.31 元。

按 D0 月归组，有拦截的 7 个月中，上午7%卖法净改善只有 9 月为正，其余 6 个月为负；收盘卖法 2 个月正、5 个月负。这里只是已观察历史的分布，月份和相邻同股交易并非独立证据。

## 连续账户与已有买前模型同口径对照

全部从 2026-01-01 以 100 万元重置，D0 2026-01—09，最后 D2 2026-10-09 结清。沿用空仓时半仓、有一笔持仓时用余款的资金规则。取消候选后不补买其他股票，不重算冻结的影子账户月度停摆信号。收益均是下述成交假设下的历史对照，未修复跌停延期退出。

{ac}

上午7%规则下，“跳过统一模型急跌”的期末金额比基准少 {money(-skip['相对基准账户变化元'])} 元，其中按原账户各笔金额取消的直接贡献为 {money(skip['原仓位取消直接贡献元'])} 元，后续剩余交易仓位变化贡献为 {money(skip['后续仓位变化贡献元'])} 元。不能把固定每笔1万元的结果直接乘一个倍数当作连续账户差额。

只买统一模型“大涨”的 12 笔实际 7 赢 5 输，交易胜率 58.33%，但当前卖法费后笔均仅 1.66%，原98笔为 2.35%；最终账户收益只有 9.52%。这正说明超过55%的胜率也不能单独证明用途有效，还要看盈亏幅度、错过的机会、交易频率和资金占用。

既有四K并集模型在相同98条、相同7%卖法下，取消41条的固定投入净贡献为 +3,604.77 元，账户 +259.20%，相对基准 +64.43万元，回撤 9.86%。这是冻结候选的历史比较，标签、可用信息和模型训练集合与 R72 不同，不能把差异全部归因于某一个特征，也不能据此直接部署。

## 跌停风险价值与未解决的执行问题

统一模型确有风险拦截作用：D2 开盘或收盘跌停从14条降至7条；D2、D3连续收盘跌停从5条降至1条；D2全天封死从4条降至1条。它拦截了较多尾部风险，但同时舍弃了更多盈利机会。

98条中有12条 D2 收盘跌停退出未核实；取消30条后剩6条。原回放把这些收盘卖出视作全成交，所以 +194.77%、+112.32% 等不能称真实可执行收益。分钟有价格、有成交量也不保证指定仓位成交；本轮不使用这类价格路径证明成交能力。

被取消的6条收盘跌停若从D2收盘估值继续看至D3收盘，每笔1万元合计额外损失 {money(extra)} 元；把这一项计入固定投入对照，取消30条的净贡献由 -7,036.06 变为 {money(skip['每笔1万元净改善元']+extra)} 元。该6条D3官方前收均与D2收盘一致，无当日除权价差。D3仍有4条收盘跌停，因此这只是一天估值敏感性，**不是延后退出回测**。相同方法下原D2收盘卖法的净贡献由 -3,660.31 变为 {money(acct('close','skip_unified_crash')['每笔1万元净改善元']+extra)} 元，更说明退出假设足以影响结论。

当前7%卖法下，若这6条的最终额外损失超过7,036.06元，固定投入的拦截净贡献才可能转正；相当于6笔合计6万元原投入再损失11.73%。这不是预计损失，也不能用于推算连续账户：保留的跌停仓位、延期现金占用和后续买入仍需完整重放。故当前证据支持“不能直接把本模型硬接为禁买”，不支持宣称其风险提示永远没有价值。

## 成交、费用与验证口径

- 上午规则：使用完成分钟的最高价相对 D1 收盘（已核对等于D2官方前收）严格超过7%，触发后下一根有量且开价高于跌停价的分钟开盘作为成交代理；不回填到已经过去的最高价，也不保证卖在+7%。本样本41笔触发，均可在紧接下一根分钟找到代理价。其余D2收盘。
- 分钟时间为结束标记；例如09:35完成触发，09:36标记的分钟开盘属于09:35执行时刻。买入沿用既有D1入场价与时刻。卖出先于后续买入时资金可复用；同一时刻按先买后卖保守排序。
- 买卖各0.1%，按可分割股数；未计滑点、冲击和实际整手约束。最大回撤取日末净值，不代表盘中最大回撤。所有方案月初都不充值；仅2026年起点重置一次。
- 98条D2的240分钟完整性、分钟首开末收与日线、前收口径已核对。12套账户现金与成交账恒等式、无负现金、买卖先后均通过。
- 六套原D2收盘方案与既有独立 replay 函数逐日净值完全一致；不是只核最终收益。当前7%触发时点逐笔核对，报告归因由原始价格重新核算。
- 模型使用首板前与首板日信息，在已进入B2候选的D0时点作研究预测。2026历史已被其他探索观察，不属于全新盲测。本轮没有新增模型或阈值选择。

## 文件与登记

- [当前卖法98笔对照](当前卖法_98笔对照.csv)：逐笔原买卖价、预测/真实标签、取消与得失、原账户金额及成交未核实标记。
- [30笔急跌预警明细](急跌预警30笔_误报及得失.csv)：包含21条误报及9条正确预警。
- [方案收益对照](方案收益对照.csv)、[取消得失归因](取消买入得失归因.csv)、[按月拦截得失](按选中月_拦截得失.csv)、[逐月账户](逐月账户对照.csv)。
- [6笔跌停D3估值](被拦截6笔跌停_D3估值敏感性.csv)、[逐笔全部方案](逐笔经济影响.csv)。
- analyze.py / finish.py、results.json / verification.json、12套逐笔成交与逐日现金表和文件哈希保存于本目录；分钟原始文件留服务器，逐个来源哈希在 results.json。
- 关联正式策略 imac/B2_V2，定义 SHA-256 54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00；使用 R69 候选、R72 冻结模型以及此前四K候选。**本轮研究尚未提交/登记生产数据库，未同步云端或回读核验。**不得把本地登记表写成生产入库。
- 服务器目录：{R}。数据计算和验证均在服务器运行；本地仅复制报告与证据。

## 后续模型评价约定

先声明使用方法和真实交易集合，再同时报告分类分母、误拦盈利与避亏、等金额逐笔得失、连续账户变化、风险暴露及可成交边界。标签预测好坏与交易用途好坏分别评价；55%不是通用合格线。不得仅用准确率、AUC或召回率给交易价值下结论，也不得用未证实的成交假设称真实收益。
"""
(R/'README.md').write_text(report)
print('written report and compact CSV; verification passed')
print('D3 sensitivity net',skip['每笔1万元净改善元']+extra)

