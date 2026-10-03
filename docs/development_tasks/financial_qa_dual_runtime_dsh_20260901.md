# 金融数据问答 CC / DSH 双运行时

## 范围与选择协议

本次只为 `investment_analyst + normal_qa + agent_route` 的金融数据问答增加 DSH 路径。工具开发、Skill、回测及其他聊天流程保持原运行时。

Chat API 接受一个稳定的显式参数：

```json
{
  "text": "贵州茅台最近一个交易日的收盘价是多少？",
  "financial_qa_runtime": "dsh"
}
```

- 支持值：`cc`、`dsh`。
- 缺省值：`cc`，保证旧客户端行为不变。
- 未知值：HTTP 400，不做猜测或静默降级。
- 显式选择 DSH 但 DSH 未启用或不可启动时：返回该路径的明确错误，不自动切回 CC，避免实验数据和线上行为失真。

同步 `/api/chat/dispatch` 与流式 `/api/chat/stream/start` 使用同一参数。

## DSH 路径

DSH 通过官方 Python SDK 驱动 `sdk-minimal` profile，并加载项目内的 patch。patch 关闭 Bash、PowerShell 和文件编辑器，只挂载一个 stdio MCP server。MCP server 复用 Fin Agent 当前金融工具实现，但仅暴露：

- `read_finance_catalog`
- `finance_query`
- `load_finance_result`

因此目录协议、静态校验、provider 执行、session variable store 和 `result_ref` 均与 CC 路径共用；DSH 不复制金融数据实现，也不能进入工具编写、Skill 或回测路径。

每个 DSH worker 持有一个可复用 Harness/MCP 进程。新会话租用当前空闲 worker，避免哈希碰撞造成“有空槽却排队”；后续轮次保持 session-to-worker 亲和，因为 DSH session 状态属于原 Harness 进程。每个 worker 同时只执行一轮，每轮只通过该 worker 独占的小型 HARD context 文件同步 owner、runtime scope 和 revision。完整工具轨迹写入旁路 trace，模型只接收工具返回的摘要和 `result_ref`。

## 配置

```dotenv
FINANCE_DSH_FINANCIAL_QA_ENABLED=1
FINANCE_DSH_PROVIDER=deepseek-official
FINANCE_DSH_MODEL=deepseek-v4-flash
FINANCE_DSH_REASONING_EFFORT=low
FINANCE_DSH_WORKERS=10
FINANCE_DSH_MAX_TOKENS=8192
FINANCE_DSH_TURN_TIMEOUT_SECONDS=300
FINANCE_DSH_QUEUE_TIMEOUT_SECONDS=300
FINANCE_API_MAX_CONCURRENCY=10
FINANCE_DSH_PREWARM_ON_START=1
FINANCE_DSH_PREWARM_WORKERS=10
```

`FINANCE_API_MAX_CONCURRENCY` 是实例级入口闸门，`FINANCE_DSH_WORKERS` 是真正可同时运行的 Harness 数。面向 8C/16GB 且主要等待远端模型与数据库的单实例，两者可先同设为 10；若实测单 worker 常驻内存过高，则同时下调到 6–8，而不是只放大入口队列。应用保持一个 Uvicorn worker，避免每个 Web 进程复制整套 DSH worker pool；需要更大吞吐时再按实例横向扩容。

生产环境优先安装 `dsh` executable 与 DeepSeek Harness Python SDK。相邻源码仓开发可配置：

```dotenv
FINANCE_DSH_SOURCE_ROOT=/absolute/path/to/deepseek-harness
FINANCE_DSH_SDK_SOURCE=/absolute/path/to/deepseek-harness/python/sdk/src
FINANCE_DSH_NODE_BIN=/absolute/path/to/node
```

## Top20 配对回归（2026-09-01）

测试集为 `cases_seed20260831_n100.json` 的前 20 题。两条路径均通过真实 Chat/SSE API，顺序执行，并发 3，单题超时 420 秒；二者模型均为 `deepseek-v4-flash`。

