# Skill 资产梳理、入口选择试验与描述实践调研

日期：2026-09-08。范围：当前本地工作区；不代表已部署服务器版本。

## 1. 本地环境

本地 `.env` 已有可用的 `DASHSCOPE_API_KEY`，但缺少 DSH 入口读取的 `LLM_API_KEY`。使用本地原有 URL、模型及 Key 发起最小真实请求，HTTP 200，返回 `OK`。因此没有从服务器复制密钥，而是将已验证的本地 Key 补入规范字段 `LLM_API_KEY`；原有 URL、模型和其他配置不变。

`.env` 已被 Git 忽略，文件权限设为 `0600`。报告与测试资产不保存 Key 或请求认证头。服务器仅进行模型配置是否存在、模型名和端点主机的只读核对，没有修改服务器配置或重启服务。

当前测试模型：`deepseek-v4-flash-0731`，本地模型端点主机：`dashscope.aliyuncs.com`。后续选择测试使用规范的 `LLM_*` 配置。已验证模型访问和选择阶段，不据此宣称数据库、最终回答、前端或全链路已经验证。

## 2. 资产清单与处理

此次属于业务 Skill 的 SOFT 描述整理，不修改 HARD 协议、权限、状态机或生产路由。按 `skill-creator` 的入口语义与渐进加载原则检查描述；金融方法及其数据契约保留。

### 现役系统业务 Skill：12 个，全部保留

每个都有正文方法及按需参考文件。以下是入口摘要；完整对外描述以 `src/skills/finance-business/catalog.json` 和对应 `SKILL.md` 的相同 `description` 为准。

| Skill | 入口功能 |
|---|---|
| market-overview | 市场复盘、大盘强弱、参与度、风格分化和风险偏好 |
| sector-theme-analysis | 行业/板块/主题表现、热点持续性、内部分化和事件传导 |
| stock-research | 围绕单股的投资逻辑、经营、走势、风险与持仓复盘，按需组合多角度方法 |
| equity-report-analysis | 个股相关的机构观点、预测、评级目标价、共识分歧、预期修订及研报专题证据 |
| earnings-analysis | 一次业绩预告、快报或财报的变化、增长驱动、质量及预期差 |
| stock-screening | 将自然语言条件转为股票范围、必要条件、偏好和排序，解释候选 |
| factor-analysis | 已定义因子的含义、口径、值与排名、时间变化及适用局限 |
| valuation-analysis | 贵不贵、估值支撑、历史/同业位置和价格隐含预期 |
| financial-quality-analysis | 跨期盈利持续性、利润现金含量、营运背离及偿债风险 |
| stock-comparison | 多只股票的可比性、相对优势、差异、代价及适用前提 |
| technical-structure-analysis | 趋势、高低点、量价、均线、支撑阻力及信号失效条件 |
| dividend-analysis | 分红记录、股息水平、稳定性、现金支撑和持续性 |

个股与研报两个 Skill 保留此前按用户意见形成的描述；本轮将其余 10 个入口整理为正向的“分析什么—适用问题—如何形成结果”。没有加入“不得选择另一个 Skill”或固定互斥关系。正文和 reference 仍负责执行方法，不把所有子方法塞入入口。

### 已退役旧版 Skill：2 个，保留但不重新上线

| 资产 | 内容依据 | 处理 |
|---|---|---|
| stock_deep_dive | 行情、资金、研报、新闻和风险方法，配套 Schema、工具策略及旧 runner | 归纳 `purpose` 与 frontmatter 描述，保留退役状态 |
| quant_factor_screening | 因子计划、数据准备、确定性计算、覆盖率及候选审计要求 | 归纳 `purpose` 与正文目标，保留退役状态 |

两者均为 `availability.lifecycle=retired`、`retrieval_mode=direct_only`，不在本轮 12 个现役业务候选中。Alpha/旧版名称不是空壳证据；删除它们还会影响现有兼容性测试。此次确认应删除的空壳为 **0 个，实际删除 0 个**。

### 平台阶段方法：8 个，保留

工具需求、设计、流程图、实现、测试、编辑规划、编辑实现，以及 Skill authoring，均有实质阶段方法。其现有描述已标明阶段输入或职责；它们属于工具/Skill 构建流程，不混入投资分析的 12 项业务选择竞赛。本轮不改变这些阶段资产。

