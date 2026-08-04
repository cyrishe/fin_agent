# 个股深度研究核心方法

## 目录

1. 从问题而不是章节出发
2. 研究命题与证据链
3. 预期差与可证伪性
4. 数据时点与证据质量
5. Sibling Skill 组合
6. 停止条件与无效重试
7. 方法来源

## 从问题而不是章节出发

先回答用户真正要判断什么。常见目标包括：

- 看懂公司靠什么赚钱、优势能否延续；
- 解释最新经营或股价变化；
- 判断当前估值包含了什么预期；
- 复盘持仓逻辑是否仍成立；
- 识别事件、题材或行业变化能否传导到利润；
- 比较少量候选公司的风险收益差异。

目标决定研究深度和证据，不按“基本面—技术面—资金面—新闻面”逐项填空。

## 研究命题与证据链

研究先形成一个简短的自然语言研究契约：用户要判断什么、截至什么时点、最关键的价值变量是什么、完成本轮判断最低需要哪些证据。它是 CC 的工作依据，不新增系统协议或要求模型输出固定 JSON。

随后形成一句可被证伪的命题，再围绕命题组织材料：

```text
当前命题
├── 价值驱动：哪些经营变量决定收入、利润和现金流
├── 支持证据：当前事实为何支持命题
├── 反面证据：哪些事实正在削弱命题
├── 市场预期：当前价格可能已经计入什么
├── 变化触发：未来什么会强化或推翻命题
└── 证据边界：数据截至何时、还缺什么
```

每个重要判断尽量包含“事实 + 解释 + 条件”，而不是只有指标或结论。

## 预期差与可证伪性

- 好公司不自动等于好价格，低估值也不自动等于低风险。
- 短期价格上涨可能来自预期变化、流动性和持仓结构，不直接证明经营改善。
- 研报一致预期是一项市场基准，不是真理；关注预测变化、分歧和实现条件。
- 同行业比较先确认商业模式、周期位置和会计口径可比，再比较倍数。
- 命题必须有失效条件。无法说明什么事实会推翻结论时，结论通常过于空泛。
- 将已兑现结果、管理层指引、机构预测、价格隐含预期和本轮推断分开。需要系统分析预期差或主动反证时，读取 `expectation-gap-and-redteam.md`。

## 数据时点与证据质量

对不同证据分别记时点：

- 实时价格和盘中量价；
- 最近一个完成交易日及历史区间；
- 最新财务报告期与公告日期；
- 研报发布日期、预测年度和预测口径；
- 新闻发生时间与发布时间。

优先级通常是交易所或公司原始披露、监管与结构化事实，其次是可核验的专业研究，再次是新闻和市场讨论。来源数量不能替代来源质量；二手材料能解释预期，但不能静默替代缺失的原始财务事实。

研究过程保留合理依据，并区分事实、估计、假设和推断。对于同一结论，优先采用最接近原始事实且时点匹配的证据，不用多篇转载营造虚假的确定性。

## Sibling Skill 组合

`stock-research` 负责全局研究命题；专业子问题交给现有方法深化：

- 最新财报改变了什么：`earnings-analysis`；
- 利润是否有现金和资产负债支撑：`financial-quality-analysis`；
- 当前价格隐含了多高的增长或回报：`valuation-analysis`；
- 题材到行业、公司、利润的传导是否成立：`sector-theme-analysis`；
- 同业差异是否真实可比：`stock-comparison`；
- 价格行为是否确认或反驳短期叙事：`technical-structure-analysis`；
- 现金回报是否可持续：`dividend-analysis`。

只组合能改变结论的少量方法。Sibling Skill 提供方法，Finance CC 仍统一数据查询、证据判断和回答。

## 停止条件与无效重试

深度不等于查询数量。核心命题已有直接证据、最强反证、估值或预期解释以及明确缺口时，应进入综合回答。

- 结构化查询没有数据时，先判断它是能力缺口还是已知口径错误；没有新的可验证修正依据就停止重试并披露缺口。
- 不为报告章节齐全而查询不影响结论的股东、资金、技术或新闻数据。
- 不同来源出现冲突时补一条能辨别口径的证据，而不是堆更多同类来源。
- 已保存的完整结果按引用读取；不为重新看到同一批数据而重复执行查询。

## 方法来源

本方法吸收并重写了：

- [Anthropic Equity Research Skills](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/equity-research/skills)：研究任务拆分、催化剂、财报更新和 thesis tracker 的可证伪思路。
- [CFA Institute: Industry and Competitive Analysis](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/industry-and-competitive-analysis)：行业结构、竞争强度、外部影响和公司竞争位置的系统分析。
- [CFA Institute: Equity Valuation Concepts and Basic Tools](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/equity-valuation-concepts-basic-tools)：估值模型应匹配公司特征、数据质量和研究目的，并进行交叉核验。
- [CFA Institute Standard V(A)](https://www.cfainstitute.org/standards/professionals/code-ethics-standards/standards-of-practice-v-a)：研究结论应具有合理充分依据，并区分事实与意见。
- [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)：确定性金融计算与 LLM 叙事分离、证据可追溯和多角色综合。
- [TradingAgents](https://github.com/TauricResearch/TradingAgents)：基本面、新闻、情绪、技术和正反研究视角的协作；本系统不照搬固定辩论轮次和交易执行链。
- [TraderMonty US Stock Analysis](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/us-stock-analysis)：基本面、估值、技术表现和风险的综合研究视角。
- 用户提供的 Stock Analysis Skills Pack：事件可知时间、预期栈、催化验证、熊案和监测条件；本系统将其改写为按需参考，并复用已有专业 Skill，不复制固定总控和评分契约。

未采用固定买卖评级、固定报告页数、美股监管文件依赖、强制多 Agent 辩论或每个维度都执行的长流水线。
