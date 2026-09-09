# 多资产业务 Skill 扩展与验证（2026-09-08）

## 交付范围

本轮是业务 SOFT 方法与评测资产改动：不新增运行时协议、状态、权限层、业务校验器或查询工具。没有提交、推送、部署或生产数据操作。共享工作区其他任务的运行时及测试改动不属于本轮交付。

去重后共 15 个系统业务 Skill。新增方法复用既有 Catalog、显式 `$skill-id` 入口、权限、版本快照及按需参考加载；不会获得额外工具权限。system/public/private 的现有归属可见性机制未改变，新增内置方法为 system/public。

| 处理 | 入口 | 方法与典型用途 |
|---|---|---|
| 增强已有 | `stock-comparison` 股票对比分析 | 同业、跨行业、不同成长阶段、单一指标和机构预期比较；根据不可比项、现金背离等发现调整路径 |
| 增强已有 | `sector-theme-analysis` 热点板块与行业概念分析 | 热点发现、股票反查主题、日内/跨日演变、成分扩散、板块比较、业务传导 |
| 新增 | `fund-analysis` 基金分析 | 默认身份与证据盘点；表现与交易、产品与同类比较分支；ETF 折溢价及材料充分时的持仓、费用、经理分析 |
| 新增 | `bond-analysis` 债券分析 | 默认行情与条款盘点；收益信用流动性、普通债比较、可转债股债关系及赎回分支 |
| 新增 | `capital-flow-analysis` 资金流与交易结构分析 | 个股/板块资金持续性、价量背离、资金集中；按需进入两融与股东披露 |
| 组合优化 | `stock-research`、`equity-report-analysis` | 以尚未解决的研究问题决定组合，不因出现多个维度词便展开所有方法 |

其他保留入口：`market-overview`、`earnings-analysis`、`stock-screening`、`factor-analysis`、`valuation-analysis`、`financial-quality-analysis`、`technical-structure-analysis`、`dividend-analysis`。

没有另建“ETF 分析”“基金对比”“可转债分析”“概念股分析”等重复入口。它们归入相应总 Skill 的参考子方法；这里的子方法是按需加载的业务指导，不是新的独立 Agent 或固定 DAG。Agent 仍持有取证、方法组合和最终回答。

## 统一方法与数据边界

入口描述采用正向的“对象 + 用户问题 + 核心能力 + 条件性深化”，不通过禁止选择其他 Skill 划分地盘。主文件给出默认路径、参考加载条件和输出要求，详细业务指导放在 `references/`。此结构应用了 skill-creator 的简洁描述与渐进加载建议，未为文案新增 HARD 字段。

数据核对依据：`src/tools/finance_data/catalog/api_view_catalog.json`，目录版本 `2026-09-08-unified-operations-v5`。目录提供能力不等于每只标的有非空、完整、同口径数据；本轮没有访问生产行情库验证覆盖率。

- 基金当前明确开放身份、行情、单位净值等。`discount` 是“净值减收盘价”的金额，不是折溢价百分比；`unit_total` 是份额数，不是基金资产。日期不匹配时先核验，原始价格/净值涨跌不冒充含分红再投资总回报。基金经理、费用、持仓与完整跟踪基准不在现有公开字段中，须另有可信材料。
- 债券身份只保证代码、名称、发行人，不能凭基础接口简介假定已有券种、到期日。现有行情不提供完整票息、现金流、净价/全价、评级、久期或转股条款。收益率、久期、转股价值仅在输入与授权确定性计算齐备时计算；缺失不等于零值。公告风险分析不假定用户持仓成本，也不承诺某个操作可以避免损失。
- 热点基础、状态、关联股票分别取证；同一日多快照不是多日持续；返回部分关联股不能推出完整扩散或集中；关系与龙头标签不直接证明订单、利润或竞争优势。
- 资金字段反映源定义的成交分类，不标识真实机构。流量、余额、比例、披露期分开；未提供主力分类包含关系时不补公式。行业没有同名资金视图，按实际板块或成分聚合能力取证。