审计覆盖仓库内 `src/skills` 的 22 个方法资产；不代表已审计线上数据库中各用户的 public/private Skill。此次没有修改用户数据库资产，也没有将任何退役资产重新开放。

## 3. 选择阶段试验

### 执行边界

脚本：`scripts/eval_skill_selection_only.py`。

1. 使用现有 `FinancialQaCcService.answer`，无 `$`、无显式 Skill，指定金融问答 Agent，独立会话，`research_mode=auto`、`execution_mode=standard`。
2. 真实启动 DSH 和 MCP，令本地捕获端点接收 DSH 生成的首轮模型请求。端点只返回测试专用 HTTP 400，从不返回模型 completion，因此不能触发后续工具。
3. DSH 关闭后，使用本地规范模型配置重放捕获的请求；仅把流式传输改为非流式，保留消息、工具声明、模型、thinking、reasoning effort 和 token 限制。不额外指定 `tool_choice`，不注入预期 Skill 或评测答案。
4. 保存真实模型返回的 `tool_calls`，评分后结束，不把 completion 交回 DSH。每例检查 MCP trace 中 `calls=[]`、`result_refs=[]`。

实际首轮由现有阶段策略开放 `read_finance_skill` 和 `read_finance_skill_reference`，可以用 `skill_ids=[]` 声明通用方法。这个限制是当前产品行为，不是测试脚本添加的强制选中规则。因此本试验能检查“选哪个方法/选择空集”，不能证明取消阶段策略后模型仍会自发优先 Skill。

这里“选择”是模型提出读取方法，不是方法正文已被读取或执行。捕获日志中的 HTTP 400 / `finish_reason=error` 是故意停止采样的记录，不是实际阿里云调用失败；实际调用结果保存在各例 `response.json`。

### 样本与指标

18 题：历史 11 个业务 Skill 运行题、此前 3 个真实 Chat 研报题、此前研报评测的 RTE001/RTE003/RTE005，以及 1 个历史纯行情查询对照。历史来源路径、原问题及本轮 gold 在执行前写入 `manifest.json`。以前数据 API 的 gold 不直接当作 Skill gold；例如“泸州老窖在金融科技领域的布局有哪些”本轮主方法是个股分析，研报可提供证据。

指标分别看：预期方法是否出现、提出的调用是否满足选择阶段契约、所选集合是否落在预先声明的可接受集合。批量读取允许多选，方法数组顺序不当成强制优先级。

冻结描述后先做一题拦截链路验证，再跑 18 题。链路验证不并入正式分母。没有根据模型结果修改描述、路由或预先声明的答案集合。对于超出可接受集合的辅助方法，另作业务复核，不把主观复核冒充事先定义的自动评分。

### 运行结果

原始证据目录为 `outputs/skill_selection_only_20260908`。基准 commit 为 `af6a0b4c289d28446c2ba74b4f811bda11601179` 加本地未提交修改；Skill revision 为 `19b4f1f73762f0a44b64a5f66ada3e71523c9f0587ac76933bd08bd052c0f3ad`。模型实际使用 `reasoning_effort=low`、`max_tokens=1536`，没有额外设置温度。执行前后所记录源码 hash 一致。每例 `capture.json`、`request.json`、`response.json`、`result.json` 保存捕获证据、模型输入输出及节点状态。

| 指标 | 结果 | 含义 |
|---|---|---|
| 模型请求完成 | 18/18 | 不含捕获端点故意返回的 HTTP 400 |
| 分析题核心方法出现 | 17/17，100% | 只看预期 Skill 是否在选中集合中，不证明选择足够精简或调用合规 |
| 含纯取数对照的核心语义命中 | 17/18，94.4% | 对照预期空集，实际选择 stock-research |
| 首轮仅提出选择阶段允许的调用 | 15/18，83.3% | 3 题额外提出尚未开放的 read_finance_catalog |
| 预期核心选择且整轮调用合规 | 14/18，77.8% | 自动 scorer 的 primary_hits，既扣除上述 3 题，也扣除纯取数对照 |
| 所选集合全部落在事先允许集合且调用合规 | 5/18，27.8% | 自动 scorer 的 acceptable_selections；不能直接解释为其余题全部业务选错，见下文 |
| 金融数据工具执行 | 0 | 全部捕获 trace 的 calls/result_refs 均为空，completion 未交回执行器 |

