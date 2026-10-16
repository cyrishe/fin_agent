# DeepCode、Pi 与 Fin Agent CC/DSH：源码和开源接入案例对照

日期：2026-09-24。本文是静态源码调研与架构推演，不是运行性能基准，也不是迁移实施方案的验收结果。

## 一句话结论

四者都有「模型—工具—反馈—继续」循环，但不是同一层的平替：**CC 是以 Claude Agent SDK/CLI 为核心的受控接入；DSH 是可组合的 Agent 运行时；Pi 同时提供低层循环、可嵌入的 coding-agent SDK/RPC 和新一层 durable harness；DeepCode 已是一套以软件工程任务为中心的完整本地应用，并保留专门的 Paper2Code 工作流。**

对 Fin Agent 而言，**若只是尽快交付有工具、Skill 和对话的业务 Agent，CC 更接近现成运行底座**；DSH 和 Pi 的价值在需要控制 Agent 运行协议时才明显；DeepCode 更适合工程任务产品或专门的 Coding/Test 执行者。Pi 是值得做隔离 PoC 的运行时备选；DeepCode 更值得借鉴其工程任务、Goal、权限和恢复实践。二者均不会自动提供金融数据的 `result_ref`、会话归属、列投影、全量快照和前端 Surface/Renderer；这些必须继续由 Fin 的 HARD 协议持有。没有同模型、同工具、同数据的实测前，不应声称任何一方更省 token、更快或效果更好。

## 业务开发者首先要区分的四层

| 层 | 开发者的问题 | 本项目的例子 |
|---|---|---|
| Agent 内核 | 谁处理模型调用、工具循环、流式、会话、重试、压缩？ | CC/DSH 当前承担；Pi 或 DeepCode 可替换其中一部分 |
| Agent 装配 | 怎样暴露工具与 Skill，决定何时入模、能否执行、是否继续？ | CC 的 `ClaudeAgentOptions`；DSH 的 profile、MCP、policy plugin；Pi extension；DeepCode Session/Workflow |
| 业务 HARD 协议 | 什么结果是真的，谁有权读，阶段如何确认，修订如何生效？ | Fin 的 `result_ref`、owner、Design 版本、候选激活、测试证据 |
| 业务体验 | 怎样询问、展示、分页、恢复与解释？ | Fin 的金融列表 Renderer、Design/Coding/Direct 界面 |

