# Agent Surface Protocol v1

状态：核心协议候选定稿，允许实现；业务子协议和渲染器注册表继续演进。

## 1. 目标与边界

`agent_surface.v1` 是金融 Agent 后台与前端之间的业务展示和交互协议。它不替代模型供应商协议、Agent Harness 事件、MCP、A2A 或具体工具结果协议，而是在这些协议之上提供稳定的业务语义层。

它需要同时支持：

- 普通金融问答的自然对话；
- 金融数据查询、分析、图表和证据展示；
- 多步骤规划、工具调用、回退与重试；
- 金融工具和因子的设计、实现、验证、修订与发布；
- 流式增量渲染、暂停恢复、审批和补充输入；
- 面向普通金融用户的简洁界面，以及面向高级用户的可展开细节；
- 数据时点、口径、来源、质量和计算过程的追溯。

协议不负责：

- 规定具体页面布局或视觉样式；
- 让模型生成 HTML、JavaScript 或任意组件树；
- 将某个供应商的原始事件直接暴露成业务 UI；
- 把因子定义、行情查询等全部业务字段塞入顶层通用协议；
- 用 UI 协议替代工具输入输出 Schema、运行时状态或持久化模型。

## 2. 适合本项目的判断标准

参考设计只有同时满足以下标准才进入核心协议：

1. **用户可理解**：非开发者能看懂当前结论、依据、进展和下一步。
2. **模型可可靠生成**：模型只需选择少量稳定语义，不承担细粒度 UI 编排。
3. **金融可追溯**：重要数据和结论能说明时点、口径、来源、质量与是否模拟。
4. **运行可恢复**：长任务、审批、补充输入、断线和重试有明确状态与游标。
5. **前端可演进**：同一业务块可因客户端能力不同采用不同渲染器，并始终有降级展示。
6. **权限可控制**：模型可以提出交互意图，但不能直接定义可执行命令或绕过审批策略。

## 3. 总体分层

```text
Provider / Codex / MCP / Tool 原始事件
                    |
                    v
        Runtime Adapter（供应商适配）
                    |
                    v
  agent_surface.v1 Event + Semantic Block
                    |
                    v
   Surface Compiler（权限、来源、渲染选择）
                    |
                    v
 Renderer Registry + Client Capabilities
                    |
                    v
       Web / Mobile / TUI 具体界面
```

必须保持三个边界：

- **原始事件层**保留供应商细节，用于追踪、重放和调试。
- **业务协议层**表达用户能理解的内容、工作、制品、证据和交互。
- **渲染层**把业务块映射到表格、K 线、流程图、表单等具体组件。

## 4. 核心对象层级

```text
Context
  Run
    Surface
      Section
        Block
```

- `context_id`：连续对话和共享上下文的标识。
- `run_id`：一次可暂停、恢复和完成的工作执行。恢复审批时沿用原 `run_id`。
- `surface_id`：一个可独立重建的业务展示面，通常对应一次助手响应或一个持续更新的工作区。
- `section`：按信息作用组织块，不按具体控件组织。
- `block`：最小的稳定业务语义和交互单元。

简单问答可以只有一个 `primary` section 和一个 `narrative` block；复杂工具设计可以在同一 Surface 内逐步出现 process、artifact、assessment 和 interaction section。协议不要求每次都创建“任务大屏”。

## 5. 七类语义块

七类是冻结的顶层分类。新增金融能力优先通过 `semantic`、`content_type` 和版本化子 Schema 扩展，不新增顶层 block kind。

### 5.1 `narrative`

自然语言内容，包括回答、解释、假设、推理摘要和阶段小结。只允许 `plain` 或 `markdown`，不承载可执行 HTML。

### 5.2 `data`

内联结构化数据。稳定形状为：

- `scalar`
- `record`
- `records`
- `series`
- `timeseries`
- `graph`
- `hierarchy`

`table`、`line`、`bar`、`pie`、`kline`、`flowchart`、`mindmap` 都是渲染选择，不是顶层业务类型。例如 K 线是 `data + timeseries + finance.ohlcv`。

### 5.3 `workflow`

统一表达计划、任务列表、步骤进度、工具调用、验证过程和问题修复循环。它取代互相割裂的 todo、process state、tool status 和 coding progress。

工作项状态统一为：

`pending | running | waiting_user | succeeded | partial | skipped | blocked | failed | cancelled`

### 5.4 `artifact`

可持久、可版本化、可评审的工作产物，例如金融工具规格、因子规格、查询定义、报告、代码包或数据集。聊天消息用于交流，Artifact 用于交付；工具设计中的设计稿不能只存在于一段 Markdown 中。

业务内部结构由 `artifact_type + content_schema_version` 指向独立 Schema，例如 `finance.factor_spec.v1`，不扩张通用协议。

