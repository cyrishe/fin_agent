# 个股深度研究方法

本参考用于研究目标复杂、证据相互冲突或需要决定“查到什么程度才足够”时。简单深度问题由主 Skill 直接完成，不必读取。

## 从决策问题出发

先说明用户真正需要判断什么，而不是先填“基本面—技术面—资金面—新闻面”章节。常见问题包括：

- 公司靠什么创造价值，优势能否延续；
- 最新经营、财报或股价变化改变了什么；
- 当前价格包含了多高的经营预期；
- 原有持仓逻辑是否仍成立；
- 某项事件或行业变化能否传导到收入、利润和现金流；
- 少量候选公司之间最重要的差异是什么。

## 建立可证伪命题

形成一句简明命题，并把研究组织为：

```text
核心命题
├── 价值驱动：什么变量决定收入、利润、现金流和资本回报
├── 支持证据：哪些已兑现事实支持命题
├── 最强反证：什么事实最可能说明命题错误
├── 市场预期：当前价格可能已经反映什么
├── 验证节点：未来什么会强化、削弱或推翻命题
└── 证据边界：截至什么时点、还缺什么
```

每个重要判断尽量包含“事实 + 解释 + 条件”。无法说明什么会推翻结论时，命题通常不够具体。

## 组织最小证据计划

根据研究深度先选择少量证据目标：快速研判通常二至三个，标准研究通常四至六个；深度报告也先从四至六个开始，只在新矛盾可能改变结论时扩展。按依赖关系分组：

- 可独立取得：公司身份、核心业务、财务趋势、复权行情、估值历史、结构化研报和近期事件；
- 需要前序判断：确定可比公司后再做同业比较，发现价格异常后再核验公司行为，确定估值口径后再解释隐含预期；
- 可选增强：不影响核心结论的普通股东、泛化资金、重复新闻和常规技术指标。

Skill 不指定并行线程、工具顺序或结果字段。系统调度可并行执行独立只读证据；固定前后依赖和机器结果交接应由 Tool/Workflow 表达。

研究深度是当前任务的软性执行策略，不写成 Skill 状态或固定章节开关：

| 用户需要 | 默认研究方式 | 何时继续深化 |
|---|---|---|
| 快速研判 | 聚焦一个决策问题、二至三个证据目标和一个主要分析镜头，先给结论与边界 | 出现直接矛盾，或用户继续追问关键假设 |
| 标准研究 | 四至六个证据目标，一个主框架加必要的交叉验证，覆盖命题、反证、估值/预期和验证点 | 辅助框架可能改变结论，而不只是增加材料 |
| 深度报告 | 仍从最小证据计划开始，按公司原型选择一至三个框架，补充来源、方法局限和必要附录 | 关键结论仍受证据冲突、重要缺口或情景敏感性影响 |

用户没有指定深度时，根据问题的决策强度和交付要求自然判断；语义模糊时使用标准研究，不增加确认阻塞。继续深化必须复用已有 working set，不从公司身份和基础行情重新开始。深度报告的完整性主要来自对既有证据的经营解释、正反命题、情景和验证点，不来自更多工具维度；首批证据已覆盖公司经济引擎、财务、估值/预期和反证来源时，应进入综合，不为报告章节继续查询泛资金、常规技术、重复行情窗口或重复新闻。

## 选择专业框架，而不是堆框架名称

专业框架用于提出更好的问题、组织证据和检验命题，不替代事实，也不自动成为报告章节。先按公司原型和决策问题选择一个主框架；只有辅助框架能提供不同视角、交叉验证或反证时才追加，通常不超过三个。

