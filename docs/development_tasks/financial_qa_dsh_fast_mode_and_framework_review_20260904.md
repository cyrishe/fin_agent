# 金融查询 DSH 快速模式与框架优化复核（2026-09-04）

## 1. 范围与结论

本次只调整金融数据查询路径，不改变 CC 路径和其他 Agent 场景。实现遵循：

- HARD：每个请求显式携带 `execution_mode=standard|fast`，默认 `standard`，保持兼容。
- SOFT：DSH 按当前阶段注入简短业务指令，模型负责自然语言到目录/API DSL 的判断。
- DSH 策略：用 Harness 原生生命周期和工具管线限制阶段、预算和终止条件，不修改 DSH 源码。

核心结论：DSH 当前组合已经很轻，后续最大的收益不在继续删通用模块，而在于压缩模型可见的金融工具结果、让独立查询真正并行，以及按阶段选择模型与采样参数。

## 2. 快速模式

### 2.1 对外契约

| 入口 | 参数 |
|---|---|
| Finance REST / MCP | `runtime=dsh, execution_mode=fast` |
| Chat / SSE | `financial_qa_runtime=dsh, financial_qa_execution_mode=fast` |
| 批量评测脚本 | `--financial-qa-runtime=dsh --financial-qa-execution-mode=fast` |

`research_mode` 仍只表示回答深度；`execution_mode` 表示执行策略，两者不再混用。

### 2.2 阶段语义

```text
用户问题
  ↓
API 定位：一次模型阶段，可选择一个或多个 dataview
  ↓
调用生成与执行：一次模型阶段，可生成一个 flow 或多个独立调用
  ├─ response_mode=data ─→ 工具结果直接终止 turn，返回 row-dict
  └─ summary/both ───────→ 一次最终回答生成后返回
```

快速模式明确关闭：目录试探、查询修复、相同参数重试、结果检查、明细翻页和 `turn-stopping` 补救提示。静态 DSL 校验、数据源错误和单次调用超时仍保留；它们是执行边界，不是 Agent 检查或重试。

“一个阶段”不等于“只能调用一个工具”。同一阶段可表达多个独立目标，但当前 MCP bridge 将没有并行安全声明的工具视为互斥执行，详见 3.3。

### 2.3 实现位置

| 文件 | 职责 |
|---|---|
| `src/scenarios/financial_qa/execution_mode.py` | 请求模式规范化 |
| `src/finance_api/models.py` | REST/MCP HARD 契约 |
| `src/scenarios/financial_qa/service.py` | 每请求模式进入运行时上下文 |
| `src/scenarios/financial_qa/dsh_service.py` | 模式写入每个 worker 的 turn context |
| `src/scenarios/financial_qa/dsh_loop_policy.mjs` | 三阶段策略、无修复分支、data-only 原生终止 |
| `src/web/flask_app.py` | Chat/SSE 参数链路 |
| `scripts/eval_finance_query_api_batch.py` | 评测参数链路 |

### 2.4 真实烟测

问题：`宁德时代与广汽集团的PE估值对比如何？`

| 返回模式 | 结果 | 模型调用 | DSH 累计上下文 | Provider total tokens | Cache read | DSH 时长 |
|---|---:|---:|---:|---:|---:|---:|
| `data` | 1 个数据集、2 行 | 2 | 9,636 | 2,692 | 7,296 | 10.70s |
| `both` | 同样原始数据 + 中文回答 | 3 | 17,712 | 7,891 | 10,496 | 14.76s |

`data` 路径实测只经过 catalog 和 query 两次模型请求，并由 DSH 原生 terminal tool result 结束。`both` 必须保留第三次回答生成。对本身已经是三调用的简单题，快速模式不会天然大幅减少 summary token；它的主要收益在 data-only、原标准路径会进入 repair/details 的问题上。

烟测同时发现并修复了公共 Finance API 的既有兼容问题：外部隔离请求使用字符串 session id，而 UI 展示层曾强转整数。现在只有数值型 UI thread 才携带分页 `thread_id`。

## 3. DSH 原生可控面复核