| 指标 | CC | DSH | DSH 相对 CC |
|---|---:|---:|---:|
| 请求完成 | 20/20 | 20/20 | 相同 |
| 端到端均值 | 36.75s | 62.09s | +68.97% |
| 端到端中位数 | 32.74s | 67.82s | +107.17% |
| 端到端 P95（20题最近秩） | 68.17s | 85.83s | +25.91% |
| 未缓存 prompt tokens 合计 | 421,142 | 409,556 | -2.75% |
| completion tokens 合计 | 53,206 | 85,701 | +61.07% |
| 未缓存 total tokens 合计 | 474,348 | 495,257 | +4.41% |
| DSH 单列 cache-read tokens | 未上报 | 1,256,576 | 不可直接作同口径差值 |
| 工具调用合计 | 109 | 150 | +37.61% |
| 首个目录入口正确率 | 45% | 50% | +5pp |
| 最终找到可接受入口比例 | 50% | 75% | +25pp |

结论：当前 DSH 路径功能与恢复能力成立，但还不是性能替代方案。它用更多目录读取、数据查询和输出 token 换取了更高的错误路由恢复率；20 题中只有 4 题比 CC 快，16 题更慢。下一轮优化应优先减少 DSH 的重复目录读取、无效修复轮次和长回答，而不是增加候选检索层。

## Reasoning High → Low 单变量复测（2026-09-02）

进一步审计确认：Fin Agent 未向 SDK 传递 `reasoning_effort` 时，DeepSeek adapter 会使用 `high`，而不是轻量自动档。本轮只把它显式调整为 `low`，模型、prompt、工具、目录协议、并发与测试题保持不变；同时新增逐次 LLM 请求上下文观测。

| 指标 | High | Low | Low 相对 High |
|---|---:|---:|---:|
| 请求完成 | 20/20 | 20/20 | 相同 |
| 端到端均值 | 62.09s | 41.93s | -32.47% |
| 端到端中位数 | 67.82s | 38.48s | -43.26% |
| Runtime 均值 | 55.36s | 34.24s | -38.15% |
| LLM 请求数 | 100 | 84 | -16.00% |
| Reasoning tokens | 62,270 | 24,628 | -60.45% |
| Completion tokens | 85,701 | 42,379 | -50.55% |
| 未缓存 total tokens | 495,257 | 392,369 | -20.77% |
| 累计上下文 tokens | 1,666,132 | 1,198,246 | -28.08% |
| 每次 LLM 请求累计上下文加权均值 | 16,661 | 14,265 | -14.38% |
| 工具调用 | 150 | 108 | -28.00% |
| 首个目录入口正确率 | 50% | 60% | +10pp |
| 最终找到可接受入口比例 | 75% | 75% | 相同 |

Low 的 84 次模型请求中，单次上下文中位数为 10,207 tokens、P95 为 42,204、最大为 50,104；每题最后一次请求的上下文均值为 21,450。20 题中 17 题变快、3 题变慢。`RTE006` 在 Low 下从 6 次模型请求增加到 10 次，说明降低推理强度不能替代工具路径停止规则，但总体上 Low 在速度、token 与路由质量之间明显优于 High，因此成为 DSH 金融问答的新默认值。

新增结果文件位于 `outputs/financial_qa_dsh_reasoning_low_top20_20260902/`，包括 Low 原始结果、High/Low 配对比较、CC/Low 比较和逐 LLM 请求上下文分析。

结果文件位于 `outputs/financial_qa_cc_dsh_top20_20260901/`：

- `cc_results.json` / `dsh_results.json`：与既有 100 题结果相同的完整结构。
- `cc_analysis.json` / `dsh_analysis.json`：目录路由、API 时间和失败审计。
- `comparison_cc_vs_dsh.json`：逐题配对差值。
- `*.events.jsonl`：SSE 观察事件。

## DSH 有界循环策略（2026-09-02）

本轮没有修改 CC 共用的 tool schema、catalog 协议或 provider 实现，而是在 DSH Agent scope 中插入独立插件 `dsh_loop_policy.mjs`。插件把开放式自主循环收敛为金融查数所需的少量状态：

`catalog -> query -> details? -> final`

其中 identity-only 查询允许继续一次实际业务查询；query 失败只允许一次有证据的修复；完全相同调用会被拒绝；目录、查询、明细均有硬上限。零行是有效业务结果，不能换 API 或目录追值。

> 本节记录的是 2026-09-01 的历史 Opt 基线（目录 3 次、所有阶段 `reasoningEffort=low`），用于复现实验，不再代表当前默认值。当前阶段提示注入、循环预算和 `max_tokens` 的唯一说明见 `financial_qa_dsh_policy_productization_20260902.md`；可执行默认值以 `dsh_loop_policy.mjs` 为准，`.env.example` 只提供同版本配置示例。

