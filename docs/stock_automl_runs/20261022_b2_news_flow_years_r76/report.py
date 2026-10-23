from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
R=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
a=pd.read_csv(R/'逐年独立重置_全部方案.csv')
b=pd.read_csv(R/'全期连续账户_全部方案.csv')
c=pd.read_csv(R/'全期连续账户_逐自然年.csv')
s=pd.read_pickle(R/'joined_306.pkl')
names={'baseline':'原V2候选全买','union_early_lr01':'原D1风控 Q75','fit_D1_flow':'D1＋资金 Q75','fit_D1_news_flow':'D1＋公告＋资金 Q75','fit_sens_D1_q80':'原D1风控 Q80','fit_sens_D1_flow_q80':'D1＋资金 Q80','fit_sens_D1_news_flow_q80':'D1＋公告＋资金 Q80','fit_D1_news':'D1＋公告 Q75','fit_econ_D1_news_flow_q80':'交易得失加权组合 Q80'}
main=['baseline','union_early_lr01','fit_D1_flow','fit_D1_news_flow','fit_sens_D1_q80','fit_sens_D1_flow_q80','fit_sens_D1_news_flow_q80']
aa=a[a['卖法'].eq('morning7')].set_index(['方案','D0年'])
bb=b.set_index(['方案','卖法'])
cc=c[c['卖法'].eq('morning7')].set_index(['方案','自然年'])
def md(head,rows):return '\n'.join(['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |']+['| '+' | '.join(map(str,x))+' |' for x in rows])
accounts=[]
for name in main:
 rows=[aa.loc[(name,yr)] for yr in range(2022,2027)]
 accounts.append([names[name]]+[f"{x['账户独立重置收益率']:+.2%}" for x in rows])
profit=[]
for name in main[1:]:
 rows=[aa.loc[(name,yr)] for yr in range(2022,2027)]
 profit.append([names[name]]+[f"{x['过滤净贡献元_每笔1万']:+,.0f}" for x in rows])
full=[]
for name in main:
 r=bb.loc[(name,'morning7')];e=bb.loc[(name,'close')]
 end2025=cc.loc[(name,2025)]['年末账户元']/1e6-1
 full.append([names[name],int(r['买入笔数']),f"{end2025:+.2%}",f"{r['账户全期连续收益率']:+.2%}",f"{r['账户全期最大回撤']:.2%}",f"{e['账户全期连续收益率']:+.2%}",int(r['剩余未核实退出数'])])
summary=[]
for yr in range(2022,2027):
 for name in main:
  x=aa.loc[(name,yr)]
  summary.append({'D0年':yr,'样本分期':x['分期'],'放行候选':int(x['候选笔数']),'方案':names[name],'过滤':int(x['过滤笔数']),'保留':int(x['买入笔数']),'少赚元_每笔1万':x['放弃盈利元_每笔1万'],'避亏元_每笔1万':x['避开亏损元_每笔1万'],'净贡献元_每笔1万':x['过滤净贡献元_每笔1万'],'独立重置账户收益率':x['账户独立重置收益率'],'独立重置账户最大回撤':x['账户独立重置最大回撤'],'仍买入连续跌停':int(x['剩余连续跌停数']),'剩余卖出未核实':int(x['剩余未核实退出数'])})