### 5.5 `assessment`

结构化判断与检查，包括数据质量、覆盖率、风险、测试、合规边界和设计评审。它不等同于“风险提示文本”，而是包含总体状态、维度、问题、证据引用和可选置信度。

### 5.6 `resource`

引用而非内联的数据或材料，包括附件、数据源、网页、研报、生成文件和工具输出资源。资源有稳定 ID、URI、媒体类型、关系、访问级别和新鲜度信息。

### 5.7 `interaction`

需要用户提供输入或作出决定的协议对象，包括澄清、确认、审批、选择、编辑、认证和重试。模型只生成意图和受限 Schema；后台根据策略补齐可执行 action，前端只回传 `action_id` 和结构化值。

## 6. Section 角色

Section 使用信息作用而不是页面位置：

| role | 用途 |
|---|---|
| `primary` | 当前主要回答、结论或工作焦点 |
| `process` | 计划、进度、工具调用和修复循环 |
| `evidence` | 数据、来源、引用和质量说明 |
| `artifact` | 可评审、可编辑、可发布的产物 |
| `interaction` | 当前需要用户处理的输入或决策 |
| `diagnostic` | 默认折叠的技术诊断、日志和高级细节 |

Section 不是卡片规范，也不固定左栏、右栏或抽屉。一个对话过程中可以根据阶段增加、更新或折叠不同 Section，这比维护五套互斥原型更符合项目实际。

## 7. 内容语义与渲染解耦

Block 可以提供非强制 `presentation_hint`，但最终渲染由 Surface Compiler 决定：

```text
block.kind
  + payload.shape
  + semantic/content_type
  + client.capabilities
  + user preference
  -> trusted renderer
```

典型映射：

| 业务块 | 首选渲染 | 降级渲染 |
|---|---|---|
| `data/timeseries/finance.ohlcv` | K 线 | 时间序列表格 |
| `data/records` | 表格 | 结构化列表 |
| `data/graph` | 流程图 | 节点和边列表 |
| `data/hierarchy` | 脑图/树 | 分级列表 |
| `artifact/*+code` | 代码查看器 | 只读文本/文件链接 |
| `workflow` | 步骤状态/任务列表 | 状态列表 |
| `interaction` | 表单/选项/审批条 | 文字问题和安全 action |

普通用户默认看到业务名称、摘要和验证结果。代码、命令和文件 diff 进入 `diagnostic` 或 Artifact 的高级详情，不能成为工具设计的默认主界面。

## 8. 金融领域上下文

`domain_context.namespace = "finance"` 是横切元数据，不新建大量金融 block type。对决策有影响的数据块应按适用情况携带：

- `as_of` 和 `timezone`
- `markets` 与标准化 instrument 标识
- `currency`、`unit`、`frequency`
- 复权口径 `adjustment`
- `data_mode`：实时、延迟、收盘、历史、估算或模拟
- `calendar`、`universe` 和可选延迟信息

这些字段由数据工具和适配器优先填写，不要求模型猜测。缺失关键口径时生成 `assessment`，而不是制造看似精确的图表。

金融高风险输出还应遵循：

- 关键结论通过 `evidence_refs` 指向 data/resource block；
- 实盘数据、估算数据、模拟数据必须可区分；
- 数据查询、计算、模拟和产生外部影响的操作分级授权；
- “结果为空”和“工具执行失败”使用不同 assessment issue code；
- Artifact 发布前关联验证和数据口径检查结果。

## 9. 模型合同与运行时合同

### 9.1 模型只需生成

- 业务 Skill 只生成与其配套 Output Schema 一致的领域结果，不生成 Surface、Section、Block 或 action；
- 通用回答或专门的展示规划模型可以生成 `ModelSurfaceDraft`，但它不是业务 Skill 的强制外壳；
- Surface Compiler 根据领域结果生成 section、block、interaction intent 和 Artifact 子协议引用；
- 模型可以提出问题、字段和交互意图，但不能生成可执行 action；
- `presentation_hint` 只允许作为可忽略建议，不能决定具体组件。

这意味着金融工具 Design Skill 的 `status / understanding / questions / design / existing_analysis` 是领域合同；`agent_surface.v1` 是该结果通过系统编译后发给客户端的展示合同。两份 Schema 有明确映射，但不一一复制造成双重模型负担。

### 9.2 运行时必须补齐或校验

- 所有稳定 ID、`seq`、`revision`、时间戳；
- run/block 状态和合法状态迁移；
- tool call、来源、时点、追踪和审计信息；
- action 与后端 handler 的安全绑定；
- 审批范围、风险级别和权限；
- renderer 选择、客户端降级和资源访问控制；
- 模型输出 Schema 校验与不可信内容净化。

前端不得执行模型提供的 `command`、URL 脚本或组件代码。Interaction 只能提交后台签发的 `action_id` 和 `resume_token`。