复核基于本地 `../deepseek-harness` commit `cd5ef8148158c3a752a658978873241fdf8e2bbc`。当前使用独立 `sdk-minimal` profile，并在 patch 中关闭 shell/editor，只接入三个金融 MCP 工具和金融 loop policy。该 profile 本身不含 Web、settings、telemetry、workspace instructions、skills、jobs、subagents、compaction。因此当前并不是“完整重框架误用于查数”。

### 3.1 已使用且合理保留

| DSH 原生能力 | 当前使用 | 结论 |
|---|---|---|
| `agent/pre-step` | 按阶段注入业务提示 | 正确；提示精确进入当前阶段，不污染 catalog 真源 |
| `agent/request` | 分阶段设置 reasoning effort / max tokens | 正确；历史全量 245 个请求没有一次触顶 |
| `tools.guard` | 阶段限制、重复调用上限、catalog 三元组约束 | 正确；这是稳定边界，不承担业务判断 |
| `tools/execute` | data-only 成功结果设置 `concludesTurn` | 正确；避免空的第三次模型调用 |
| SDK patch / 外部插件 | 独立金融策略，不 fork DSH | 正确；升级面小，可回归 |
| MCP timeout / reconnect | 单调用 180s、有限重连 | 保留；这是基础设施失败边界 |

### 3.2 高价值的下一步

#### P0：模型可见结果投影

DSH 原生 `tools/post-execute` 能只替换模型可见 content，同时保留 canonical value。应针对：

- `read_finance_catalog`：只给模型当前 operation 的协议、字段和精确例子。
- `finance_query`：保留 schema、row_count、关键 sample、`result_ref`，不内联完整长文本。
- `load_finance_result`：强制列投影、页大小和字节预算；长文本只给任务所需片段。

这是当前最确定的 token 优化点。系统仍保存完整 row-dict，模型只收到回答所需证据，符合 `result_ref` 的原始设计。

#### P1：真正的同阶段并行

DSH `agent-loop` 原生支持 `maxParallelToolCalls`，但只有工具定义返回 `isConcurrencySafe() === true` 才会并行；省略声明时默认 exclusive。当前 DSH MCP client 生成的工具定义没有该声明，因此模型同一步发出多个 catalog/query 调用时仍会串行。

可选实现按优先级：

1. 在金融 MCP 工具内部提供一个批量调用，由金融服务控制并发和结果顺序。
2. 做一个 DSH 原生金融 adapter plugin，由它拥有工具定义并将只读调用声明为并行安全。
3. 不建议直接修改 DSH MCP bridge；这会扩大升级维护面。

并发还需受数据库连接池和数据供应商限流约束，不能只把 `maxParallelToolCalls` 调大。

#### P1：阶段模型路由与确定性采样

`agent/request` 原生允许逐步覆盖 provider、model、reasoning effort、temperature、max tokens。当前只使用后两项。可以 A/B 测试：

- catalog/query：快模型、低推理、`temperature=0`。
- final：只有复杂解释才切强模型；data-only 不进入 final。
- 若模型切换导致 KV cache 失效，必须用总时长和真实 billed token 判断净收益。

这比继续缩短几十字阶段提示更可能产生可见收益。

#### P1：末段工具可见性 A/B

DSH 原生 `tools.restrict()` 可按 agent scope 隐藏工具。当前为了 DeepSeek KV cache 保持三个小 schema 的稳定前缀，只用 guard 限制阶段；代价是 details 阶段仍可能“看见”并错误选择 `finance_query`。

建议只 A/B 两种策略：

- 稳定 schema + guard（当前）。
- catalog/query 保持稳定，进入 details/final 后隐藏无关工具。

第二种可能少一个无效回合，但会改变工具 schema 前缀、降低 cache 命中，不能凭直觉直接切换。

### 3.3 条件性能力

| 能力 | 适用条件 | 当前判断 |
|---|---|---|
| token meter | 平台展示近似上下文占用 | 可用于 UI，但其 4 chars/token 对 CJK/JSON 明显低估；计费继续用 provider usage |
| compaction + tool-result-pruner | 真正的多轮长会话 | 只对有 `conversation_id` 的长会话启用；孤立三阶段请求启用会多一次总结调用，得不偿失 |
| generic spill | 非金融工具产生不可控大文本 | 金融已有结构化 `result_ref`，优先修自己的精确投影；spill 只作兜底 |
| LLM retry plugin | 仅 transport/rate-limit 失败且业务允许 | `sdk-minimal` 当前未装；快速模式明确不应增加。标准模式也只能有限、按错误类型使用 |