pd.DataFrame(summary).to_csv(R/'年度对比_简表.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
# D3 mark-to-market sensitivity: only an equal-ticket diagnostic, never a delayed-fill account.
stress=[]
for yr in range(2022,2027):
 base=aa.loc[('union_early_lr01',yr)]
 for name in main[2:]:
  q=aa.loc[(name,yr)]
  current=q['过滤净贡献元_每笔1万']-base['过滤净贡献元_每笔1万']
  marked=current+q['过滤D3额外估值损失减少元_非成交']-base['过滤D3额外估值损失减少元_非成交']
  stress.append({'D0年':yr,'方案':names[name],'对D1Q75净贡献_原卖价':current,'对D1Q75净贡献_D3非成交估值':marked,'仍买入连续跌停':int(q['剩余连续跌停数']),'未核实退出数':int(q['剩余未核实退出数'])})
pd.DataFrame(stress).to_csv(R/'逐年D3估值敏感性_非成交.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
report="""# R76：五个年份按同一卖法回放

## 为什么上轮只有2026

R75沿用了R73已核实分钟卖价和账户的98笔2026交易，直接给了2026当前“上午超过7%卖、否则收盘”的经济对照。R75还给出2025等年份的**原D2收盘价**逐笔贡献，但没有把那些年份按当前卖法和账户规则接起来。这是上轮展示不完整，本轮已补齐。

从冻结的V2放行集合重算全部306笔：2022／2023／2024／2025／2026分别35／65／54／54／98笔。**按D0年份归属；2022从5月开始，2026的D0止于9月、D2结清至10月9日。** 2024含52笔训练和2笔跨年剔除。固定同一笔买价、上午7%条件、两侧各0.1%费用及原有月度停摆结果，过滤后不补票。

306笔的完整D2分钟已核对日线OHLC；125笔复用R73、78笔复用R67、71笔复用R69、32笔从保存的压缩档案读取，其中11个缺口档案由本机原始压缩文件传到服务器，所有分析计算都在服务器。全样本上午真正触发7%的为137笔。2026九个方案的分钟退出、逐笔成本及账户均与R73/R75逐项一致。原D2收盘全期基线+161.07%／回撤38.56%，与此前定版V2全期约+161.07%／38.56%对齐；有舍入差异。

## 各年独立账户收益

每个D0年份的交易单独用100万元初始化，至该组最后一笔D2结清；跨年最后一笔仍归原D0年份。账户按事件时间回放：空仓投入一半可用现金，有持仓用剩余现金，同日卖出后资金才可再买；双边费用各0.1%，日末持仓按D1收盘估值。**不同年份的收益不能相加，全年含停摆月份和无交易日。**

"""+md(['方案','2022 (35笔)','2023 (65笔)','2024 (54笔)','2025 (54笔)','2026 (98笔)'],accounts)+"""

2025是关键反例：原D1 Q75收益+45.50%，D1＋资金Q75只有+13.49%，全组合Q75只有+10.59%；所以不能凭2026 +277.94%／+286.96%替换原D1。提高门槛的D1＋资金Q80在2025为+50.57%、2026为+261.89%，均略高于D1 Q75的+45.50%／+259.20%；但2023明显弱于D1，且2025原D1自身Q80达到+74.72%，说明“门槛”与“资金因子”的贡献不能混为一谈。全组合Q80在2025为+26.51%，仍弱于D1。

2022—24这些行主要是模型训练样本的**样本内回放**，不能当成模型对未知年份的预测成绩。2025参与验证／阈值研究，2026历史也已被多轮研究观察，均不能称新的盲测。

## 逐笔经济得失

以下为每笔固定投入1万元：过滤后少赚的盈利、避开的亏损分别累计，**净贡献＝避亏－少赚**。表中只放净贡献；两部分金额、过滤笔数、剩余跌停及账户回撤见[年度对比简表](年度对比_简表.csv)和[完整逐年表](逐年独立重置_全部方案.csv)。这是等投入交易得失，不能直接当账户收益。

"""+md(['方案','2022','2023','2024','2025','2026'],profit)+"""

例如D1＋资金Q75在2025过滤20笔，放弃盈利10,544.87元、避亏9,128.43元，净−1,416.44元；2026同门槛净+4,726.30元。D1＋资金Q80在2025过滤14笔，放弃4,854.63元、避亏9,023.64元，净+4,169.01元。降低误拦的确能改善2025，但不能只挑2026表现最好的一段。

## 全期连续账户

另一套账户从2022年首批信号前以100万元初始化，中途**不在年初重新充值**，一直结清到2026-10-09。“截至2025”显示2026强势期进入前的净值变化；“全期”包含2026，因此不能把两者当作独立实验。收盘卖一列只用于同起点核对历史结果。

"""+md(['方案','保留交易','2022—25累计','2022—26累计','最大回撤','原D2收盘全期','卖出未核实'],full)+"""

这些高复利数字受到2022—24样本内预测、历史资金快照、固定账户投入与跌停成交假设共同影响。它们是研究用的**可复算账户回放**，不是能按历史真实成交的收益。全期后段2026贡献很大；逐自然年在[连续账户逐年表](全期连续账户_逐自然年.csv)。

## 风险代价与数据可用性

全306笔中，当前卖法仍有50笔收盘跌停或末分钟无量，卖出成交未核实。逐年基线分别为10／6／12／10／12笔；原D1 Q75过滤后剩6／2／5／3／4笔。表中账户仍按收盘全额成交计价。未核实退出进一步按D3收盘估值的等投入敏感性见[逐年D3估值表](逐年D3估值敏感性_非成交.csv)；D3仍可能无法卖出，且尚无后续资金占用与影子停摆反馈的执行账户。

2022—24资金记录主要是后补历史快照；R75核验只有训练152笔中的37笔当前版本时间不晚于D1买前，2025为54/54、2026为97/98。因此资金模型的早年回放尤其不能视为严格当时可用，2025／2026也未取得供应商不可变历史版本。公告日期的可用时点按R75口径。正式「涨停冷静V2—月度停摆」及生产模型不因本轮更改。

## 资产

- [306笔同卖价与触发分钟](306笔_全年同卖法价格.csv)、[306笔逐方案经济归因](306笔_逐方案经济归因.csv)。
- [年度对比简表](年度对比_简表.csv)、[完整逐年账户](逐年独立重置_全部方案.csv)、[全期连续账户](全期连续账户_全部方案.csv)、[全期连续账户逐年](全期连续账户_逐自然年.csv)。
- 逐方案逐日净值及逐笔账户在同目录；脚本prepare.py、replay.py；minute_source_manifest.json记录每笔分钟来源和哈希，verification.json记录306分钟价格与2026九方案的独立对账，manifest.json为研究资产哈希。
- CSV数值不加前导单引号；比率字段存数值型小数（如0.1表示10%），供Excel直接计算。

服务器研究目录：/data/cyris/work/b2_news_flow_years_r76_20261022；本地为副本。关联正式imac/B2_V2定义SHA-256 54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00。本轮没有新训练、没有更改生产数据库或定时任务；登记材料已准备，尚未提交生产库或完成云盘回读。
"""
(R/'README.md').write_text(report)
payload={'experiment':'B2_R76_NEWS_FLOW_ALL_YEARS_20261022','parent_strategy':'imac/B2_V2','definition_sha256':'54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00','parent_experiment':'B2_R75_NEWS_FLOW_COMBO_20261022','sample_count':306,'per_D0_year':s.d0_year.value_counts().sort_index().to_dict(),'new_training':False,'replay_modes':['D2_close','morning7_next_minute_open_else_D2_close'],'account_modes':['independent_1m_per_D0_year','single_continuous_1m_from_2022'],'policy_count':9,'source_manifest':'minute_source_manifest.json','verification':'verification.json','production_registration':'not_submitted','cloud_readback':'not_done','server_artifact_root':str(R)}
(R/'registration_payload.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
files=[p for p in R.rglob('*') if p.is_file() and p.name not in ['manifest.json','local_copy_verification.json']]
(R/'manifest.json').write_text(json.dumps([{'path':str(p.relative_to(R)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)],ensure_ascii=False,indent=2))
print('FILES',len(files),'REPORT',len(report),'ALL306',len(s))
print(md(['方案','2022','2023','2024','2025','2026'],accounts))
print(md(['方案','2022','2023','2024','2025','2026'],profit))
print(md(['方案','保留','2025末','全期','回撤','原收盘','未核实'],full))