金融方法参考主要用于概念而非套用境外规则：[SEC ETF 投资者说明](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24)支持交易价格与净值及费用区分；[FINRA 债券收益与回报](https://www.finra.org/investors/insights/bond-yield-return)、[债券尽调](https://www.finra.org/investors/insights/bond-investing-due-diligence)支持现金流、收益定义和风险核验；[上交所历史可转债说明](https://edu.sse.com.cn/service/hotline/qa/c/5151728.shtml)仅用于转股与赎回风险概念，不能替代当前单券公告与有效规则。

## 自动化回归

执行：

```bash
.venv/bin/python -m pytest -q tests/test_finance_multi_asset_skills.py tests/test_finance_business_skill_catalog.py tests/test_finance_business_skills.py tests/test_finance_business_default_skills_v2.py tests/test_stock_research_composition.py
```

结果 **55 passed**，1 条既有 `python_multipart` 弃用告警。覆盖 15 个唯一入口、Catalog 描述一致性、新方法显式上下文、参考需先加载父方法、冻结版本内容/哈希、子方法资源可达和不额外授权工具。不是全仓回归或前端人工验收。

## 真实模型入口测试

固定样本：`tests/evals/finance_multi_asset_selection_v1.json`，12 个自然语言问题，无显式 Skill 指定。既有脚本捕获真实服务首轮请求后隔离重放模型，只记录选择，不执行返回的业务工具。gold 不进入模型上下文。

初测证据：`outputs/finance_multi_asset_selection_20260908/`，其中 `manifest.json` 绑定模型、commit、方法 revision 和来源哈希；每题保留 request、response、捕获事件和判分结果。

- 核心 Skill 命中 **12/12**；整体选择符合预先允许集合 **11/12**。
- 个股综合问题额外加载了未在该题允许集合中的业绩方法。保留原始预期，不事后改 gold。
- 人工检查另发现 4 题附带目录调用参数不合法：`flow_plate`、`hot_reverse`、`stock_regression`、`report_regression`，包括将公司/板块名称填入 `subject`、把主体填为 `dataview` 等。现有 scorer 允许目录调用但不校验其业务参数，因此 **11/12 不是完整工具调用有效率**。
- 实际业务工具执行数 **0**。结果证明入口具备被选中的能力，不能证明后续查询、子方法动态选择或最终分析端到端通过。
- 测试期间共享工作区有外层目录披露流程改动，不与此前 18 题历史分数直接比较。初测方法 revision 为 `799f3bc2a8ac4ac8c667a465bd49348a3e2bcfd63c3ac2fba07df7a1f08c4a36`；之后仅调整了参考业务指导，入口描述保持不变。

修订后另跑新增三类代表题，证据放在 `outputs/finance_multi_asset_selection_20260908_v2/`：核心命中及允许选择均 **3/3**，分别仅选基金、债券、资金方法。附带目录参数人工检查与现有主体/视图/operation 定义一致；没有实际派发验证。该轮 commit 为 `1dfb8ae2ff0efa101023781d3ac059f75f6276ae`，方法 revision 为 `8f8f95042eaec127ee32f22b29db9b34c537be5436a6a653a5012f6bf5631f77`，模型 `deepseek-v4-flash-0731`，记录的来源哈希结束时仍一致。期间外层工具说明有其他任务更新，因此三题改善不归因于本轮 Skill 参考修订。

## 合成证据下的方法效果

固定输入：`tests/evals/finance_multi_asset_methods_v1.json`。脚本 `scripts/eval_finance_skill_methods.py` 使用真实配置模型，人工预加载指定总 Skill 和参考，提供合成证据，无工具、无生产数据。人工复核标准不传给模型。相同问题分别给同日/错日净值、普通债缺条款/可转债有赎回公告，观察方法是否随证据改变；另测资金冲突与热点重复快照。

初测：`outputs/finance_multi_asset_methods_20260908/`。六题正常返回，但完整人工审阅发现过度推断，不能记为六题业务质量通过：基金自造“通常折溢价区间”、把成交活跃推为流动性安全；债券把未知票息近似零息、假设用户买入成本和确定性亏损；资金自设连续天数阈值；热点把部分样本或关系类型推断为实际集中/弱关联。

对应调整只在业务参考中澄清“未知与负面证据不同、源分类与推断不同、报价与持仓盈亏不同、风险等级须有比较依据”。未增加关键词路由、运行状态或 validator。冻结样本不改，复测保留在 `outputs/finance_multi_asset_methods_20260908_v2/`。其 commit、方法 revision、模型同上，6 题均正常结束、无工具调用；以下是完整人工复核，不是自动 scorer 分数：

| 题目 | 观察到的改善 | 仍需注意 |
|---|---|---|
| `fund_aligned` | 正确识别金额与 2% 溢价；不自造通常阈值，不再认定退出安全 | 将从市价回归净值的损失写成“约 2%”，仍未明确损失分母；不作为精确派生指标验收通过 |
| `fund_stale_nav` | 转向日期核验，不报当前溢价率，不认定套利 | 仍泛化“价差会随套利机制收敛”，缺少当前产品机制和期限条件 |
| `bond_missing_terms` | 保留未知票息与含权条款，不量化 YTM/久期 | 仍把低频成交延伸为“边缘成交/定向报价”假说，并笼统描述收益率曲线陡峭化的影响；不作已证实事实 |
| `bond_call_notice` | 优先处理公告；不虚构截止日期，区分价格风险与已实现亏损 | **未通过**：`130−100.5` 写成 29.6（应为 29.5），自行假设面值 100，仍说“必须转股或卖出”；“风险超过收益机会”“转股价值接近市价即有基本面驱动”均过度推断 |
| `flow_conflicting` | 保留未知身份、累计与连续区别及股东披露时点 | **未通过**：仍援引“建仓通常伴随持续吸筹”，将融资余额增加写成资金净流入、将户数减少概括为持股集中，超出已给口径 |
| `hot_repeated_snapshots` | 核心要求满足：只承认日内升温，宽度未知，标签不证明业绩；不把部分样本当作集中证据 | 当前仅一个合成用例，不能代表真实热点查询与完整产业传导效果 |

这轮证明方法指导能改变部分回答边界，也证明仅靠增加业务提示不能保证数值与推断质量。保留失败样本，停止针对单题堆补丁；后续全链路应优先验证现有确定性计算能力是否实际使用，以及输出是否忠于已取得的证据。不将上述记录包装成“六题业务通过”或生产可用证明。

复现命令（输出目录必须不存在，避免覆盖）：

```bash
.venv/bin/python scripts/eval_skill_selection_only.py --cases-file tests/evals/finance_multi_asset_selection_v1.json --output outputs/finance_multi_asset_selection_new_run
.venv/bin/python scripts/eval_finance_skill_methods.py --output outputs/finance_multi_asset_methods_new_run
```

## 后续候选与停止线

资金流独立方法已有明确高频问题及真实字段，因此本轮实现。宏观/资产配置、完整基金经理评价、债券组合久期管理等还依赖更多时间序列、持仓、负债约束或条款能力，暂不创建看似完整但只能输出泛泛说明的入口。现有分红、财务、业绩、估值已能承担相关专题，也不另起同义 Skill。

下一步应先解决外层目录参数选择问题，再以授权数据做少量全链路问答，核验按需子方法真实读取、查询口径及最终引用。入口命中与合成证据复测均不足以宣称专业分析质量或生产就绪。