用户说的“CC 的框架可能不需要再开发，直接构建业务流程”，**对前两层大体正确**。当前 [Claude Agent SDK options](https://github.com/anthropics/claude-agent-sdk-python/blob/2b87034f571b75797b976b3f32a6dbe7a03f20eb/src/claude_agent_sdk/types.py#L1962-L2038) 已暴露内置工具选择、MCP、系统提示、权限、会话；[后续字段](https://github.com/anthropics/claude-agent-sdk-python/blob/2b87034f571b75797b976b3f32a6dbe7a03f20eb/src/claude_agent_sdk/types.py#L2267-L2323) 有子 Agent、Skill、插件；最新 SDK 还允许[外部 transcript store 镜像及恢复](https://github.com/anthropics/claude-agent-sdk-python/blob/2b87034f571b75797b976b3f32a6dbe7a03f20eb/src/claude_agent_sdk/types.py#L2380-L2404)。它虽源于 Claude Code，但 Anthropic [官方亦以邮件 Agent 举例说明非编码用途](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)。业务开发者通常**不该重写模型循环**，而应直接提供工具、Skill、权限策略与业务输出。我们自己的 CC 接线正是[注册 Finance MCP、选 Skill、设置工具与 resume](../../src/services/finance_claude_session_service.py)。

但“无需开发框架”不等于“无需开发业务后端”。[ClaudeSDKClient](https://github.com/anthropics/claude-agent-sdk-python/blob/2b87034f571b75797b976b3f32a6dbe7a03f20eb/src/claude_agent_sdk/client.py#L26-L55) 管对话与控制流，不管金融结果是否属于当前用户，也不替我们维护设计修订和激活事务。[Fin 的候选修订与激活](../../src/services/custom_tool_service.py) 仍是宿主的权威状态。Skill 可以决定怎样理解与表达，但不能代替 HARD 归属和状态转换。这是对该判断最重要的修正。

### 开发者实际要写、要维护什么

| 选择 | 第一版业务 Agent：最少自己写什么 | 当需求变成复杂业务系统时，新增的框架层工作 | 何时会合适 |
|---|---|---|---|
| **CC / Claude Agent SDK** | 业务工具/MCP、Skill/提示、运行配置和结果适配；循环、会话与交互控制现成 | 对精确上下文选择、特殊工具结果投影和分阶段协议，优先放在宿主工具与业务层；若一定要修改内核请求组装，可用公开钩子/会话能力受限，不能把闭源 CLI 当自有框架改 | 模型路线可接受，目标是尽快交付多步业务 Agent；工具/API/Skill 足以表达大部分流程 |
| **DSH** | 先选 profile，再接模型、MCP、工具和宿主；其 SDK 已能驱动循环 | 细化 profile、Cordis plugin、请求历史、工具前后处理、审计与资源边界；自由度高，也由团队承担装配和升级验证 | 需要请求级上下文控制、工具结果协议、跨模型路由或深层可观测，并愿意长期拥有 Agent 基建 |
| **Pi** | 选 coding-agent SDK 或 RPC，关掉不适合业务的默认读/写/命令工具，接业务自定义工具 | 用 extension/host 服务承载上下文筛选、权限、租户、持久业务事实、事件到产品 UI 的映射；不必重写 Pi 循环，但要自行搭业务应用壳 | 希望在 TS 或 RPC 宿主里快速定制 Agent、掌控工具和模型接线，但不想维护 DSH 的组合树 |
| **DeepCode** | 对仓库修复/科研复现几乎可直接用 CLI、Desktop、Web 或 headless 工作流 | 若改做金融业务后端，要替换 coding 默认 persona/工具，接租户/金融结果协议，并决定是复用其本地应用服务还是只取 AgentRunner；两种路线都不是简单配置 | 任务本身是 Coding/Test/研究复现，或需要完整本地工程 Agent 产品 |

上表的“最少自己写”是工程判断，不是已测开发工时。四者都无法替代 Fin 的 owner、版本、数据结果和 Renderer。它们的区别在**交付一个可用业务 Agent 之前，你要拥有多少 Agent 框架装配代码**，而不是谁的功能清单最长。

有两个容易误判的接口细节。其一，CC 的 `allowed_tools` 是**免审批**列表，限制内置工具是否出现要看 `tools`；Skill 名单是上下文过滤，[不是文件隔离](https://github.com/anthropics/claude-agent-sdk-python/blob/2b87034f571b75797b976b3f32a6dbe7a03f20eb/src/claude_agent_sdk/types.py#L1965-L1985)。其二，DSH 的 [最新 `sdk-minimal` 组合](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68/packages/bundle/sdk-minimal/README.md#L10-L12) 刻意不含完整 Skill/compaction/设置等底座，默认反而有可改宿主文件的 shell；Fin [明确禁掉 shell/editor 并接 finance MCP](../../config/deepseek_harness/finance_query.patch.yml)。因此不能把“DSH 有某能力”直接说成“Fin 选的 minimal profile 开箱即有”，也不能把较多自定义代码一律算成业务必需。

判断是否值得改 harness，可以用四个已出现的需求做切分：

1. **“大表不要全送模型”**主要是业务工具与结果协议问题：存完整快照、返回摘要和引用、提供按列/页回读，CC/DSH/Pi/DeepCode 都可沿用，先不改框架内核。
2. **“完整对话留档，但独立本轮不带旧历史入模”**是请求视图问题：DSH 已有可重放的 `request/history`；Pi 可在模型请求前的 context transform 实现；CC 最新的外部 `session_store` 是 transcript 保存/恢复，不等于请求级选择。尚未在已核对的 CC 公开 Python options 中看到与 `fromTurn` 等价的接口，所以在 CC 上宜由宿主选择新 session/明确摘要，或先验证更合适的公开能力，而不是直接改写私有 CLI。
3. **“数据归属、版本激活、结果可追溯”**是 Fin HARD 协议问题：这些检查必须在宿主和工具端，改模型循环不会替代事务与权限。
4. **“每一步工具结果要裁剪、阶段工具要动态收窄”**才可能值得使用 DSH/Pi 的原生钩子；但只在实际 trace 显示重复 token、误调用或阶段失控时引入，且保持工具 schema/业务资产不因一次 case 改形。

### 按我们实际要交付的业务选，而非按框架名选

| 任务 | 目前最有利的路线 | 为什么；什么变化会改变判断 |
|---|---|---|
| 金融问答、目录查找、取数与 Skill 解释 | **CC 最快交付；现有 DSH 可在证据充分时承担高频主线** | CC 已给会话/工具/Skill；Fin 保有数据与权限。DSH 的额外价值是现有请求历史与结果投影，必须靠同题 trace 证明收益足以覆盖维护成本 |
| CPO 成分股这类“查完整表、模型少看、UI 全量分页” | **保持 Fin 的结果协议；Agent 运行时不是决定项** | 价值来自工具外置完整表、预览与 `result_ref`。Pi/DeepCode 不会天然比 CC/DSH 少搬运，除非同样接入这个协议 |
| 多轮 Design → Coding → Test → Direct | **Fin 保有阶段资产；Coding/Test 可试 CC 或 DeepCode 工程执行者** | CC 交付速度高；DeepCode 的默认工作空间、命令、验证与 Goal 更贴近实现阶段。但 Design 修订/确认和 Direct 资产读取不能搬成自由聊天 |
| 研究论文 → 代码 → 实验验证 | **DeepCode 的专用 Paper2Code 优先做对照** | 这是它已有的业务工作流，不只是一段通用 prompt；CC/DSH/Pi 也能做，但需要自己设计分解、实验与工件生命周期 |
| 多租户、可控上下文、强金融审计的通用业务 Agent backend | **CC 适合先上线；DSH 适合我们长期自有平台；Pi 可作技术备选；DeepCode 不宜直接作为默认金融后端** | 都需 Fin 的业务硬协议。DSH/Pi 在内核组装上更开放；DeepCode 的默认 prompt 是 coding agent，且当前应用 [WorkflowService 只注册 `paper2code`](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/application/workflow_service.py#L67-L73)，说明完整产品服务尚非通用金融流程 API |

这不是“CC 优于 DSH”或“DeepCode 不能做业务”的绝对结论。它是以我们当前 Fin 产品、团队已写的适配、业务边界和机会成本为条件的选择。若只取 DeepCode [可自定义 `system_prompt` 的 AgentRunner 组装](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/agent_setup.py#L239-L275)，理论上能做非编码 Agent；但这样不会自动继承其全部应用功能，宿主仍需再建业务服务。Pi 的 [SDK 可仅启用指定工具](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/src/core/sdk.ts#L258-L265)，也不是必须 fork 框架才能做复杂业务；真正待验证的是同一业务链路下的质量、隔离、运行成本及长期维护负担。

## 证据边界与版本

本轮读取了以下仓库的代码，而非只看项目介绍：

| 仓库 | 读取的 ref | 用途 |
|---|---|---|
| [HKUDS/DeepCode](https://github.com/HKUDS/DeepCode/tree/807ff34a077f3218a1eb8cc95481da40cc8f781e) | `807ff34a` | Python AgentRunner、应用服务、Paper2Code、MCP 配方 |
| [earendil-works/pi](https://github.com/earendil-works/pi/tree/b45597504eeaba1f11a9920a1d1048c361ed4b8e) | `b4559750`，0.87.1 包线 | agent-loop、AgentHarness、coding-agent SDK、session、extensions |
| [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python/tree/2b87034f571b75797b976b3f32a6dbe7a03f20eb) | `2b87034f` | Python SDK options、Client、CLI 传输边界 |
| [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness/tree/46a7f68) | `46a7f68` | 最新公开 `sdk-minimal` 组合；与 Fin 本地选用版本区分 |
| [earendil-works/pi-chat](https://github.com/earendil-works/pi-chat/tree/9adbd29b40ee27ff1decf0fc87cbe180b40924f5) | `9adbd29b` | Pi 扩展应用；仍依赖旧 `@mariozechner/*` 包线 |
| [dnouri/pilish](https://github.com/dnouri/pilish/tree/7ca4ee574d6a2b14032a9769c5c4cada97b97977) | `7ca4ee57` | Emacs 经 RPC 调用 Pi |
| [SaschaMet/pi-coding-agent](https://github.com/SaschaMet/pi-coding-agent/tree/de92ac6d8c652c7d7779d306ac771741177e4840) | `de92ac6d` | 第三方 Pi SDK + 权限/质量扩展 |
| [openclaw/openclaw](https://github.com/openclaw/openclaw/tree/1f01706bc75c3a96ff8f918c99272336fd8d546c) | `1f01706b` | 纠正「当前仍使用 Pi Agent 内核」的说法 |
| 本仓库 Fin Agent | `9b70f771` 工作树 | 当前金融 CC/DSH 接入与结果协议 |
| 本地 deepseek-harness | `0433ae61`，`deepseek-ai/deepseek-harness` | Fin 当前集成所参照的 DSH 能力；**不代表其最新上游** |

采用的证据等级：执行源码与真实 import/调用 > 示例配置和专门集成指南 > README 宣称。静态阅读可确认接口和执行路径，不能确认生产效果、第三方部署量或真实耗时。项目已有未提交改动，与本报告无关；本轮没有改运行代码或启动外部服务。

## 源码里实际是什么

### Pi：不是只有四个工具的轻量循环

当前 Pi 已有三层：`pi-ai` 多提供方模型适配、`pi-agent-core` 的循环及新的 AgentHarness、`pi-coding-agent` 的 CLI/SDK/Skills/会话/扩展。低层循环在 [agent-loop.ts](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/agent/src/agent-loop.ts#L376-L405) 将 `AgentMessage[]` 先经 `transformContext` 再转为模型请求；[tool execution](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/agent/src/agent-loop.ts#L502-L520) 支持顺序或并发，[beforeToolCall](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/agent/src/types.ts#L304-L329) 可拦截。新 [AgentHarness](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/agent/src/harness/runtime/harness.ts#L374-L408) 提供会话恢复和未完成 operation 的显式恢复信息，不能再按旧印象称它“没有 harness”。

高层 [createAgentSession](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/src/core/sdk.ts#L258-L265) 可限定内置工具、增加自定义工具；[session-manager](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/src/core/session-manager.ts#L469-L510) 维护分支/compaction 感知的模型上下文。扩展文档明确提供 [`context`、`tool_call`、`tool_result`](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/docs/extensions.md#L99-L115) 钩子，且 [`appendEntry()`](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/docs/extensions.md#L169-L180) 可以把会话数据持久化但不送进模型。SDK 和 RPC 都是公开接入面；RPC 是长驻子进程 JSONL 协议，适合 Python/编辑器宿主。[官方 RPC 说明](https://pi.dev/docs/latest/rpc)。

关键边界：Pi 官方源码 [README 的权限说明](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/README.md#L39-L48) 明确没有内建的文件系统、进程、网络、凭据权限隔离。`tool_call` hook 能实现应用策略，但它与 OS 沙箱不同；若启用 `bash` 或不可信扩展，权限不能只靠工具名称白名单。扩展本身与 Pi 进程同权限，[文档也明说这一点](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/docs/extensions.md#L1-L7)。

### DeepCode：一套工程 Agent 产品 + 复用内核的科研复现流程

DeepCode 的核心不是单一 Paper2Code 脚本。通用 [AgentRunner](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/agent_runtime/runner.py#L306-L325) 处理多轮工具循环、权限检查、hook、重试、上下文压缩；[压缩路径](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/agent_runtime/runner.py#L1692-L1759) 先无模型裁剪大工具结果，再按需调用模型总结。[应用服务](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/application/application.py#L67-L155) 组合项目、会话、Skill、插件、MCP、审批、事件、工作树与自动化；这已相当接近一个本地 coding-agent 平台。其 [session runtime](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/application/session_runtime.py#L173-L185) 明确处理其他进程写入的重载和单会话单执行 lease。[GoalRuntimeRouter](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/agent_runtime/goal_runtime.py#L49-L129) 把长任务目标绑定到当前 Turn。

Paper2Code 不是另一个独立底座：[code_implementation_workflow.py](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/workflows/code_implementation_workflow.py#L900-L947) 在科研复现阶段构造 `AgentRunSpec`，接模型、工具、迭代预算、permission checker、回调后调用同一个 `AgentRunner`。这证明框架已在**本仓库的另一条真实工作流**里复用；但不能误称“独立第三方项目已嵌入 DeepCode 库”。该专用 unattended 阶段的默认 permission mode 是 `FULL_AUTO`，直接搬到含客户数据的 Fin 主线不合适。

两套开源运行时并非完全不同的思想谱系：DeepCode 的 [compaction 实现注释](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/agent_runtime/runner.py#L1699-L1729) 明确把先裁工具结果、再模型总结称作 “dsh's ordering”，其 [会话单写者注释](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/core/application/session_runtime.py#L173-L185) 也称 “the dsh rule”。这是源码层面的设计标注；不能单凭注释推断代码来源或两项目的实际性能等价，却说明我们在 DSH 上已有的某些协议经验正在被别的实现采用。

DeepCode 另有 [OpenSpace MCP＋Skill 集成配方](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/docs/integrations/OPENSPACE.md) 和 [可执行配置](https://github.com/HKUDS/DeepCode/blob/807ff34a077f3218a1eb8cc95481da40cc8f781e/examples/mcp/openspace.deepcode_config.json#L1-L40)：把 `openspace-mcp` 放进普通 MCP 目录，导入两个 Skills，限定可用工具、凭据和审批。这证明公开扩展缝能接另一开源 Agent；但配置在 DeepCode 仓库，仍是**官方接入样例**，不是第三方生产运行证据。其文档还指出内外两层审批不能传递：外层批准 `execute_task`，不等于能审查内层每次 shell/文件操作。这对 Fin 接长时子 Agent 尤其重要。

### 我们的 CC/DSH：金融协议在宿主，不在通用循环里

Fin CC 在 [finance_claude_session_service.py](../../src/services/finance_claude_session_service.py) 创建 `ClaudeSDKClient` 和 finance SDK MCP server，禁掉默认 Bash/文件/网络工具，设允许工具、Skill、session resume。DSH 在 [dsh_service.py](../../src/scenarios/financial_qa/dsh_service.py) 使用 Python SDK + `sdk-minimal` profile + finance MCP/policy patch；[dsh_loop_policy.mjs](../../src/scenarios/financial_qa/dsh_loop_policy.mjs) 的 pre-step/post-execute 接口处理阶段、数据投影与历史请求选择。这里的“CC/DSH”是 Fin 的两种 Agent 执行路径，不应与 Fin 的整个业务系统混称。

真正不可丢的是 Fin 自己的结果契约：[tools.py](../../src/scenarios/financial_qa/tools.py) 把完整查询结果存进 `result_ref`，向模型给 schema、行数与预览，并通过 `load_finance_result` 按 `filter/order/offset/limit/columns` 再读；[SessionVariableStoreService](../../src/services/session_variable_store_service.py) 对 `session_id` 与 `data_ref` 做归属校验，只分页读取授权快照。业务界面读权威结果，不靠模型复述大表。独立对话的判断在 [financial_qa/service.py](../../src/scenarios/financial_qa/service.py) 复用语义解析的 `context_resolution_source == "llm"` 且 `context_refs == []`；DSH 通过 [原生 `request/history` 事件](../../src/scenarios/financial_qa/dsh_loop_policy.mjs) 从本轮构造模型请求，归档仍留在 session。旧 DSH 不支持该方法时保留完整历史，属于效果优先的兼容退路。[此前自适应读取分析](dsh_adaptive_reading_review_20260910.md) 说明选择列不等于只靠一条 prompt 就可稳定完成。

## 真正接入 Pi 的开源项目：接入层在做什么

| 项目与代码证据 | 如何使用 Pi | 它自己补上的责任 | 给 Fin 的启示 |
|---|---|---|---|
| [pi-chat `index.ts`](https://github.com/earendil-works/pi-chat/blob/9adbd29b40ee27ff1decf0fc87cbe180b40924f5/index.ts#L901-L1030)、[runtime.ts](https://github.com/earendil-works/pi-chat/blob/9adbd29b40ee27ff1decf0fc87cbe180b40924f5/src/runtime.ts#L67-L245) | Pi Extension 注册聊天工具、驱动模型；`ConversationRuntime` 管外部消息/队列 | 远端身份和触发规则、聊天消息 ledger、`chat_history` 按需回读、[Gondolin 沙箱](https://github.com/earendil-works/pi-chat/blob/9adbd29b40ee27ff1decf0fc87cbe180b40924f5/src/gondolin.ts#L61-L112)、工具限制。其 [context hook](https://github.com/earendil-works/pi-chat/blob/9adbd29b40ee27ff1decf0fc87cbe180b40924f5/index.ts#L1360-L1369) 还能让部分持久内容不随请求入模 | Pi 可做“外部完整事实 + 按需读”，但宿主必须持有身份、数据、读取协议。该仓库仍用旧 `@mariozechner/*`，不能拿它证明已兼容当前 0.87.1 |
| [pilish `pilish-core.el`](https://github.com/dnouri/pilish/blob/7ca4ee574d6a2b14032a9769c5c4cada97b97977/pilish-core.el#L693-L719)、[pilish-jsonl.el](https://github.com/dnouri/pilish/blob/7ca4ee574d6a2b14032a9769c5c4cada97b97977/pilish-jsonl.el#L279-L319) | Emacs 起 `pi --mode rpc`，收发 JSONL、读会话文件 | 编辑器 UI、进程管理、session 树投影/恢复 | Fin 的 Python 后端可经 RPC 接 Pi，但必须处理进程池、session 亲和、取消、协议版本与事件重放 |
| [SaschaMet `src/main.ts`](https://github.com/SaschaMet/pi-coding-agent/blob/de92ac6d8c652c7d7779d306ac771741177e4840/src/main.ts#L16-L103)、[read-boundary-guard.ts](https://github.com/SaschaMet/pi-coding-agent/blob/de92ac6d8c652c7d7779d306ac771741177e4840/.pi/extensions/read-boundary-guard.ts#L58-L135) | 0.87 系 SDK 建 session/runtime，再进入 InteractiveMode | 工作目录边界、[写入范围](https://github.com/SaschaMet/pi-coding-agent/blob/de92ac6d8c652c7d7779d306ac771741177e4840/.pi/extensions/write-boundary-guard.ts#L85-L175)、验证/披露 gate 等 | SDK 可深度嵌入，策略仍是应用工作；其 hook 内规则不能视作隔离沙箱或我们的权限协议 |

一个需要纠正的常见例子：Pi 旧文档/讨论常把 OpenClaw 当现成 Pi 内核接入；当前 [OpenClaw 架构文档](https://github.com/openclaw/openclaw/blob/1f01706bc75c3a96ff8f918c99272336fd8d546c/docs/agent-runtime-architecture.md#L12-L25) 已写明 Agent core 内化为 `@openclaw/agent-core`，**无外部 Agent 框架依赖**；[package.json](https://github.com/openclaw/openclaw/blob/1f01706bc75c3a96ff8f918c99272336fd8d546c/package.json#L2250-L2256) 仅保留 Pi TUI。它可以作为曾经外部依赖后选择内化的维护风险案例，不可列作当前 Pi Agent Core 使用者。

检索 DeepCode 的外部依赖、运行命令与直接 import，核对其可公开的集成文档后，**本轮未找到能验证的独立开源仓库把 DeepCode 作为库嵌入并实际调用其运行时**。这里是证据空白，不是“没有用户”或“不可嵌入”的结论。公开可核实的复用是仓库内 Paper2Code 调用 AgentRunner 和 DeepCode 官方提供的 OpenSpace 接入配方。

## 面向 Fin 的能力矩阵（“可实现”不等于“已经有”）

| 维度 | CC（当前 Fin） | DSH（当前 Fin） | Pi 0.87 | DeepCode 当前 |
|---|---|---|---|---|
| 宿主接入 | Python Claude Agent SDK，模型/供应商栈相对集中 | Python SDK + Node/Cordis profile、patch/plugin，适合协议钩子 | TS SDK 内嵌，Python 可 RPC；多供应商模型层 | Python 服务/CLI/MCP、完整本地产品；可用内核但产品绑定较深 |
| 会话与上下文 | SDK resume，Fin 外部保存业务状态 | DSH session，Fin 已用 `request/history` 精确请求选择 | 分支 JSONL/compaction；request-local `context`/`transformContext`，会话旁路 `appendEntry` | canonical session + lease + compaction；有清 resident history，但未核到与 DSH `request/history` 同等的持久请求选择协议 |
| 工具和扩展 | Finance MCP 工具、Skill、allow/disallow | MCP bridge、`pre-step`/`post-execute`、policy patch | 自定义工具、扩展钩子、动态工具；低层可拦截/改结果 | MCP/Skill/Plugin、权限检查、工程工具、Goal/工作树/审批 |
| 长任务/子 Agent | SDK 可用，但 Fin 金融问答禁用 `Agent`/`Task` | 框架有 subagent 组合能力，Fin 当前金融路径以单主循环为主 | 默认 coding-agent 不预设子 Agent 流程；可用扩展/自定义工具装配 | 原生 Goal、子 Agent、工作树与工程任务协调更完整 |
| 权限边界 | Fin 当前白名单 + SDK 权限模式 + 宿主校验 | Fin 白名单/工具归属 + DSH 钩子；可不发布宿主 shell | 核心无 OS 权限隔离；须由宿主限工具、sandbox 及凭据 | 有工程权限引擎与审批；嵌套 Agent 另需独立边界 |
| 金融完整表、审计与渲染 | **Fin 自己已有** | **Fin 自己已有** | 需保留 Fin 结果库/工具适配/Renderer | 同左；工程型会话模型不能代替金融快照归属 |

这张表只比较当前代码可验证的结构。比如“Pi 支持并发工具”不能推出 CPO 查询会更快；如果模型仍逐页读不必要的列，或者 host 每次把完整表塞入 `content`，任何内核都会付同样的模型代价。

对金融问答做最小接线时，Pi/DeepCode 都应只替换 `Agent 调度` 一格：`用户输入 → Fin 语义与权限 → [Agent 调度] → Fin 授权查询/结果库 → result_ref + 预览 → Fin Renderer`。Pi 的适配器要把自定义工具结果分成模型可见 `content` 与宿主保留的权威数据，并在 Python/RPC 边界校验 owner/session；DeepCode 的适配器可用 MCP 接工具，但不能让 DeepCode 自身的通用 Session 成为第二套金融事实库。若需长任务子 Agent，还必须界定内层权限、成本预算和结果归属，不能将外层一次工具批准推定为内层授权。

### 场景 A：CPO 成分股列表

当前理想路径：Fin 查目录和行情/成分接口，完整结果落权威 store，返回 `schema + row_count + 少量 sample + result_ref` 给 Agent；列表 UI 从 store 分页展示。模型只决定“是否已查到正确板块和列”，不搬运每行数据。用 Pi 实现时，以 SDK `customTools` 或 RPC 扩展接现有 Python Finance 工具，把 `result_ref`/owner 保持在 Python；Pi `tool_result`/`afterToolCall` 只送预览给模型，前端继续用现有 API 读取完整快照。用 DeepCode 则可通过 MCP 接 Finance 工具，但其通用工程 UI/会话和现有金融 Renderer 双持有，集成成本更高；除非想让 DeepCode成为完整交互壳，否则没有明显收益。**提速点在数据投影和业务动作，不在换框架。**

### 场景 B：逐条读一百篇公告并归纳风险

当前 Fin 可按 `result_ref` + `columns` + `offset/limit` 读取；模型决定哪些列必须逐条看。Pi 的 `context` hook、`appendEntry` 和自定义工具可复制“完整数据留外部、按需入模”；DeepCode 的工具裁剪与 MCP 可保模型窗口，均不能替模型判断金融口径，也不能用“只看三行”替代逐条覆盖。对真正逐条判断的任务，成本下限由必读内容决定；可优化的是非目标列、重复 schema、重复历史、重复模型转写。若需快照聚合，优先在 Fin 已存数据上做受约束程序计算，返回可追溯统计与样例。

### 场景 C：金融工具的 Design/Coding/Test/Direct

DeepCode 的 Goal、验证、工作树及长期任务执行，比 Pi 的默认 coding-agent 更贴近 Coding/Test；Pi 要达到相似的阶段体验，需用扩展、持久工件、审批和项目服务装配，第三方 Pi 项目证明这种装配可行，也证明责任不在核心包。CC 已经在 Fin 项目内经 Skill/Output Schema 与资产生命周期衔接；DSH 的 plugin 架构也能承接。Fin 的 `SOFT → HARD → SOFT` 设计资产、修订、归属、阶段确认是业务主线，不应因为 DeepCode 有 Goal 或 Pi 有 Session 而变成大模型自由文本。Direct 更应读现有资产并渲染，换任何 Agent 循环都无益。

### 场景 D：独立追问但保留完整 session

Fin 的语义层已经判断独立性，DSH 可记一条 `request/history`，保留完整归档、仅让本轮进入模型。Pi 可在 `context`/`transformContext` 按当前轮边界选择模型消息，并把独立性/摘要作为非上下文 session entry 保存；需要实现配对安全、跨进程重放和测试。DeepCode 的 `clear_live_history` 仅清驻留模型上下文，canonical history 仍在；结合其重载逻辑看，不能直接等同于 DSH 的持久请求选择，需新的会话或宿主显式请求视图。因此**这项能力上，Fin+DSH 现有实现更接近所需的协议语义**。

## 效率、成本、稳定性、效果：可以从源码推断到哪里

1. **效率/消耗**：Pi 的 `transformContext`、DeepCode 的先 prune 后 summarize、DSH 的 `request/history` 都有减少请求上下文的机制；CC 可通过新 session/外部存储减少历史。机制存在并不等于净节省：summary 自身可能调用模型，Pi 扩展阻塞事件可能增延迟，DeepCode 多阶段/Goal 可能增加调用。相同模型/任务的 token 与耗时必须 trace 实测；不同供应商缓存口径也需分开记。
2. **稳定性**：Pi SDK/RPC 两种接入降低语言摩擦，但 RPC 需要宿主维护进程和版本；DeepCode 应用层已有 lease、durable session、审批和事件服务，但嵌入整个应用会增加运行依赖；DSH 当前在 Fin 已有 worker 池、session 亲和、兼容降级，替换本身就是新的风险源。
3. **安全/健壮性**：DeepCode 权限引擎是优势，但其 Paper2Code unattended FULL_AUTO 模式提醒不能直接复用工作流默认值；Pi 明确无内建 sandbox，第三方 guard 体现的是产品自己的策略。Fin 的 `result_ref` 归属校验必须留在工具/store 侧，不能仅靠任一模型或扩展 hook。
4. **效果**：没有同题实测，不能给四者排效果名次。影响金融结论的主要因素是语义选择、数据口径、取数正确性、逐条覆盖和可回读性；通用 Agent 框架的工程任务能力不能替代金融 scorer。

## 建议：先测“替换价值”，不要先替换主线

若目标是多模型/嵌入式运行时，做一个**隔离的 Pi PoC**：固定当前 Fin 业务工具契约和数据集，用 Pi RPC（Python 后端最少侵入）或一个薄 TS SDK 进程，仅复现三类流：CPO 列表、长表逐条归纳、独立追问。比较 CC/DSH/Pi 的工具序列、入模上下文、用户可见答案、完整行/列覆盖、权限拒绝、取消/恢复、provider 用量与墙钟。先验证 `result_ref` 的拥有者始终是 Fin；不迁移 Renderer、业务状态和代码阶段协议。只有同模型同工具质量不退化、稳定性证据足够且成本确实下降，才考虑扩大范围。

对 DeepCode，可先作为**工程 Agent 对照样本**评估 Coding/Test：固定相同设计输入和验收，用 headless `exec` 跑隔离工作树，查看产物、测试、审批和恢复；不直接嵌入金融问答主链。若未来出现稳定的独立 Python embedding API 与真实外部接入样本，再重估作为通用运行底座的成本。OpenSpace 的嵌套 Agent 配方可用于研究，但金融权限/凭据不能通过外层一次 MCP 批准向内层“传递”。

不建议把现有 DSH 历史选择、结果投影和 `result_ref` 迁出 Fin 去换成框架私有状态。最值得吸收的框架层点是：Pi 的模型请求级上下文变换/非上下文会话条目与 DeepCode 的压缩阶梯、可恢复会话/单写者约束；其中 DSH/Fin 已经覆盖一部分，应按真实 trace 查缺，不重复造协议。
