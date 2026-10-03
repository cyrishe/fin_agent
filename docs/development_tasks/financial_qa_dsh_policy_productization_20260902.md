# DSH 金融查数策略产品化记录

## 核心结论：管理对象是提示词注入，不是工具目录

金融查数路径需要管理的核心资产是“哪个业务阶段、以什么语义、向模型注入哪一段提示”，而不是为每个阶段重新造一套工具。工具只承担两类职责：

1. 三个稳定能力壳：`read_finance_catalog`、`finance_query`、`load_finance_result`；
2. 当模型没有遵循阶段提示时，由 guard 阻止确定性错误调用。

默认 `preserveRequestPrefix=true`，三个工具 schema 在整轮保持稳定，目录内容仍由工具按 `subject + dataview + operation` 分层披露。阶段行为通过 DeepSeek Harness 的 `agent/pre-step` 精确注入提示，通过 `agent/request` 设置本次模型预算，通过 `agent/turn-stopping` 对“必须调用但模型漏掉”的动作最多补一次 steer。工具权限不是提示策略本身，只是 SOFT 失效后的最小 HARD 护栏。

这符合项目的 `SOFT → HARD → SOFT` 原则：

`用户问题 → 阶段提示理解 → subject/dataview/operation 与 result_ref → 下一阶段提示/最终表达`

## 提示资产唯一来源与注入时点

| 提示资产 | 唯一真源 | 注入时点 | 用途 |
|---|---|---|---|
| DSH 场景总约束 | `src/scenarios/financial_qa/dsh_system.md` | Harness 会话 system prompt | 规定金融查数总目标、返回方式和通用原则；该文件保留人工修改 |
| DSH 阶段策略 | `STAGE_PROMPTS` in `src/scenarios/financial_qa/dsh_loop_policy.mjs` | 每次 `agent/pre-step` | 只表达当前阶段允许做什么、下一步如何收束 |
| 目录字段、协议与例子 | 共用 catalog 服务的当前 operation 结果 | `read_finance_catalog` 成功后 | 只向已选中的 dataview/operation 披露执行知识，不复制到阶段提示 |
| 数据结果下一步指引 | 共用工具结果中的权威 `step_evidence.guidance` | 工具执行结果 | 根据真实 row count、sample completeness、result_ref 指向下一动作 |
| 部署业务经验 | `FINANCE_DSH_LOOP_POLICY_CONFIG.businessHint` | 阶段提示尾部 | 租户或业务线的短 SOFT 经验；不得复制 catalog 事实 |

禁止把同一份目录描述、字段、例子或结果事实再抄进 `dsh_system.md` 或阶段提示。阶段提示只管理动作语义，catalog/result payload 管理事实。这样既避免多源表达，也允许 CC 与 DSH 共用同一套数据协议。

每次 DSH 结果现在旁路记录：

- `prompt_assets`：全局 system 与阶段策略文件路径、SHA-256；
- `loop_policy.requests[*].prompt_injected/prompt_surface/prompt_sha256/prompt_chars`：本次请求实际识别到的阶段提示证据；
- `stage/stage_reason/stage_inferred_from_calls`：优先读取真实提示标记，只有旧 trace 缺标记时才按工具调用回退推断。

这些字段只用于 trace、评测和未来调参界面，不进入金融结果协议或正式答案。

## 当前已经具备的控制面

DSH 金融路径已有独立策略配置，不影响 CC：

- `preserveRequestPrefix`：`true` 为固定 system/tool schemas/request config，通过结果指引和 guard 控制阶段；`false` 为动态裁剪工具、提示和阶段预算。
- `maxCatalogAttempts`、`maxQueryAttempts`、`maxQueryRepairs`、`maxLoadAttempts`：非进展循环的硬预算。
- `duplicateCallLimit`：完全相同工具与参数的重复上限。
- `businessHint`：租户、场景或业务线的附加经验；不修改共用 catalog。
- `budgets.*.reasoningEffort/maxTokens`：固定前缀与动态工具模式都按阶段生效；固定前缀只保持 system/tool schema 主体稳定，阶段 message 与本次请求预算仍可变化。

默认策略仍把 catalog、字段、结果引用和真实数据保存交给共用 HARD 协议；DSH 插件只负责阶段判断、下一动作、预算和停止条件。它没有引入新的业务状态枚举，也没有把调参信息混进用户答案。

## `max_tokens` 结论与处理

### CC 与 DSH 的差别

当前 CC 金融路径创建 `ClaudeAgentOptions` 时显式设置的是 `max_turns` 和 `effort`，没有传入 `max_tokens`；输出上限由 CC SDK、模型路由或上游服务默认值决定。DSH Python SDK 则支持 `max_tokens`，含义是“每次模型请求的 completion 上限”，并且 DeepSeek 的 `completion_tokens` 包含 reasoning tokens，不只是最终可见文字。

