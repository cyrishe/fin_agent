# 个股研究 Skill 的分段执行与上下文设计

日期：2026-10-07。范围仅限个股研究主入口及其按需调用的专业方法。本文件是设计和验证计划，尚未代表产品已经启用子任务执行。

## 判断

可以让 DSH 管理复杂 Skill 的执行，但不应把每份 Skill 文档直接变成一个常驻 Agent。一个问题仍由个股研究主入口负责最终判断；只有存在可独立回答、且会改变主结论的局部问题时，才以一次性子任务执行专业 Skill。简单事实查询继续在主会话完成。主入口始终拥有用户问题、最终综合和停止扩查的决定权。

这与现状有实质差别：当前 `read_finance_skill` 仅把方法正文加载到当前 Agent，上下文仍是同一个 DSH 会话。`dsh_loop_policy.mjs` 虽按目录、查询、明细、成文阶段收紧工具和预算，却不清除同一轮累积的消息。因此 Skill 组合越多，模型越容易同时看到旧假设、重复取数、不同报告期和多份方法说明。五题同题实测中，单题 8–14 次模型调用、最多 144,111 tokens；紫金矿业的“2.6 倍”倒置发生在来源数字正确之后的成文环节，表明仅增加提示词不足以解决关系交接与长上下文问题。证据见 [同题复核](stock_research_wencai_review_20261007.md)。

## 可借鉴的执行方式

| 方式 | 能力 | 本项目判断 |
| --- | --- | --- |
| 现有 DSH 单 Agent + Skill 逐步加载 | 简单、已有工具和权限链；单轮上下文持续增长 | 保留作为短问题默认路径；无法提供真正的子任务隔离 |
| DSH `spawn` 一次性子 Agent | 子 Agent 从空对话开始，父级只收到最终结果；可配置 persona、工具可见性、深度和模型选项 | 最接近所需的“主入口调专业方法”；先做隔离实验，再接金融权限与证据引用 |
| DSH `fork` 子 Agent | 继承父级已完成轮次 | 不适合作为清洁上下文的默认方案，容易复制旧假设和长历史 |
| DSH `workflow` 工具 | 模型编写 JavaScript 编排脚本，适合大型多 Agent 扇出 | 对常见个股研究过重，增加脚本和 schema 成本；不作为首期方案 |
| OpenAI Agents SDK 的 manager + agent-as-tool | 主 Agent 掌握最终答复，专家承担有界任务 | 借鉴责任分工，不为此替换已有 DSH 运行时 |
| LangGraph/Deep Agents 子图和子 Agent | 显式状态、检查点、独立上下文 | 借鉴“原始证据留在状态、按节点投影上下文”；不再叠加一套图状态机 |

DSH 当前使用 `sdk-minimal` profile，金融 patch 仅加载金融 MCP 与 loop policy，并未挂载 subagent 包。DSH 仓库已有 `dsh-subagent`、`dsh-subagent-spawn-in-process` 和 `dsh-tool-subagent`。这证明运行时具备原语，不等于在 Fin Agent 中直接打开就可用：必须验证子会话和金融 MCP 的执行身份、授权目录、结果引用作用域及预算/trace 是否正确衔接。尤其 DSH 的 `toolFilter` 管的是工具可见性，不会自动继承 Fin Agent 的业务授权。

## 一次复杂个股研究的主线

以“紫金矿业涨价是否真正改善自由现金流，当前估值是否反映”为例：

1. 主入口理解问题，固定标的、截止日、用户时间范围与核心命题。它先从结构化数据取少量直接事实：产量/价格、利润、经营现金流、资本开支、估值。能够直接回答时就停止，不拆子任务。
2. 若现金流质量和估值预期都需要独立判断，主入口分别调用已授权的 `financial-quality-analysis` 与 `valuation-analysis`。每个子任务只收到一个具体问题、同一标的与截止日、所需数据引用和当前缺口；不接收主会话整段对话，也不写整份报告。
3. 子任务在自己的 DSH 会话中使用相应方法与授权数据工具，返回短的“判断—关键依据—反证/缺口”语义说明，并附其实际使用的证据引用。报告期、单位和分子/分母由原始查询结果承载，不让下一阶段猜测。
4. 主入口只接收两份短说明与证据引用，必要时按引用回看原始结果；它决定共同结论和未解决的矛盾。诸如“买矿额/经营现金流”这类会影响判断的比例，交给确定性计算能力得出数值及口径，再由主入口解释。
5. 最终输出一份面向用户的个股结论。执行耗时、token、每个分支的方法版本和工具轨迹保留在 trace，不进入正文。