## 10. 流式事件

事件 Envelope 使用稳定的 `event_id`、`run_id`、单调递增 `seq`、目标实体和修订号。核心事件：

```text
run.started
run.status
run.finished
surface.snapshot
section.upsert
section.remove
block.create
block.append
block.patch
block.complete
block.remove
interaction.resolved
stream.error
stream.heartbeat
```

规则：

1. `surface.snapshot` 是可重建基线；增量事件只描述基线后的变化。
2. 文本流优先用 `block.append`，结构变更使用 RFC 6902 `block.patch`。
3. `event_id` 用于幂等去重，`seq` 用于检测缺口，`revision` 防止旧更新覆盖新状态。
4. 客户端发现 seq/revision 缺口时请求新 snapshot，不猜测合并结果。
5. `transient=true` 的通知不进入消息历史；重要结论、审批和 Artifact 不得 transient。
6. `commit_state=provisional` 可即时显示；完成校验和持久化后发 `committed` 更新。
7. `run.finished` 只在持久化、审批记录和必要的收尾工作完成后发送；最后一个 token 不等于 run 完成。
8. Stream 断开不代表任务失败。客户端用 `run_id + after_seq` 恢复或获取 snapshot。

## 11. 暂停、审批与恢复

`waiting_user` 是可恢复状态，并使用 `wait_reason` 区分：

`input | approval | authentication | external | rate_limit`

Interaction response 至少包含：

- `interaction_id`
- `action`：`submit | choose | accept | decline | cancel | retry`
- 结构化 `values`
- `expected_revision`
- `idempotency_key`
- 后台签发的 `resume_token`

Interaction 有两种提交方式：

- `submission_mode=conversation`：用于需求澄清和自然语言修改。`fields` 描述问题和可选值，用户最终仍可通过统一对话输入框回答；不要求 action。
- `submission_mode=action`：用于确认、审批、重试等确定性状态迁移。必须包含后台签发的 action、`subject_ref`、`subject_revision` 和 `resume_token`。

设计确认的 subject 必须指向某一版 Artifact。用户修改后 Artifact revision 增加，旧确认响应因 `expected_revision` 不匹配而失效。

审批是 Run 的中断点，不是普通按钮。审批结果持久化后恢复原 run；不额外伪造一个新用户任务。对于会交易、发布、写数据或访问敏感资源的动作，必须展示影响范围和一次性/本次运行/长期授权范围，长期授权不能由模型自行提供。

## 12. 安全、幂等与可观测性

- 模型输出、工具输出和外部 Resource 都按不可信数据处理。Markdown 需要净化，URI 需要协议与域名策略校验，客户端不得自动抓取未知 URL。
- Renderer 必须来自客户端受信注册表。未知 renderer 或 content type 使用通用降级渲染，不能动态加载模型指定代码。
- Interaction 使用后台签发的 `action_id + resume_token`，提交时校验用户身份、`expected_revision`、授权范围和过期时间。
- 具有外部影响的操作必须携带幂等键；重试沿用同一业务调用 ID，并记录 attempt、timeout、error code 和最终结果。
- `event_id` 去重、`seq` 检测丢包、entity `revision` 防并发覆盖；三者用途不同，不能互相替代。
- 大型表格、行情和二进制内容通过 Resource 引用传输，内联数据受客户端 `max_inline_bytes` 限制。
- Trace、供应商原始事件、完整工具参数和 token/cost 属于可观测数据，默认进入 operator/diagnostic 通道，不进入用户主回答。
- 日志、Provenance 和持久化 RunState 必须移除凭据、账户敏感字段和不必要的个人信息；调试便利不能覆盖数据最小化原则。
- 外部内容中的指令不改变系统权限、工具许可或渲染策略。资源引用只构成证据，不自动获得执行能力。

## 13. 借鉴与取舍