历史基线的 `preserveRequestPrefix=true` 保持 system、tool schemas 和调用配置稳定，阶段约束主要由执行守卫与工具结果指引落实；若显式设为 `false`，插件会按阶段动态裁剪可见工具并切换提示与预算。当前实现已支持在稳定前缀模式下通过 Harness 的 message 注入当前阶段提示，并按请求设置阶段预算，不再沿用“所有阶段 low”的默认策略。

业务经验有两个注入点：稳定的通用路由经验放在 `dsh_system.md`；部署或租户特有经验使用 `FINANCE_DSH_LOOP_POLICY_CONFIG.businessHint`。工具结果后的动态提示只表达下一动作，不重复业务事实。MCP canonical result 的 `{content:[...]}` 外层必须先解包再判断 `ok/row_count/sample_complete`；未解包会把成功零行误判成失败，引导模型无效修复。本轮已用协议测试和真实零行用例覆盖该适配边界。

循环策略及逐次 LLM 证据通过 `financial_qa.loop_policy`、`financial_qa.llm_step_usages` 和旁路 JSONL 暴露，不进入正式回答文本。每次请求可观察阶段、可见/实际工具、上下文、输出、reasoning、reasoning effort 和 max tokens，便于后续前端调参面板直接消费。

### 最终 Top20 配对回归

测试题、模型、真实 Chat/SSE API、并发 3 与 420 秒超时均与前述基线一致。CC 的模型 usage 是整轮聚合记录，不能与 DSH 的逐请求 `model_call_count` 或累计上下文直接比较；表中只列可比口径。

| 指标 | CC | DSH Low 原始 | DSH 最终策略 |
|---|---:|---:|---:|
| 请求完成 | 20/20 | 20/20 | 20/20 |
| 端到端均值 | 36.75s | 41.93s | **23.12s** |
| 端到端中位数 | 32.74s | 38.48s | **20.85s** |
| 端到端 P95 | 68.17s | 77.07s | **46.37s** |
| DSH 模型请求数 | 不可比 | 84 | **69** |
| 工具调用 | 109 | 108 | **63** |
| `finance_query` 调用 | 52 | 57 | **25** |
| 未缓存 prompt tokens | 421,142 | 349,990 | **330,392** |
| completion tokens | 53,206 | 42,379 | **30,862** |
| 未缓存 total tokens | 474,348 | 392,369 | **361,254** |
| DSH 累计上下文 tokens | 不可比 | 1,198,246 | **871,192** |
| reasoning tokens | 未上报 | 24,628 | **17,503** |
| 首个目录入口正确率 | 45% | 60% | **80%** |
| 最终找到可接受入口比例 | 50% | 75% | **80%** |

最终策略相对 DSH Low：平均端到端 -44.85%，runtime -48.52%，模型请求 -17.86%，工具调用 -41.67%，金融查询 -56.14%，累计上下文 -27.29%，reasoning -28.93%。未缓存 total token 仅下降 7.93%，说明下一阶段 token 优化重点已经不是继续砍 loop，而是 DSH 专有的长文本工具结果投影。

相对 CC：平均端到端 -37.07%，未缓存 total token -23.84%，工具调用 -42.20%，金融查询 -51.92%，首入口正确率 +35pp。机械路由标签不是完整答案正确率：例如 PE 对比直接使用 `stock.pricevalue`、标准预测数值使用 `stock.report_metric` 在业务上合理，但该批次部分 gold 仍只接受 `stock.report`，因此 80% 不应被解释为答案正确率上限。

第一轮“全动态裁剪”与最终策略的路由正确率和模型请求数都为 80% / 69 次；最终策略仍快 18.54%，未缓存 total token 少 43.84%。这证明对当前 provider 来说，稳定前缀加正确的结果阶段指引，比每步重写 system/tools 更适合作为默认策略。

最终结果位于 `outputs/financial_qa_dsh_loop_policy_stable_top20_20260902/`：

- `dsh_final_results.json`：完整 Top20 结果与 SSE 结构；
- `dsh_final_context_analysis.json`：逐请求上下文与 reasoning 分布；
- `dsh_final_routing_analysis.json`：目录/API 路由核验；
- `comparison_dsh_low_vs_final.json`、`comparison_dynamic_vs_final.json`、`comparison_cc_vs_final.json`：配对比较。