| 要回答的问题 | 可借鉴的专业框架 | 本系统中的适用方式 |
|---|---|---|
| 行业利润由什么决定，公司处于什么竞争位置 | Porter 五力、PESTLE、CFA 行业与竞争分析 | 识别行业边界、供需与议价关系、外部变量、市场份额和竞争策略；不把行业分类标签直接当作竞争结论 |
| 竞争优势是否真实且耐久 | Morningstar Economic Moat | 从无形资产、转换成本、网络效应、成本优势和有效规模寻找优势来源，再用资本回报与现金证据验证；只表述为借鉴，不冒充 Morningstar 官方评级 |
| 高盈利或高 ROE 的来源和质量如何 | DuPont、MSCI Quality，必要时参考 Piotroski | 拆解利润率、周转和杠杆，检查盈利能力、负债、盈利稳定性和现金转化；特殊行业不用通用阈值机械打分 |
| 增长是否创造价值 | Damodaran Fundamental Growth、ROIC 相对资本成本 | 把增长连接到再投资率、增量资本回报和竞争优势期限；不把收入增速直接等同于价值增长 |
| 当前价格要求兑现什么 | DDM、FCFF/FCFE、剩余收益、可比估值与隐含预期 | 按商业模式、盈利状态和资本结构选择口径；没有正式模型和可靠假设时只做条件化解释，不生成伪精确价值 |
| 哪些变化会推翻命题 | 情景分析、敏感性分析、Thesis Red Team | 保留基准、上行、下行条件和最强反证，连接到可观察验证节点；不编造概率和阈值 |

同一次分析可以组合多个框架，但不按框架分别生成小报告。最终只保留框架对核心命题贡献的判断、证据、限制和置信度。框架需要精确公式、稳定输入或重复计算时，把计算实现为 Tool；需要固定依赖和机器交接时使用 Workflow；只有形成高频、可独立触发且可单独评测的专业任务时，才考虑注册为新 Skill。

## 用三条桥把方法落实到报告

专业性不来自展示框架名称，而来自三条能够被证据检验的桥：

1. **行业到公司**：行业规模、结构、供需和竞争作用力如何落到公司的份额、价格、成本、客户或资本回报；
2. **经营到财务**：订单/销量/价格/结构如何进入收入、利润、现金、营运资本、资本开支和增量资本回报；
3. **预期到估值**：已兑现事实、管理层目标、机构预测和价格隐含假设分别是什么，哪些变量决定估值重估或压缩。

每个主要命题用“判断—直接事实—传导—最强反证—下一验证点”表达。若某个框架不能改变证据目标、解释传导或发现反证，就不在正文展示它。

估值遵循“理解业务—形成预测—选择方法—转换为估值—给出条件化结论”的顺序。没有正式预测和确定性计算 Tool 时，停在隐含预期、相对估值和敏感变量，不让模型补造完整 DCF。

## 组合其他专业方法

多个 Skill 是并列方法，不是父子执行关系。当前问题确实需要财务质量、估值、同业、行业、技术或分红方法时，由 Finance CC 从注册目录加载少量相关方法，并在同一 working set 中统一取证和综合。

若后续步骤必须消费一个确定结果，应调用 Tool；若必须按固定依赖执行，应使用 Workflow。不要在 Skill 中声明脆弱的 `depends_on_skill` 或假装另一个 Skill 会返回固定 JSON。

## 停止条件

以下条件满足时进入综合，不继续扩展：

- 核心命题已有直接证据；
- 最强反证已经得到解释或明确保留；
- 当前价格/估值或市场预期与命题的关系可以说明；
- 下一验证点和数据缺口清楚；
- 新增查询不太可能改变结论，只会增加材料。

无数据时没有新的可验证修正依据就停止重试。冲突时补一条能区分口径的直接证据，而不是堆更多同类来源。

## 方法来源

- [Anthropic Equity Research Skills](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/equity-research/skills)：研究、财报、催化和 thesis tracking 的任务边界。
- [Anthropic Thesis Tracker](https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md)：可证伪命题、反面证据和持续更新。
- [CFA Institute: Industry and Competitive Analysis](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/industry-and-competitive-analysis)：行业结构、竞争位置和外部变量。
- [Harvard Business School: The Five Forces](https://www.isc.hbs.edu/strategy/business-strategy/Pages/the-five-forces.aspx)：行业结构、竞争作用力和战略位置。
- [Morningstar: Economic Moat Ratings](https://www.morningstar.com/business/insights/blog/equity-economic-moat-ratings)：竞争优势来源、耐久性及其与资本回报和估值的关系。
- [MSCI: Quality Time](https://www.msci.com/research-and-insights/paper/quality-time-understanding-factor-investing)：ROE、杠杆和盈利稳定性的质量视角。
- [Damodaran: Fundamental Determinants of Growth](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/growth.htm)：再投资、资本回报与增长价值的联系。
- [CFA Institute: Discounted Dividend Valuation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/discounted-dividend-valuation)：股利、自由现金流和剩余收益模型的适用边界。
- [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)：金融数据/计算与 LLM 综合分层；本方法不照搬其固定工作流。