模型累计消耗 40,977 tokens。这是单轮历史样本试验，没有同一题多次重跑的稳定性结论，也没有前后描述 A/B 对照，不能宣称此次描述改动提升了多少准确率。预先一题链路验证额外消耗 2,253 tokens，不并入上述分母。

逐题实际选择如下（中文为 Skill 名称缩写，精确 ID 见 JSON）：

| 历史题 / 关注点 | 首轮选择 | 观察 |
|---|---|---|
| market_overview_real / 大盘复盘 | 市场概览 | 核心命中；额外请求未开放的数据目录 |
| sector_theme_real / 白酒板块结构 | 行业主题、市场概览、技术结构 | 核心命中；个股技术方法用于板块需关注适配，首轮可能多选 |
| stock_research_real / 宁德时代中期研究 | 个股、估值、财务质量、技术、业绩、研报 | 核心命中；首轮 6 个方法偏重；额外请求未开放的数据目录 |
| earnings_analysis_real / 茅台本期年报质量 | 业绩、财务质量 | 两者有合理互补，超出本次预设集合不应直接判业务错误 |
| stock_screening_real / 沪深300条件选股 | 选股、技术、财务质量 | 核心命中；条件判断是否需要两个额外方法须看实际贡献 |
| factor_analysis_real / 60日动量差异 | 因子 | 专项命中，集合精简 |
| valuation_analysis_real / 美的估值支撑 | 估值、个股、财务质量、业绩 | 核心命中；估值和质量互补，首轮是否需加载四个待观察 |
| financial_quality_real / 海天三年财务 | 财务质量、业绩 | 核心命中；多期质量与一次披露方法可能重复 |
| stock_comparison_real / 茅台和五粮液对比 | 个股对比、财务质量、估值、分红 | 前三者贴合，分红不在原题重点，可能额外扩展 |
| technical_structure_real / 宁德时代技术 | 技术结构 | 专项命中，兼容单个 skill_id 参数 |
| dividend_analysis_real / 神华分红持续性 | 分红、财务质量 | 合理互补，超出预设集合不等同于选错 |
| consensus / 茅台机构共识分歧 | 研报、个股 | 研报专项命中，允许总分组合 |
| sensitivity / 糖蜜涨价影响安琪 | 个股、研报、业绩 | 研报命中；业绩 Skill 面向披露，情景分析额外使用它是否必要待验证 |
| revisions / 安琪机构盈利预测修订 | 研报、个股 | 研报专项命中 |
| RTE001 / 泸州老窖金融科技布局 | 个股、研报 | 总分组合合理；额外请求未开放的数据目录；不意味着题目假设真实 |
| RTE003 / 鹏鼎与沪电预测增速差异 | 对比、研报、业绩 | 研报命中；预测和已披露业绩的方法边界需关注 |
| RTE005 / 今世缘各机构估值方法 | 研报、估值、个股 | 专项命中；估值方法作辅助有合理性 |
| raw_quote_control / 宁德时代收盘价涨跌幅 | 个股 | 预期可走空集通用取数，实际额外加载分析方法 |

这里有两个不同问题：

1. **入口覆盖已体现，精简程度仍需观察。** 当前样本没有漏掉核心分析方法，但多选较积极。部分组合明显合理，部分可能重复或超出题目重点。由于只测试选择，没有执行方法，不能用此结果断言其后一定重复取数或最终效果差。
2. **预设集合评分也有局限。** 比如“分红 + 财务质量”“业绩 + 财务质量”被事先较窄集合扣分，但业务上可解释。本报告保留 5/18 的原始分数，不在看到结果后改 gold 提高成绩；后续应在新样本运行前明确定义核心方法、可选辅助方法及不必要扩展，不能把合理组合当作路由错误。

3 次未开放工具调用是实际模型返回，而非工具真的被执行。捕获到的工具 schema 只有 Skill 与 reference 两项；说明问题不只是 Skill 文案，也包含模型对当前工具阶段的遵守。本轮未修改阶段策略、增加校验或为单题补提示词。