因此，DSH 命中 3,072 并不代表模型真的写了 3,072 token 的答案。旧随机 20 题 `dsh opt` 中共有 78 次模型请求，4 次命中 3,072 上限，全部发生在 final 请求；四次 reasoning 占比分别为 96.0%、99.4%、95.0%、100%，真正可见输出只有 122、17、154、0 tokens。新策略验证中的一次 3,072 命中同样有 3,061 reasoning tokens，仅 11 个可见 tokens。根因是低推理模式在本应调用工具或直接回答的阶段持续思考，不是业务答案天然需要超长输出。

### 当前处理原则

- catalog/query/repair 保留 `reasoningEffort=low`，预算分别为 1,536/3,072/3,072；这些阶段需要语义路由和 DSL 构造，但正常工具调用远短于上限。
- details/final 使用 `reasoningEffort=off`，预算均为 2,048，把预算留给列选择和可见答案。验证样例的最终回答为 617/717 tokens，2,048 不是正常答案的瓶颈。
- 记录每次请求的 `visible_output_tokens`、`reasoning_share` 和 `max_token_hit`，区分“真实长回答”和“推理耗尽”。
- 不启用 DSH 的 `maxTokensAsSuccess` 来掩盖截断；若确实命中仍按 max-token 暴露。仅允许框架在客观缺少必需动作时补一次 steer，避免无限续写。
- 不因为单次推理跑满就直接扩大预算。先看阶段提示是否精确、是否遗漏必需动作；只有多题出现“可见业务输出确实被截断”才提高该阶段上限。

这是一种阶段输出预算，不是总上下文上限。总上下文仍通过逐请求 `context_tokens`、`cumulative_context_tokens`、`final_context_tokens` 独立观测。

## DSH Low 与 DSH Opt 的可复现实验定义

本项目中的技术抽象名统一为“阶段策略编排”：

- **DSH Low**：同一模型 `deepseek-v4-flash`、`reasoning=low`、根请求 `max_tokens=8192`，设置 `FINANCE_DSH_LOOP_POLICY_CONFIG={"enabled":false}`；它是无 DSH 专有阶段编排的基线。
- **DSH Opt（阶段策略编排）**：同一模型，启用 `dsh_loop_policy.mjs` 当前默认配置；catalog/query 低推理，details/final 关闭推理，并使用有界循环、重复调用 guard、阶段提示和一次必要 steer。

旧版 Opt 的 JSON 字段仍全部兼容；`enabled/preserveRequestPrefix/maxCatalogAttempts/maxQueryAttempts/maxQueryRepairs/maxLoadAttempts/duplicateCallLimit/businessHint/budgets` 均可继续解析。旧配置若显式把 details/final 覆盖为 `reasoningEffort=low`，也会忠实执行，但会重新引入“推理吃满 final 输出预算”的已知问题，因此只适合历史复现，不作为当前 Opt 默认值。

当前默认完整配置和 Low 基线开关均记录在 `.env.example`。每次评测还必须保存模型、配置 JSON、题集、随机种子、prompt asset hash 与 catalog revision，不能只用 `opt` 这个名字代表一个会漂移的实验条件。

## 建议的前端形态

第一版不要直接铺开所有底层参数，建议分为三个层次：

1. **策略模板**：`稳健省 token`（固定前缀，默认）、`极致低延迟`（动态工具）、`复杂研究`（更高查询/明细预算）。模板必须版本化，可灰度到租户或场景。
2. **业务经验卡**：按研报观点、标准预测、实际财务、行情估值等关键路由位置维护短自然语言经验；支持草稿、评测、发布、回滚。它是 SOFT 资产，不扩张 catalog HARD schema。
3. **专家参数**：展示 loop 上限、重复调用、reasoning、max tokens 和前缀模式，并实时估算最坏模型请求数。修改必须绑定离线题集或小流量实验。

每次运行建议显示一个紧凑阶段轨迹：

`目录 1/3 -> 查询 1/3 -> 明细 0/2 -> 回答`

点击后再展开每次 LLM 的上下文、缓存读取、输出、reasoning、可见工具、实际工具、拒绝原因和 stop reason。正式回答保持简洁，所有调试证据走旁路 observability。

## 产品指标

策略实验至少同时看四组指标，不能只优化平均耗时：

- 效果：首入口正确率、最终可接受入口、答案证据覆盖、零行诚实率；
- 效率：模型请求数、工具调用数、重复/拒绝调用数、端到端 P50/P95；
- token：未缓存 prompt、cache read、completion、reasoning、累计上下文；
- 稳定性：完成率、provider/validation 错误、max-token、原始工具标记泄漏。

所有对比应记录模型、provider、并发、题集版本、策略版本和 prompt 版本。CC 的整轮聚合 usage 与 DSH 逐请求 usage 不应伪装成同一指标。

## 2026-09-02 随机 20 题实测

### 实验口径

