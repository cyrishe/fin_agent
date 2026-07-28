# Fin Agent 金融业务 Skill 架构方案

> 本文只设计金融、股票、证券场景的业务 Skill。现有金融业务 Skill 视为不存在，从零规划。
>
> 需求理解、方案设计、流程图、Coding、测试等自定义工具开发能力属于系统 Skill，继续保留，但不纳入本文的金融业务 Skill 分类。

## 1. 建设目标

金融业务 Skill 用来完成用户能够直接识别的专业任务，例如：

- 分析一只股票；
- 复盘市场和风格；
- 根据自然语言条件选股；
- 分析财报、估值、行业或事件；
- 解释因子和策略表现。

业务 Skill 不应成为 API 使用说明、固定状态机或一组数据字段的包装。它应向 Agent 提供模型本身不稳定具备的专业方法、工具使用方式和结果质量要求。

整体遵循 Fin Agent 的 `SOFT → HARD → SOFT`：

```text
用户问题、上下文和反馈
        ↓ SOFT
选择适合的业务 Skill，并理解本轮真正任务
        ↓
调用确定性 Tool / Data API，保存必要事实和证据
        ↓ HARD
基于业务方法分析、解释并组织结果
        ↓ SOFT
自然、清晰、可追溯的金融回答
```

HARD 只用于可靠执行和保存必要事实，不用来限制自然对话：

- 可以稳定记录：`skill_id`、证券标识、时间范围、数据截至时间、工具调用和证据引用。
- 不为业务表达增加 `success`、`is_error`、固定阶段状态或复杂枚举。
- 不因为少了某个展示字段而中断任务。
- Skill 的正式输出以结构化自然语言为主，不要求输出多层 JSON。

## 2. 调研结论

### 2.1 Anthropic Financial Services