复跑命令（需保留脚本引用的历史输出文件；路径须选择新的输出目录）：

```sh
.venv/bin/python scripts/eval_skill_selection_only.py --output outputs/skill_selection_only_new_run
```

可用 `--case consensus` 只跑其中一题。此脚本不会主动执行研报查询、行情查询或返回分析报告。

## 4. 描述写法调研（只调研，不据此再次修改或测试）

### 公开实践对比

| 框架/产品 | 对入口描述的做法 | 对 Fin Agent 的启发 |
|---|---|---|
| Agent Skills 规范 | YAML `name`、`description`；描述同时说明做什么及何时使用，后续正文按需加载 | 保留一个自然语言入口，方法细节进入正文与 references |
| Claude 官方作者指南 | 简洁具体，覆盖能力和触发场景；强调第三人称，举例是动作能力加 `Use when` | 写用户问题和实际产出，不用“高级专家”等身份口号代替用途 |
| VS Code / Copilot | 同样使用 what + when；自动发现和用户命令入口由独立元数据控制 | 描述表达业务用途，系统负责可见性和调用入口；不要把权限或优先级塞进描述 |
| LangChain Skills | 单 Agent 按需加载方法，支持轻量组合、层级能力和参考资源 | 总 Skill 与专项 Skill 可以重叠并组合，没必要靠互斥描述维持目录 |
| Anthropic Financial Services 实例 | 可比公司分析描述列出分析产物、指标和适用任务，也列出不适用情况 | 可参考具体任务表达；其负向排除、固定 Excel 产物不直接照搬到本项目 |

来源：[Agent Skills 规范](https://agentskills.io/specification)、[Claude 作者指南](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)、[VS Code Skills](https://code.visualstudio.com/docs/agent-customization/agent-skills)、[LangChain Skills](https://docs.langchain.com/oss/python/langchain/multi-agent/skills)、[Anthropic 可比公司分析实例](https://github.com/anthropics/financial-services/blob/main/plugins/agent-plugins/market-researcher/skills/comps-analysis/SKILL.md)。

没有一个已经被这些文档共同证明最优的固定句式。甚至语气建议也有区别：Claude 作者指南强调第三人称，Agent Skills 的描述优化指南建议 `Use this skill when…` 式指令表达。共同点是**用途明确、用户意图明确、简洁、真实触发评测**，不是必须使用某一种英文模板。[描述优化指南](https://agentskills.io/skill-creation/optimizing-descriptions)

### 后续可比较的两种格式（本轮未应用）

- **用途先行**：围绕什么对象完成什么分析。适用于哪些用户问题。支持哪些主要结果或必要组合。
- **意图先行**：当用户希望判断/比较/解释某类问题时使用。结合哪些关键证据形成什么结果。

两个版本保持业务范围和方法正文相同，才能比较句式本身。本轮整理后的描述属于用途先行；没有在看到结果后切换句式。正向定义研报中的“机构观点/预测证据”，与个股分析中的“公司与投资判断”，比相互列禁止项更符合本项目的自由组合目标。这是结合项目的判断，不是外部规范要求。

### 后续评测建议（本轮未扩展实施）

描述优化指南建议真实正例、近邻反例、重复运行和固定训练/验证划分，并将触发评测与最终产出评测分开。可借鉴其测试早停、自然问法及避免逐题补关键词的做法；此处只记录建议，本轮不进行描述优化循环。[来源](https://agentskills.io/skill-creation/optimizing-descriptions)

对 Fin Agent 还需区分首轮核心方法命中、辅助方法是否必要、是否先读方法再取数、后续按证据调整方法，以及最后结论的业务质量。多选本身不等于更智能或更差，需看每个方法对问题是否有实际贡献。

## 5. 验证与交付范围

- 现有目录、业务 Skill 与组合加载测试：35 passed。
- 新增选择评测 scorer / 单次请求重放测试：5 passed。
- 未运行全量测试、完整金融数据执行、Chat HTTP 入口、前端、个人 Skill 选择或服务器部署。
- 本轮新增/修改仅为 Skill 描述、评测脚本/测试与说明文档；工作区中其他已有运行时改动予以保留。
- 未提交、推送或同步本轮修改到服务器。