### 3.4 不建议用于当前查数主线

- PTC / `run_code`：适合复杂程序化工具组合，但会引入代码生成和 SDK 提示；对三个固定金融工具不保证省 token。
- workflow/subagent：独立子 Agent 有自己的提示和上下文，查数任务会增加模型调用。
- 替换整个 `agent-loop`：DSH 技术上允许注册新的 Agent driver，但当前原生事件和工具管线已足够，重写比例失衡。
- 为每个 dataview 写代码分支：会把 SOFT 业务判断硬编码进框架，违背当前架构原则。

## 4. 现有全量结果的上下文抽样

数据源：`outputs/financial_qa_dsh_opt_full_20260903/dsh_opt_full_results.json`，67 题。

### 4.1 总体

| 指标 | 结果 |
|---|---:|
| 模型调用 | 均值 3.77；中位数 4；最大 6 |
| 累计上下文 | 均值 46,739；中位数 37,583；P95 86,482；最大 110,707 |
| 最终单次上下文 | 均值 22,055；最大 75,990 |
| catalog / query step / load 总次数 | 100 / 94 / 38 |
| 多 catalog 题目 | 30 / 67 |
| catalog 已加载但后续未使用的题目 | 11 / 67 |
| 被 guard 拒绝后又多一个模型请求 | 6 / 67 |
| details 阶段错误尝试 `finance_query` | 4 / 67 |
| `max_token_hit` | 0 / 245 个模型请求 |

40 个“load 后进入下一模型请求”的上下文增量中，中位数为 3,845 tokens，但有 10 次增加至少 20,000，最大一次增加 55,502。说明阶段提示本身已经很短，主要长尾来自 catalog/result content，而不是 100～300 字的阶段指令。

### 4.2 五个代表性样本

| Case | 路径 | 上下文判断 | 改进空间 |
|---|---|---|---|
| RTE047 宁德时代/广汽 PE | catalog → query → final；3 调用；3,722→6,153→7,382 | 紧凑、入口和字段准确 | 基本无；适合作为快路径基线 |
| RTE014 天风/北方华创 2027 收入预测 | catalog → 一个含 2 step 的 query → final；最终 10,299 | catalog 准确，但第二个 2025–2026 对照并非用户所求，且也为 0 行 | 去掉“至多一个解释性比较目标”的泛化诱因；快速模式已只取明确目标 |
| RTE009 吉比特净利率 | catalog → 失败 query → repair → load → 错误 query → final；6 调用；最终 14,523 | 内容不算巨大，但 loop 不精确 | 首次用了该 API 不支持的 `like`；repair 有价值，details 后又误调 query 浪费一轮 |
| RTE042 中国核电海外收入 | 首轮读 3 个 catalog，仅执行 business_segment；随后 load，并在 details 误调 query；5 调用；最终 24,132 | 目录明显过选 | 3 个 catalog 中 2 个未消费；适合验证“末段隐藏工具”和 catalog 选择精度 |
| RTE005 今世缘估值方法 | 2 catalog → 2 数据集 query（25+60 行）→ 2 次 load → final；4 调用 | 严重长尾：20,488→75,990，单步增加 55,502 | `investment_highlights` 长文本被大量内联；P0 必须做 load 结果投影和列/字节预算 |

## 5. 实施顺序

1. 已完成：快速模式、API/Chat/评测参数链路、data-only 原生终止、协议与策略测试。
2. 已完成（2026-09-04）：实现 DSH `tools/post-execute` 金融结果投影；完整结果仍由 `result_ref` 保存，只有下一次模型请求使用有界样例。实现与公开溯源协议见 `financial_qa_public_trace_and_dsh_opt_20260904.md`。后续继续用 RTE005/RTE070 等长尾题验证准确率与 token。
3. 再下一步：实现或验证金融批量只读调用的真实并发，而不是只让模型同轮输出多个 tool calls。
4. A/B：阶段模型/temperature 和末段 tool visibility；以 API 一致性、总时长、provider billed token、cache read 四项共同决策。
5. 多轮会话产品化时，再加入 token meter 与条件 compaction；不污染孤立查数快路径。