[anthropics/financial-services](https://github.com/anthropics/financial-services) 是目前最完整的官方金融 Skill 参考。它按照金融工作任务组织能力，而不是按照底层 API 分类，主要覆盖：

- Equity Research：业绩分析、业绩预览、首次覆盖、研究模型更新、投资逻辑跟踪、催化剂日历；
- Financial Analysis：可比公司、DCF、LBO、三张表、竞争分析；
- Investment Banking、Private Equity、Wealth Management、Fund Administration 等机构场景。

最值得借鉴的设计是：

1. Agent 负责理解用户和组织完整任务；
2. Skill 保存某项金融工作的专业方法；
3. Connector / Tool 负责获取数据或执行确定性操作；
4. 一个 Agent 只加载当前需要的 Skill 和资料。

部分 Skill 很长，并混入大量机构模板和美国市场假设，不适合直接复制。

### 2.2 OctagonAI Skills

[OctagonAI/skills](https://github.com/OctagonAI/skills) 将股票分析拆成行情、分析师预期、财务报表、电话会、SEC 文件、行业和估值等细分能力，并使用 Master Skill 组织叶子 Skill。

可以借鉴：

- 对财报、电话会和监管文件的细分分析方法；
- 总任务与专业方法之间的分层；
- 针对同类资料形成稳定分析视角。

不宜照搬：

- 很多叶子 Skill 实际只是单个 Tool 的调用手册；
- 过细拆分会让 Skill 选择、上下文和组合成本上升；
- “查一个数据”不应该独立成为业务 Skill。

### 2.3 Claude Trading Skills

[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) 提供了较完整的交易研究分类：

- market-regime；
- screening / opportunity；
- trade-planning；
- strategy-research；
- performance / memory；
- meta。

其 `skills-index.yaml` 为 Skill 记录类别、输入输出、时间范围、依赖和工作流，适合参考 Skill Registry 的建设。

可以借鉴分类和发现机制，但不应复制其中大量策略假设、付费数据依赖和硬编码交易规则。Registry 只服务于发现、加载和观察，不应演变成业务状态机。

### 2.4 LangAlpha

[ginlix-ai/LangAlpha](https://github.com/ginlix-ai/LangAlpha) 更值得借鉴运行架构：

- 先给 Agent 简短的工具和 Skill 摘要；
- Skill 被选中后才加载完整说明；
- 大型金融资料进入 workspace，由 Agent 按需检索；
- 批量数据通过代码处理，不直接塞入模型上下文。

这与 Fin Agent 已有的 API Catalog、Context Bundle 和动态执行方式契合。业务 Skill 应说明“需要什么信息和如何分析”，具体 API 文档继续作为可检索资产，不复制进每个 Skill。

### 2.5 A 股社区项目

[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) 提供了 A 股 T+1、涨跌停、资金流和市场制度等参考；[simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data) 展示了 A 股数据能力封装。

这些项目适合补充中国市场术语和数据需求，但社区规则必须经过本地数据协议和金融口径验证。数据查询封装仍应归类为 Tool，而不是业务 Skill。

## 3. Skill、Tool 与系统 Skill 的边界

### 3.1 Tool

Tool 是确定性能力，输入相同且数据时点相同，结果应基本一致。

例如：

- 查询证券代码；
- 获取日线、分钟线、财务报表、估值和资金流；
- 执行已发布的动态工具；
- 加载报告或历史中间结果；
- 计算确定公式。

`stock.quote` 是 Tool，不是业务 Skill。

### 3.2 金融业务 Skill

业务 Skill 负责需要语义理解、专业方法和综合判断的任务。

例如：

- 判断一只公司的基本面和估值是否匹配；
- 解释市场为什么走强或走弱；
- 将自然语言条件转化为合理的选股分析；
- 对财报变化、催化剂和风险进行综合判断。

Skill 可以调用多个 Tool，但 Tool 不主动调用 Skill。一次任务需要多个 Skill 时，由 Agent 总控选择和组织，不让 Skill 之间形成隐藏调用链。

### 3.3 系统 Skill

系统 Skill 负责 Fin Agent 自身的构建能力：

- 金融工具需求理解与确认；
- 模块和流程设计；
- 流程图生成；
- 动态模块 Coding；
- 功能运行与静态对齐测试。

它们保留在独立的 `financial-tool-development` 体系中。诸如 `financial-requirement`、`financial-test-planning` 的能力不能出现在普通金融业务 Skill 目录，也不能被普通金融问答误触发。

## 4. 推荐的业务 Skill 分类

分类依据是“用户希望完成的任务”，不是数据来源，也不是页面展示形式。

### 4.1 市场与行业

#### `market-overview`

回答大盘、市场环境、风格、宽度、成交和风险偏好的综合问题。

核心步骤：

1. 明确市场、日期和用户关注的维度；
2. 获取主要指数、涨跌分布、成交、风格和必要的资金数据；
3. 区分市场事实、主要驱动和推断；
4. 给出简洁结论、核心证据、分化和风险。

#### `sector-theme-analysis`

分析行业、概念或主题的表现、驱动、内部结构和代表公司。

核心步骤：

1. 确认行业或主题的业务边界；
2. 分析整体表现、持续性和内部宽度；
3. 识别领涨、补涨、分化和可能驱动；
4. 给出代表公司、证据与需要继续观察的变量。

### 4.2 个股研究

#### `stock-research`

对单只或少量股票进行综合研究。它是宽口径任务，不替代更专业的财报、估值或事件 Skill。

核心步骤：

1. 通过系统 Tool 确认证券身份；
2. 理解用户真正关心的问题；
3. 选择必要的行情、财务、估值、行业和事件信息；
4. 形成核心判断、证据、反面信息和待观察事项；
5. 明确数据时点以及事实与推断的边界。

#### `earnings-analysis`

分析业绩预告、定期报告或已公布财报。

核心步骤：

1. 确认报告期、对比基准和材料范围；
2. 识别收入、利润、现金流、利润率和关键经营指标变化；
3. 区分一次性因素与经营趋势；
4. 分析超预期、低于预期或结构变化；
5. 给出主要结论、证据、风险和后续观察点。

#### `valuation-analysis`

分析当前估值、历史位置和可比公司差异。

核心步骤：

1. 明确估值对象和适用方法；
2. 获取价格、盈利、资产、成长和可比数据；
3. 选择适合业务特征的估值视角；
4. 解释估值差异背后的基本面原因；
5. 输出估值判断、关键假设和敏感因素，不伪造精确目标价。

#### `thesis-and-catalyst`

形成或更新投资逻辑、催化剂与反证条件。

核心步骤：

1. 归纳当前投资逻辑；
2. 提取支持证据和核心假设；
3. 识别催化剂、时间窗口和潜在反证；
4. 使用新事实更新原有判断；
5. 保留哪些判断已被验证、削弱或仍待观察。

### 4.3 选股、因子与策略研究

#### `stock-screening`

将用户的自然语言条件转化为选股过程，并解释候选结果。

核心步骤：

1. 理解用户真正想寻找的公司特征；
2. 将条件整理为必要筛选、偏好条件和排序依据；
3. 只对真正影响结果的歧义进行确认；
4. 调用数据 Tool 或已发布动态工具执行；
5. 展示候选、命中依据、未命中原因和数据时点。

该 Skill 负责完成一次选股任务；如果用户要求创建可反复使用的新工具，应切到系统级自定义工具开发流程。

#### `factor-analysis`

计算、解释或比较因子，不负责开发新的动态工具。

核心步骤：

1. 明确因子含义、对象、时间范围和比较方式；
2. 获取计算所需的数据；
3. 计算因子及少量关键中间指标；
4. 检查样本、缺失值和可比性；
5. 解释结果代表什么、不代表什么。

#### `strategy-analysis`

分析已有规则或策略的逻辑、触发结果和典型案例。

核心步骤：

1. 理解策略目标和已有规则；
2. 识别关键条件、依赖数据和适用环境；
3. 执行已有策略或使用历史数据检查表现；
4. 展示触发过程、核心指标和反例；
5. 区分代码能否运行与策略是否有效。

策略开发或修改进入系统 Skill；策略运行、解释和研究留在业务 Skill。

### 4.4 文档研究

#### `financial-document-analysis`

分析研报、公告、财报、招股书、电话会或用户上传的金融材料。

核心步骤：

1. 确认用户关注的问题和材料范围；
2. 从文档资产按需检索相关章节，不把全文塞入上下文；
3. 提取事实、管理层表述、变化和风险；
4. 必要时使用数据 Tool 做外部核对；
5. 输出带来源定位的结论。

首版可以用一个统一 Skill，等真实使用证明电话会、监管文件或研报需要明显不同的方法时再拆分。

### 4.5 暂缓建设

以下能力等产品范围和数据成熟后再决定：

- 组合诊断与风险暴露；
- 自动调仓建议；
- 交易执行与订单管理；
- 复杂回测研究；
- 交易复盘和长期记忆；
- 投行、PE、财富管理等机构工作流。

不因为参考项目存在这些 Skill 就提前建设。

## 5. 推荐目录结构

```text
src/skills/
├── financial-tool-development/       # 现有系统 Skill，保持独立
└── finance-business/                 # 新建的金融业务 Skill 根目录
    ├── catalog.json                  # 发现和加载所需的最小注册信息
    └── skills/
        ├── market-overview/
        │   ├── SKILL.md
        │   ├── agents/openai.yaml
        │   └── references/
        ├── sector-theme-analysis/
        ├── stock-research/
        ├── earnings-analysis/
        ├── valuation-analysis/
        ├── thesis-and-catalyst/
        ├── stock-screening/
        ├── factor-analysis/
        ├── strategy-analysis/
        └── financial-document-analysis/
```

不按 `market/`、`research/` 再增加一层物理目录。分类保存在 Catalog 中即可，避免发现和加载路径过深。

每个 Skill 只创建实际需要的文件：

```text
skill-name/
├── SKILL.md
├── agents/openai.yaml
└── references/       # 只有存在专用方法或资料时才创建
```

- 不创建 Skill 自己的 README、设计记录和变更日志。
- 确定性、重复执行的计算优先成为平台 Tool；只有确有必要时才在 Skill 中增加 `scripts/`。
- 平台 API 文档继续由统一 API Catalog 管理，不复制到 Skill。
- 金融方法、特定口径和输出示例可以进入 `references/`，由 Skill 明确何时读取。

## 6. 单个 Skill 的内容规范

### 6.1 Frontmatter

只保留标准字段：

```yaml
---
name: stock-research
description: 对一只或少量股票进行行情、基本面、估值、行业与风险的综合研究。用户要求分析、评价、比较某只股票，或询问其投资逻辑和主要风险时使用。
---
```

`description` 是主要触发依据，需要同时说明“做什么”和“什么问题应使用”，不要另外维护一套关键词规则。

### 6.2 SKILL.md 正文

推荐控制在以下结构：

```markdown
# 角色与任务

用一段话说明专业角色、业务任务和结果目标。

## 工作方法

用 4～6 个自然步骤说明如何完成任务。步骤表达业务方法，
不是系统状态，也不要求每次机械地全部展示。

## 工具与证据

说明应优先使用系统 Tool 获取哪些事实、如何处理时间口径、
缺失数据和事实与推断的边界。

## 回答要求

说明用户最终应看到的结论、证据、风险和必要限制。
以自然语言为主，不定义复杂 Output Schema。

## 按需参考

明确什么情况下读取 references 中的具体文件。
```

### 6.3 Skill 内部交互原则

- 信息足够时直接完成任务，不为了展示流程而提问。
- 缺少的信息可以用可靠默认值处理时，简要告知并继续。
- 只有缺失信息会改变任务方向或使结果失去意义时才询问。
- 用户反馈由 Agent 和对话上下文承接，Skill 不维护自己的状态机。
- 用户中途换到普通金融问题，由顶层 Agent 正常路由；返回后仍可继续原任务。
- Skill 选择不完全准确但仍可合理完成任务时，继续向目标推进，不因分类边界拒绝。

### 6.4 输出原则

普通金融业务 Skill 默认输出结构化自然语言：

1. 先回答用户真正的问题；
2. 给出少量最重要的证据和指标；
3. 说明风险、反面信息或数据限制；
4. 标注数据截至时间；
5. 需要继续研究时，给出自然的下一步。

表格、图表和卡片是 Renderer 对自然结果的表达，不反向要求 Skill 输出复杂 JSON。只有确定性 Tool 调用参数和可寻址证据使用稳定协议。

## 7. Catalog 的最小设计

Catalog 只用于发现 Skill、展示和加载，不决定对话能否继续。

建议首版字段：

```json
{
  "id": "stock-research",
  "category": "equity-research",
  "path": "skills/stock-research",
  "description": "对一只或少量股票进行综合研究"
}
```

字段用途：

- `id`：稳定寻址；
- `category`：目录展示和管理；
- `path`：加载 Skill；
- `description`：发现与语义选择；

Catalog 不记录“数据充分”“部分缺失”或平台能力标签。每个 Skill 在
`## 数据需求` 中用自然语言说明完成分析所需的核心证据、按问题补充的证据、
增强证据和数据边界；当前系统真实可用的数据及调用方式由动态 API Catalog
提供，Finance CC 在运行时完成匹配。Skill 不绑定具体 subject、dataview 或 API 名。

暂不加入 `required_capabilities`、`maturity`、`difficulty`、`timeframe`、
固定工作流、前置 Skill、后置 Skill、成功状态和大量输入输出字段。
未来只有出现真实消费方时才扩展。

## 8. Agent 如何使用业务 Skill

推荐由 Finance CC 主会话承担自然语言总控：

1. 顶层模块完成上下文指代消解，并确定进入 Finance Agent；
2. Finance Agent 根据当前问题和 Catalog 描述选择零个、一个或少量业务 Skill；
3. Skill 被选中后加载 `SKILL.md`；
4. Agent 按需读取该 Skill 的 references，并调用系统 Tool；
5. Agent 综合事实和专业方法，直接向用户回答；
6. 系统保存工具调用、证据和必要资产，不让 Skill 重复输出系统事实。

没有合适 Skill 时，Finance Agent 可以使用已有金融 Tool 正常回答，不为覆盖率强行选择 Skill。

多 Skill 任务由 Agent 组织，例如“分析贵州茅台并与五粮液比较估值”可以复用股票研究和估值方法，但系统不预设固定 Skill 流程，也不让一个 Skill 隐式调用另一个 Skill。

## 9. 第一阶段建设范围

建议首批只建设六个 Skill：

1. `market-overview`
2. `stock-research`
3. `stock-screening`
4. `factor-analysis`
5. `earnings-analysis`
6. `sector-theme-analysis`

它们覆盖当前 Fin Agent 的主要业务：

- 常规金融问答；
- 大盘、行业和个股分析；
- 选股策略平台；
- 因子计算和解释；
- 财报与基本面研究。

第二批优先补充高频个股任务：

1. `valuation-analysis`
2. `financial-quality-analysis`
3. `stock-comparison`
4. `technical-structure-analysis`
5. `dividend-analysis`

投资逻辑跟踪、策略研究和金融文档分析继续根据真实使用补充。

不一次创建全部目录和空 Skill。每完成一个 Skill，就用真实问题验证后再建设下一个。

## 10. 建设与验证方法

### 10.1 建设前

只盘点平台现有 Tool、API Catalog 和数据覆盖，不参考现有金融业务 Skill 的内容：

- 当前能稳定取得哪些数据；
- 数据时间范围和更新频率；
- 哪些数据已有确定性 Tool；
- 哪些业务任务存在真实数据缺口。

Skill 不能凭模型记忆弥补证券代码、行情、财务或其他可查事实。

### 10.2 单 Skill 验证

每个 Skill 准备 5～10 个真实问题，至少覆盖：

- 明确任务；
- 信息较模糊但可以合理继续；
- 真正需要用户补充的信息；
- 多轮追问或修改关注点；
- 数据不足；
- 与相邻 Skill 容易混淆的问题。

重点观察：

1. 是否进入合理的 Skill；
2. 即使选择不是最优，是否仍向用户目标推进；
3. 是否调用正确 Tool 获取事实；
4. 是否存在无依据结论；
5. 输出是否简洁、有证据且真正回答问题。

### 10.3 集成验证

分别进行：

- 单步测试：顶层路由和 Skill 选择；
- 分阶段测试：单独验证一个业务 Skill 的执行；
- 全链路拟真测试：根据 Agent 每轮真实返回继续对话，包括追问、切换话题和回到原任务。

效果问题记录后优化 Skill；只有真实的协议、工具或主线功能问题才修改系统层。

## 11. 推荐决策

Fin Agent 不应建设一个数量庞大、每个数据查询都对应 Skill 的目录，也不应把所有金融能力塞进一个万能 Skill。

最推荐的方案是：

1. 系统 Skill 与金融业务 Skill 完全分层；
2. 业务 Skill 按用户任务分类；
3. 首批只建设六个高频 Skill；
4. 每个 Skill 保持短小，以业务方法和结果要求为核心；
5. 数据 API 作为可检索平台资产，确定性能力保留为 Tool；
6. Finance Agent 以自然语言选择和组合 Skill，不建立固定 Skill 状态机；
7. 用真实对话决定后续拆分，而不是预先穷举金融业务。

判断一项能力是否值得成为独立 Skill 时，只问五个问题：

1. 用户是否会把它作为独立任务提出？
2. 是否需要多个步骤、数据源或专业判断？
3. 是否存在相对稳定的金融工作方法？
4. 是否能形成独立且可评价的业务结果？
5. 是否与已有 Skill 有清晰不同的任务目标？

多数答案为否时，它应当是 Tool、reference 或现有 Skill 的一个分析步骤，而不是新的 Skill。

## 12. 首批实际落地

目前已经按本文规范建立十一项业务 Skill，其中个股相关能力占主要部分：

| Skill | 公开方法基础 | 当前数据适配 |
| --- | --- | --- |
| `market-overview` | TraderMonty 的市场环境分析，以及 Anthropic 的金融研究分层 | `index.quote`、指数成分聚合、`industry`、`plate.quote`、`plate.moneyflow` 可支持指数、宽度、行业和资金结构 |
| `stock-research` | Anthropic Equity Research、TraderMonty US Stock Analysis | `stock.basic_info`、`quote`、`financial_3_table`、`pricevalue`、`moneyflow`、`report` 等可支持综合研究 |
| `stock-screening` | Anthropic Idea Generation | 股票各 dataview 的筛选、排序、窗口方法以及行业、板块成分能力可支持自然语言选股 |
| `earnings-analysis` | Anthropic Earnings Analysis | `financial_3_table`、`performance_notice`、`business_segment`、`report` 可支持报告期、业绩质量和业务结构分析 |
| `sector-theme-analysis` | Anthropic Sector Overview | 行业、板块、成分和资金数据可支持内部结构、分化和代表公司分析 |
| `factor-analysis` | Anthropic Idea Generation 的因子视角 | 行情、财务和确定性计算能力可支持因子计算与解释 |
| `valuation-analysis` | Anthropic Comps Analysis 与 DCF Model | `pricevalue`、行情和财务数据可支持历史、同业和隐含预期分析 |
| `financial-quality-analysis` | Octagon Financial Health 与 Financial Metrics | 三张表字段可支持盈利、现金流、负债、营运和效率分析 |
| `stock-comparison` | Anthropic Comps Analysis 与 Competitive Analysis | 行业成分、个股行情、财务和估值可支持一致口径比较 |
| `technical-structure-analysis` | TraderMonty Technical Analyst | 日线、分钟线和窗口计算可支持趋势、关键位置与量价分析 |
| `dividend-analysis` | TraderMonty Dividend Skills | `corporate_action`、行情和财务数据可支持股息与可持续性分析 |

实际文件位于：

```text
src/skills/finance-business/
├── catalog.json
└── skills/
    ├── market-overview/
    ├── stock-research/
    ├── stock-screening/
    ├── earnings-analysis/
    ├── sector-theme-analysis/
    ├── factor-analysis/
    ├── valuation-analysis/
    ├── financial-quality-analysis/
    ├── stock-comparison/
    ├── technical-structure-analysis/
    └── dividend-analysis/
```

每项 Skill 均使用简短的 `SKILL.md` 定义任务和工作方法，将详细金融方法及公开实现依据放在 `references/method.md`，不复制具体 API 名称，不增加 `skill.json` 或业务输出 Schema。这样可以在数据平台变化时保持业务方法稳定，只由金融数据工具和 API Catalog 适配实际访问。