| 来源 | 吸收 | 调整后吸收 | 明确不采用 |
|---|---|---|---|
| OpenAI Agents SDK | 原始事件/RunItem/Agent 生命周期分层；可序列化 interruption | 审批统一映射为 interaction + waiting_user | 把供应商 RunItem 直接作为业务块 |
| Codex | thread/turn/item 生命周期和 item start/update/complete | todo、命令、文件变更归入 workflow/artifact/diagnostic | IDE 文件和命令作为产品主导航或顶层业务类型 |
| OpenCode | 可判别 tool state、step、snapshot、retry | 技术细节默认折叠并提供业务摘要 | shell/patch 直接主导普通用户界面 |
| pi-mono | turn snapshot、save point、非阻塞事件消费、明确 phase | `provisional/committed` 与恢复规则 | 把 Harness 内部所有 phase 暴露给前端 |
| LangChain | 跨供应商标准 content block 与 provider adapter | 保留 escape hatch 但必须命名空间和有降级 | 复制全部多模态/供应商块为业务顶层枚举 |
| LangGraph | messages/tasks/checkpoints/custom 分流；interrupt 可恢复 | 统一成一个有 channel 语义的 Event Envelope | graph node 名和内部 state 直接成为用户 UI |
| AG-UI | lifecycle、start/delta/end、snapshot + RFC6902 delta | 使用更少的业务事件并增加 revision/commit | 任意 custom event 无治理扩张 |
| Vercel AI SDK | persistent/transient part、同 ID reconciliation | 作为前端适配目标 | `data-*` 任意类型成为核心协议 |
| MCP | progress token、structuredContent、outputSchema、resource、受限 elicitation | Interaction 使用受限 JSON Schema，敏感信息走安全外部流程 | 把 MCP 请求响应原样渲染给用户 |
| A2A | Message/Task/Artifact 分离，input/auth required | 复杂工作才创建正式 Run/Artifact | 每个简单问答都包装成长任务 |
| A2UI | 可信组件目录、声明式数据、增量更新、客户端原生渲染 | 后续可在 interaction 内试验受控布局模板 | v1 让模型生成任意组件树 |
| Hermes | todo、子任务、审批和详情折叠的用户体验 | 子 Agent 归入 workflow，默认只显示目标和结果 | 默认暴露模型、token、文件和内部代理拓扑 |
| Open WebUI | SSE、replace、citation 的简单兼容思路 | 作为旧事件迁移输入 | 继续维护松散的 status/message/source 事件集合 |

本地代码审阅的主要落点包括：`../ref_repos/codex/sdk/typescript/src/{items,events}.ts`、`../ref_repos/openai-agents-python/src/agents/{items,stream_events}.py`、`../ref_repos/opencode/packages/schema/src/{session-message,session-todo}.ts`、`../ref_repos/langchain/libs/core/langchain_core/messages/content.py`、`../ref_repos/langgraph/libs/langgraph/langgraph/types.py`、`../ref_repos/pi-mono/packages/agent/src/{types,harness/types}.ts`、`../ref_repos/hermes-agent/ui-tui/src/types.ts` 和 `../ref_repos/open-webui/backend/open_webui/socket/main.py`。

公开设计依据：[OpenAI Agents streaming](https://openai.github.io/openai-agents-python/streaming/)、[OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)、[LangChain content blocks](https://docs.langchain.com/oss/python/langchain/messages)、[LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[AG-UI events](https://docs.ag-ui.com/concepts/events)、[AI SDK stream protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)、[MCP schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)、[A2A specification](https://a2a-protocol.org/dev/specification/)、[Google A2UI](https://developers.googleblog.com/en/a2ui-v0-9-generative-ui/)。

## 14. 对现有代码的迁移映射

| 当前对象 | v1 映射 |
|---|---|
| `markdown` | `narrative` |
| `table/bar/line/pie/kline/flow` | `data` + shape/semantic + presentation hint |
| `code` | `artifact`，普通用户默认 details |
| `action` | `interaction`，后台签发 action |
| `status` / todo / tool progress | `workflow` |
| `facts/risks` | `assessment` + evidence refs |
| `source/citation/file` | `resource` |
| `runtime_feedback.v2` | `run.status` + workflow/assessment block |
| `render_payload.sections` | `surface.sections`，按 section role 组织 |
| `display_contract` | Surface Compiler 的 renderer registry 输入，不再作为模型的控件清单 |

建议保留 `LlmStreamBlockBuilder` 作为旧协议适配器，新增 v1 builder 后逐路由切换，不原地破坏现有前端。

## 15. 冻结项与扩展项

v1 冻结：

- Event Envelope 的 ID、seq、revision 和 snapshot/delta 规则；
- 七类 block kind；
- 六类 section role；
- workflow 状态集合；
- Interaction response 和安全 action 原则；
- Artifact 与 Message/Workflow 的边界；
- renderer 只接收声明式数据和可信注册组件。

允许注册表扩展：

- `semantic` 和 `content_type`；
- Artifact 子协议及版本；
- Assessment issue code；
- renderer ID 与客户端能力；
- 金融 instrument、市场、频率和数据口径字典；
- `annotations` 中的命名空间字段。

任何新增顶层 block kind、状态或事件类型都需要协议版本升级和迁移说明，不能由单个业务模块临时添加。

## 16. 文件与下一步

- Schema：`src/protocols/agent_surface_protocol_v1.schema.json`
- 复杂样例：`docs/protocol_examples/agent_surface_v1_financial_tool_design.json`

实现顺序建议：先做 v1 领域模型与旧事件适配器，再做 Surface Store/重放，随后接入 renderer registry，最后改造工具设计原型。这样每一步都能被现有接口和前端独立验证。