- 题源：`outputs/financial_qa_mainland_eval_20260902/cases_mainland_supported.json`。
- 先排除仍包含 `stock.news` 要求的 RTE006、RTE010、RTE060，再以固定种子 `20260902` 从 64 题抽取 20 题。
- 三组均使用 `deepseek-v4-flash`、并发 3、单题超时 600 秒；CC/DSH 均关闭 Web Search/MCP 外部搜索。
- DSH Low：loop policy 关闭、全局 low、单次 completion 上限 8,192。
- DSH Opt：当前“阶段策略编排”默认配置；下表采用验证重跑 20/20 的结果。首跑 19/20，RTE059 因错误选择不支持的 `compute` operation 后进入无进展目录循环，两次请求的 1,536 tokens 几乎全部被 reasoning 用完；单题验证重跑通过。首跑保留为稳定性证据。
- 原始结果、事件流、逐题 API 轨迹和比较分析均保存在 `outputs/financial_qa_prompt_policy_random20_20260902/`。

### 汇总结果

| 指标 | CC | DSH Low | DSH Opt |
|---|---:|---:|---:|
| 完成 | 20/20 | 20/20 | 20/20 |
| 首入口符合 golden | 14/20 | 14/20 | 18/20 |
| 最终包含可接受入口 | 18/20 | 18/20 | 20/20 |
| 覆盖全部 required entries | 17/20 | 16/20 | 19/20 |
| 平均端到端耗时 | 50.06s | 67.77s | 38.73s |
| P95 端到端耗时 | 92.82s | 110.52s | 62.85s |
| 上报 prompt tokens | 356,067 | 398,345 | 501,706 |
| 上报 completion tokens | 86,667 | 73,769 | 40,532 |
| 上报总 tokens | 442,734 | 472,114 | 542,238 |
| DSH 累计上下文 | 不可得 | 1,268,745 | 1,059,146 |
| DSH reasoning tokens | 不可得 | 49,998 | 17,894 |
| DSH 模型请求数 | 不可比 | 87 | 81 |
| 工具调用数 | 114 | 127 | 87 |
| max-token 命中 | 不可得 | 0 | 0 |
| 阶段提示注入证据 | 不适用 | 0/87（策略关闭） | 81/81 |

当前 Opt 相比 Low：平均耗时下降 42.9%，reasoning 下降 64.2%，模型请求下降 6.9%，工具调用下降 31.5%，累计上下文下降 16.5%；19/20 题更快。相比 CC：平均耗时下降 22.6%，14/20 题更快，工具调用下降 23.7%。

但当前 Opt 的上报总 tokens 比 Low 高 14.9%、比 CC 高 22.5%，因此“速度和循环效率显著改善”成立，“上报 token 显著降低”尚未成立。DSH 的 cache read 与累计上下文是独立观测口径，不能用来冲抵上报 token；下一阶段应优先处理 DSH 工具结果投影和请求前缀缓存，而不是继续压低 final 的 2,048 上限。

### API 执行一致性

| 两组比较 | 首个数据 API 完全一致 | API 集合完全一致 | API 顺序完全一致 |
|---|---:|---:|---:|
| CC vs DSH Low | 12/20 | 7/20 | 5/20 |
| CC vs DSH Opt | 11/20 | 9/20 | 7/20 |
| DSH Low vs DSH Opt | 11/20 | 6/20 | 5/20 |
| 三组同时一致 | 8/20 | 5/20 | — |

API 完全一致不是唯一正确性标准：同一题可能有多个可接受入口，或需要不同次数/顺序读取同一 dataview。因而本轮同时保留了逐题首 API、API 集合、API 顺序、golden/acceptable/required 命中四种证据，不能只用“三方是否一模一样”替代效果判断。

## 后续优先级

### P0：DSH 专有的结果投影

当前真实结果已经保存到 `result_ref`，模型看到的是摘要/样例；但研报长文本样例仍是未缓存 prompt 的主要来源。下一步应在 DSH `tools/post-execute` 增加可配置投影预算：保留口径、row count、schema、必要短样例和 `result_ref`，长文本或多行结果标记为不完整，并让模型只通过 `load_finance_result` 读取回答所需列和页。必须用研报观点类题验证证据覆盖，不能用机械截断换 token。

### P0：停止原因显式化

在旁路 trace 统计 `query_success`、`zero_rows`、`repair_limit`、`duplicate_denied`、`detail_limit` 等原因，并将“模型请求了被拒绝工具”单独计数。它属于观测事实，不进入业务状态机或答案 schema。

### P1：业务经验评测闭环

业务提示发布前自动跑固定路由题与语义审阅题，对比效果、时间和 token。前端呈现逐题改善/退化，允许按场景灰度，而不是把一条经验直接全局生效。

### P1：简单查询快速收束

对单一标准指标、单一行情快照等稳定形态，可探索 DSH 内部专用 finalizer 或结构化直接渲染，减少最后一次生成；观点、竞争格局和因果解释仍保留模型总结。该优化必须按场景分流，不能扩张共用工具协议。

本轮不引入“先检索候选工具”层。路由继续依赖结构化目录摘要、选中 dataview 后加载字段，以及业务提示经验，避免召回不确定性成为新的效果风险。