上述步骤是语义流程，不新增 `researching/reviewing/synthesizing` 一类业务状态枚举。系统只保留主线必需的 HARD 事实；选择分析维度、提出假设和解释矛盾仍是 SOFT 能力。

## 上下文交接

主会话的上下文由三部分投影：用户本轮目标与约束、系统持有的证据索引、已完成子任务的短结论。原始多行查询、全文检索片段和各子任务的中间尝试继续保存在工具结果/子会话中，按引用查看；不复制进每次模型请求。

给子任务的交接保持少量稳定机器信息：证券身份、分析截止日、授权 Skill 快照修订、原始结果引用及调用者归属。自然语言部分只说明“本次要判断什么、哪些已知事实、哪些仍有争议”。子任务回传自然语言判断及用过的证据引用。不要把论证过程拆成多层 JSON、让模型重复系统已经保存的状态，或因可选章节缺失拒绝后续阶段。

不能仅凭“新 DSH 会话”假定可以访问旧结果。现有金融结果引用由 `_agent_runtime_scope` 约束，隔离请求还会生成随机 key。子任务必须由服务端在同一用户授权内使用明确的共享证据作用域，或由服务端投影最小必要数据；不可直接把任意 `result_ref` 当跨会话通行证。子会话也须重新受 Fin Agent 的用户/Skill/工具权限约束，不能因为 DSH 提供方已启动就扩权。结论合并时保留原始来源、报告期、公开时间、单位和复权口径，以免不同数据被误当同一期事实。

## 实施顺序与验收

1. **隔离验证**：在测试用 DSH 配置中挂载一次性 `spawn`（最大委派深度 1），验证空上下文、父级只见短结果、取消/超时、子任务 trace、金融工具鉴权与 `result_ref` 读取。先不用后台可继续任务或 workflow。若原生插件不能可靠继承 Fin 的工具上下文，就以当前 `run_turn` 的新会话调用实现同样的有界子任务，仍由 DSH 执行模型/工具生命周期；避免另造并行状态机。
2. **接入个股研究**：只给主入口增加“独立判断时委派”的可选执行能力，专业 Skill 仍是可单独调用的业务方法。首期只覆盖财务质量、估值/预期和量价结构中确有独立判断的场景；简单数据问题沿原路径。复用同一批已取证结果，避免每个子任务重查。
3. **控制开销**：设置少量并行分支和单分支 token/工具/时限上限，独立任务才并发；停止条件由主入口问题和证据充分性决定。预算是运行策略，不固化为业务状态。子任务必须简短回传，主入口限制最终重复表述。
4. **固定评测**：用 2026-10-07 的五道同题及其中紫金比例倒置、平安银行收益率窗口、中际旭创现金流时点三项回归。分别记录事实/口径错误、有效证据引用、重复查询、输出字符数、端到端时长、父子模型调用与 token。只在准确性至少不下降、冗余和时间/token 有可解释改善时扩大启用。

本设计的首要实验问题不是“子 Agent 能不能回答”，而是“在严格共享证据和权限的前提下，分段是否比当前单会话更准确、更省”。若五题不能证明收益，应保留单会话路径，并先修复具体数据口径与确定性计算问题。

## 调研依据

- 仓库现状：`src/scenarios/financial_qa/dsh_service.py`、`dsh_loop_policy.mjs`、`tools.py`、`config/deepseek_harness/finance_query.patch.yml`，以及 `src/skills/finance-business/skills/stock-research/references/method-composition.md`。
- DSH 本地实现与说明：`/Volumes/ext/fin_harness/packages/bundle/sdk-minimal/cordis.patch.yml`、`packages/subagent/subagent-spawn-in-process/README.zh.md`、`packages/subagent/tool-subagent/README.zh.md`、`packages/workflow/tool-workflow/README.zh.md`。
- 外部模式：[OpenAI Agents SDK 的 agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)、[LangGraph 的状态与节点设计](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph)、[LangChain Deep Agents 的子任务隔离](https://docs.langchain.com/oss/javascript/deepagents/overview)、[Anthropic 的上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)。这些材料支持模式选择，不构成对当前 Fin Agent 效果的实测证明。
