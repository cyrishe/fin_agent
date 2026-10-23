from pathlib import Path
import pandas as pd,numpy as np,json,hashlib,platform,subprocess
R=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022')
ac=pd.read_csv(R/'2026当前卖法_组合得失.csv').set_index('方案')
eco=pd.read_csv(R/'经济目标与过滤强度_完整对照.csv')
f=pd.read_pickle(R/'panel306.pkl')
names={'baseline':'未加买前风险过滤','union_early_lr01':'D1原买前模型 Q75','fit_D1_news':'D1＋公告 Q75','fit_D1_flow':'D1＋资金 Q75','fit_D1_news_flow':'D1＋公告＋资金 Q75','fit_sens_D1_q80':'D1原买前模型 Q80','fit_sens_D1_flow_q80':'D1＋资金 Q80','fit_sens_D1_news_flow_q80':'D1＋公告＋资金 Q80'}
def md(columns,rows):
 return '\n'.join(['| '+' | '.join(columns)+' |','| '+' | '.join(['---']*len(columns))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
table=md(['方案','过滤笔数','少赚／元','避亏／元','净贡献／元','账户收益','最大回撤'],[[label,int(ac.loc[k,'取消整笔']),f"{ac.loc[k,'少赚元_每笔1万']:,.0f}",f"{ac.loc[k,'避亏元_每笔1万']:,.0f}",f"{ac.loc[k,'净贡献元_每笔1万']:+,.0f}",f"{ac.loc[k,'账户收益率']:.2%}",f"{ac.loc[k,'最大回撤']:.2%}"] for k,label in names.items()])
val=md(['方案','2025过滤数','2025净贡献','2026过滤数','2026净贡献'],[[label,*sum(([int(q.alerts),f'{q.net_1w:+,.0f}'] for split in ['valid','test'] for q in [eco[(eco.family=='floor_probability')&(eco.policy.eq(policy))&(eco.split.eq(split))].iloc[0]]),[])] for policy,label in [('D1_q75','D1 Q75'),('D1_q80','D1 Q80'),('D1_flow_q75','D1＋资金 Q75'),('D1_flow_q80','D1＋资金 Q80'),('D1_flow_q90','D1＋资金 Q90'),('D1_news_flow_q75','全组合 Q75'),('D1_news_flow_q80','全组合 Q80'),('D1_news_flow_q90','全组合 Q90')]])
stress=[];br=ac.loc['union_early_lr01']
for k,label in names.items():
 r=ac.loc[k];delta=r['净贡献元_每笔1万']-br['净贡献元_每笔1万'];sd=delta+r['D3额外估值损失减少_非成交']-br['D3额外估值损失减少_非成交']
 stress.append({'方案':k,'相对D1净贡献_当前卖价假设':delta,'相对D1净贡献_未核实退出按D3收盘估值':sd,'剩余连续跌停笔数':5-r['捕获连续跌停'],'未核实退出数':r['未核实退出整笔数']})
pd.DataFrame(stress).to_csv(R/'延期估值敏感性_非成交.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
st=md(['方案','当前卖价下相对D1','D3估值下相对D1','仍买入连续跌停'],[[names[r['方案']],f"{r['相对D1净贡献_当前卖价假设']:+,.0f}",f"{r['相对D1净贡献_未核实退出按D3收盘估值']:+,.0f}",int(r['剩余连续跌停笔数'])] for r in stress if r['方案'] in ['fit_D1_flow','fit_D1_news_flow','fit_sens_D1_flow_q80','fit_sens_D1_news_flow_q80']])
report="""# R75：消息、资金与买前过滤

## 结论

目标是提高避亏后的净收益，兼顾误拦盈利和风险暴露，不要求抓住全部跌停。**资金与买前量价组合有值得保留的增量线索，公告更适合区分具体事件；目前不能认定跨年份稳定增益。** 不能因漏掉一笔连跌就否定，也不能用2026账户最高收益直接选型。

- D1＋资金 Q75：2026取消41笔，每笔按1万元，少赚10,549.82元、避亏15,276.12元，净贡献4,726.30元，比原D1增加1,121.52元。不是只增加过滤：新增5笔、恢复5笔。
- 全组合 Q75：净贡献5,169.36元，比原D1增加1,564.59元。两者均保留为研究候选；2025同门槛取消净贡献分别为−802.10和−2,157.82元，不能直接替换。
- **降低误拦频率也有线索：** Q80的D1＋资金和全组合在2025、2026原收盘口径下均有正贡献；2026当前7%卖法也为正。更严格到Q90却又出现2025好、2026差，收益不随过滤强度单调变化。
- 具体题材否认仅3笔入选历史、2026仅1笔，不足以证明稳定有效。公告数量或主力净流出直接“一票否决”会错过不少盈利。
- 未核实跌停退出改按D3收盘估值后，Q75资金组合相对原D1优势仅约190元，全组合约181元。应保留线索继续验证，不能把原假设的收益优势当作已经解决连跌风险。

本轮全部查询、训练、回放、特征加工及验证在服务器完成；本地为归档副本。未改正式V2、生产数据库或定时任务。

## 1. 先看经济结果

2026年98笔V2放行交易，D0至2026-09，D2结清至2026-10-09。每笔1万元栏为固定投入的货币损益合计：**净贡献＝避开亏损－放弃盈利**。它不是连续账户收益率。

账户从2026-01-01一次性初始化100万元；无持仓时半现金买入，有持仓时用剩余现金，双边各0.1%，分数股。固定原候选与原月度影子开关，过滤后不补选其他股票。D2上午完整分钟高点超过7%后取后续可用分钟开盘成交代理，否则收盘。沿用R73价格假设，仍未修复收盘跌停全额卖出的执行缺口。

"""+table+"""

Q75／Q80为风险评分严格高于**训练集**75／80分位数才过滤。不是“过滤25%／20%的未来交易”；分布变化后2026实际过滤比例更高。D1基线为已保留的 union_early_lr01 研究模型，并非宣称这层风控已经正式上线。

每个候选的买卖记录、逐日净值、取消盈利、避免亏损、原仓位贡献及后续仓位变化均已保存。97套账户现金对账通过，未加过滤的逐日净值与R73一致。

### 减仓用途

全组合Q75仅减半被提示交易、保留全部98笔时，当前假设下账户+279.96%、回撤11.70%；全部取消提示交易为+286.96%、10.90%。D1＋资金减半为+242.01%、13.90%。减半可以少放弃盈利，也保留一半亏损；账户回撤不必位于全买和全过滤之间，因为后续现金仓位会变化。表中的“捕获”对半仓方案只表示被提示并减仓，不能称完全避开，另列剩余风险仓位合计。

## 2. 跨年份与门槛

以下2025／2026均用**原D2收盘卖法**，确保价格口径一致；与上一表2026上午7%卖法不能混作同一列。单位仍为每笔1万元的取消净贡献。训练152笔（2022—24），验证54笔（2025），观察98笔（2026），跨年标签剔除2笔。

"""+val+"""

本轮完整保存六组风险模型×5个训练分位门槛，未只展示赢家。按2025净贡献在这30个候选里选择，获选的是D1 Q80；它2026当前7%净贡献1,917.70元，反而低于原D1 Q75的3,604.77元。Q80组合是值得保留的敏感性线索，不是事后挑中的新定版。

第一阶段六组C0.1逻辑回归统一预测D2开盘或收盘跌停：D1、公告、资金、D1＋公告、D1＋资金、全组合。原Q75验证筛选只有单独资金组达到最低绝对门槛，但未胜过D1，2026也弱；不据此部署。完整阈值敏感性另列，未覆盖或改写第一阶段记录。

### 直接按交易得失训练

又按用户澄清的经济目标补了4组模型：预测原收盘费后亏损，用训练期绝对盈亏幅度加权；误拦大赚、漏过大亏的代价更高。固定C0.1与相同特征，比较0.5及训练Q60/Q70/Q80/Q90共20个动作。

仅按2025货币贡献选择，全组合Q80以+3,431.38元获选；2026原收盘为−3,233.89元，当前7%为−4,061.07元，未带来可靠改善。这说明该训练方式在现有小样本中没有稳定转化，不是否定资金或消息本身。20个候选和其完整结果全部保留。

2026此前已被多轮研究观察，不是新的盲测；增加门槛及20个经济目标动作也增加选择不确定性。模型替换相对原D1的月份聚类区间均跨0；不能据此宣布稳定胜率或显著增益。

## 3. 漏掉风险的代价，要看多少钱

目标不要求全抓住。下面把未核实D2成交的交易统一改按D3收盘估值，再计算固定1万元下相对原D1的差异。不是新成交价，也没有假定D3必能卖出；D3继续封死仍为未平仓风险。

"""+st+"""

Q75的资金组合新增过滤5笔：少赚459.83、避亏1,678.06、净+1,218.24元；同时恢复5笔买入，合计−96.71元，最终相对D1净+1,121.52。恢复名单含恒尚节能2026-07-14。全组合新增过滤9笔、恢复8笔，二者必须一起评价。

这组D3估值只说明优势对延期损失敏感，不能替代真实延迟退出账户；尚未包括更后续封板、现金占用、排队容量及月度影子反馈。原D1抓到2026全部5笔连续跌停也不是目标或保证：其中恒尚两笔属于同一段重叠风险，独立事件更少。

## 4. 资金的实际线索

读取 kcrp_stock_moneyflow，525只股票、39,739条原始日记录，709候选D0全部有记录。金额单位元。用净额／当日各档买入成交额合计标准化，避免大市值公司金额天然更大；同时保存净额／本档买卖额、前5／20日均值、相邻5日趋势、净流出天数。量能和体量沿用D1原23因子的市值、换手、连板数、相对常态成交量、D0封板与尾盘成交、D1前四根分钟K。

核对主力净额＝超大单净额＋大单净额；三者不能当作三张独立赞成票。买卖金额总差只有舍入量级，标准化与供应商百分比口径一致。订单大小分类不等于真实识别“主力赚够了想逃”或“机构避险”，本轮只检验可观察流向。

2026：

- 16笔D0主力净流出：D2开收跌停4笔，连续收盘跌停3笔。
- 82笔非净流出：分别10笔、2笔。
- 但2025方向不同：净流出20笔仅2笔D2风险，非净流出34笔8笔风险。不能直接把净流出设成禁买。
- 在原D1之外再叠加“主力净流出”新增过滤8笔，少赚6,721.12、避亏804.46，净−5,916.66元。**有风险区分，不代表过滤划算。**
- 2026连续跌停5笔，D0主力净额占成交额中位数−2.48%、大单−4.38%、小单+5.16%；其余93笔分别+13.44%、−1.45%、−5.28%。这是“价格仍强、资金结构转弱”的候选线索；5笔样本很少且存在重叠，不能作主力动机结论。

保存D−10至D+3逐日轨迹。D1至D3全天资金列明确标为POST，仅用于事后解释，全部排除训练。连续跌停组D2主力占比中位数约−34.55%，这已是下跌当日表现，不能倒放到买前预测里。

### 资金可用时点

D0全天资金不能在D0 15:00即时使用：306行最新记录均晚于该时点。适合放在D1买前，实际采集成功后再读。

按当前行创建和更新时间，训练152笔仅37笔版本时间不晚于D1 09:34；2025为54/54、2026为97/98。2022／23存在后补历史；2026亚盛2026-03-11的资金行更新在D1之后。**当前资金训练是历史快照探索，不是已严格证明当时可得的回测。** 已保存原始时间、缺失与更晚更新标记；时间戳符合也不等于已取得供应商不可变历史版本。未来实时首次采集快照是可操作的解决办法，本轮没有部署。

## 5. 消息面：具体内容、频度和趋势

覆盖709候选，D0截止15:00；日期型公告保守按次日可用。近5日严格取截至D0的5个交易可用日，前5日取紧邻的前一段5日，另有近3对前3。公告在非交易日可用则映射下一交易日。这和R74“D0加此前5日”及D1盘前截止口径不同，不能直接比较命中数。

统一补齐所有异动／澄清类公告正文共987份目标，其中920新下载、67复用已有；未仅针对坏案例补正文。提取公告篇数、去重发布日数、风险提示日数、异动日数、标题族新颖度及相邻窗口变化。标题族只是去除日期等后的标题归一，不等同完整事件去重。

这轮频度是**公告活跃度**，并非完整媒体报道热度。此前新浪仅30窗口完整、306公开搜索也不等于全量媒体语料；未覆盖不能按0。不能声称“新闻报道次数趋势已在全样本验证”。

2026当前卖法：
- 公告日数近5≥3且比前5增≥2，12笔提示；其中5笔与D1重叠，新增7笔净−3,146.28元。公告升温可能伴随活跃交易，直接过滤会错失利润。
- “反复风险提示”在D0严格新窗口下3笔，都与D1重叠；没有新增保护，不等于其信息没有价值。
- 具体题材否认只有亚盛1笔，取消贡献+892.87元；泛化措辞新华文轩1笔，+804.46元。训练期具体否认2笔合计反而−1,015.65元，验证期0笔。
- 因此不能下结论“否认热点是一条已经稳定盈利的消息规则”，也不能因频度单独过滤较弱就丢弃其与量价的交互线索。

具体例子：[亚盛公告](https://static.cninfo.com.cn/finalpage/2026-03-04/1224993935.PDF)否认矿产相关业务；[新华文轩公告](https://static.cninfo.com.cn/finalpage/2026-09-23/1225577351.PDF)为泛化热点措辞。两种语义分开保留，并保存每一条原句、链接及可用时间。“未涉及重大资产重组、发行等”的常规无重大事项声明也归泛化，不能误当具体题材否认。

## 6. 保留什么、怎样继续判断

本轮保留D1＋资金Q75/Q80、全组合Q75/Q80及减仓动作作为研究候选；同时保留失败模型、所有门槛及最初正文覆盖版本。它们的用途是比较经济取舍，不是自动否决线或新正式版本。

下一步可检验的方向：

1. 量价强度相同条件下，主力占比相对自身前5日恶化、小单接盘、量比变化是否仍有增量；目前线性组合显示一些线索，尚未证明交互和因果。
2. 具体事件的新信息、重复提示、密集报道与D0涨停及D1承接是否一致；完整媒体样本未补齐前应分别管理可观测公告与未知媒体量。
3. 冻结候选及阈值，按未用于挑选的新时期收集逐日快照；用实际未成交持仓延后退出、等金额货币贡献、连续账户共同判断。允许漏掉一些风险，但要看其金钱代价是否值得。

## 7. 文件、核验与登记状态

- [306笔简表](306笔_量能消息资金与模型.csv)：中文表头、量能／消息／资金／5组过滤标记、分期和收益。
- [709候选完整特征](709候选_消息与资金.csv)、[20笔连跌事前线索](20笔连跌_事前线索.csv)。
- [当前卖法全部97套动作](2026当前卖法_组合得失.csv)、[全部门槛和经济训练结果](经济目标与过滤强度_完整对照.csv)。
- [新增过滤与恢复买入](模型换单_新增过滤与恢复买入.csv)、[完整替换增量及区间](替换D1的完整增量与不确定性.csv)、[延期估值敏感性](延期估值敏感性_非成交.csv)。
- [公告逐条时点](公告近5日_逐条可用时点.csv)、[否认原句](否认热点_逐句复核.csv)、[D0前后资金轨迹](逐笔D0前后资金路径_事后列隔离.csv)。
- verification.json：10模型重载、原D1分数复现、709消息窗口时点、资金恒等式、97账户现金、原R73净值、CSV无前导单引号核验通过。数值写为数字，不加单引号；比例列为小数（0.1＝10%），名称含“百分点”的原字段保持原单位。
- 所有训练样本／分期／模型／阈值／预测／代码／原始资金／正文均已保存，源码与资产哈希见manifest.json；R74原始公告索引等外部依赖见source_manifest.json。

关联正式 imac/B2_V2 SHA-256：54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00。registration_payload.json已准备，**本轮研究未提交生产登记、未入生产库、未完成云盘回读**。不以报告代替数据库归档，不静默升级策略。

服务器：/data/cyris/work/b2_news_flow_combo_r75_20261022。复现依次执行 fetch_flow.py（只读）、build_features.py、fit_combo.py、economic_extension.py、evaluate_actions.py、flow_diagnostics.py、finalize.py、report.py。expand_documents.py仅为本轮一次性覆盖扩展，复现时使用保存的正文，不重复覆盖initial_coverage。
"""
(R/'README.md').write_text(report)
# Registration material: link immutable parent, data windows and every model, without any DB mutation.
mi=json.loads((R/'model_info.json').read_text());ei=json.loads((R/'economic_selection.json').read_text())
models=[]
for family,groups in [('floor',mi),('money_weighted',ei['models'])]:
 for name,v in groups.items():
  file=('model_' if family=='floor' else 'economic_')+name+'.joblib'
  models.append({'family':family,'id':name,'file':file,'sha256':hashlib.sha256((R/file).read_bytes()).hexdigest(),'features':v['features'],'cutoffs':v.get('thresholds',v.get('threshold'))})
windows={split:{'count':int(len(x)),'D0_min':str(x.d0_date.min()),'D0_max':str(x.d0_date.max())} for split,x in f.groupby('split')}
payload={'experiment':'B2_R75_NEWS_FLOW_COMBO_20261022','parent_strategy':'imac/B2_V2','definition_sha256':'54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00','parent_runs':['B2_D1_RISK_20261021','R71','R73','R74'],'sample_count':709,'selected_count':306,'windows':windows,'new_models':models,'signal_join_key':'stk_code|d0_date','production_registration':'not_submitted','cloud_readback':'not_done','server_artifact_root':str(R),'source_manifest':'source_manifest.json','asset_manifest':'manifest.json','account_records':'*_ledger.csv and *_daily.csv','predictions':'predictions306.pkl','historical_flow_vintage':'incomplete; not production-qualified'}
(R/'registration_payload.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
deps=[Path('/data/cyris/work/b2_news_factor_r74_20261022')/p for p in ['cohort709.pkl','classified_official_documents.json','feature_rules.json','official_docs.json']]
deps+=list(Path('/data/cyris/work/b2_news_factor_r74_20261022/cninfo_windows').glob('*.json'))
deps+=list(Path('/data/cyris/work/b2_news_factor_r74_20261022/documents').glob('*.txt'))
deps+=[Path('/data/cyris/work/b2_model_economics_r73_20261022/joined_98.pkl'),Path('/data/cyris/work/b2_model_economics_r73_20261022/morning7_baseline_daily.csv'),Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs/calendar.json')]
(R/'source_manifest.json').write_text(json.dumps([{'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in deps if p.is_file()],ensure_ascii=False,indent=2))
try:commit=subprocess.check_output(['git','-C','/data/cyris/fin_agent_deploy/b2-20261015','rev-parse','HEAD'],text=True).strip()
except Exception:commit='unavailable'
(R/'code_provenance.json').write_text(json.dumps({'server_base_commit':commit,'research_code_version':'hashes of scripts in manifest.json; research scripts outside base commit','execution':'server only','runtime':json.loads((R/'verification.json').read_text())['runtime']},ensure_ascii=False,indent=2))
files=[p for p in R.rglob('*') if p.is_file() and p.name not in ['manifest.json','local_copy_verification.json']]
(R/'manifest.json').write_text(json.dumps([{'path':str(p.relative_to(R)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)],ensure_ascii=False,indent=2))
print('report, registration material and manifest saved',len(files),'files')
print(pd.DataFrame(stress).to_string(index=False))

