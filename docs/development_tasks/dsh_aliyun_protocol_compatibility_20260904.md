# DSH × 阿里云 MaaS 协议兼容审计（2026-09-04）

## 结论

金融查数使用的 DSH `deepseek-official` 文本与工具调用路径，可以继续使用阿里云 MaaS 的 OpenAI 兼容入口。请求语义、thinking、推理回放、并行工具、结束原因和 token 计量均已实测兼容。唯一确认的 wire 差异是：阿里云在流式工具调用的后续参数分片中重复发送 `id: ""` 与 `function.name: ""`；DeepSeek 官方形状是在首个分片发送身份，后续省略字段。

修复位于 DSH `llm-deepseek` 提供方边界：每个 tool-call index 的首个非空 id/name 成为稳定身份，后续空占位不覆盖；不同的后续非空身份或终态身份缺失以 `MALFORMED_RESPONSE` 失败。该处理不识别阿里云 hostname，不改变 Fin Agent 工具 schema，也不进入 agent loop，因此切回 DeepSeek 官方无需回退，未来改用其他 adapter 时也不会执行这段逻辑。

这里的“兼容”指协议与执行链等价，不表示两个不同模型部署会逐 token 生成相同内容。本地历史评测使用 `deepseek-v4-flash`，当前阿里云使用精确模型 id `deepseek-v4-flash-0731`；阿里云 `/models` 同时列出这两个独立 id，调用结果也原样返回所请求的 id，而 `system_fingerprint` 不提供可核对的权重版本。因此不能证明 alias 与带日期部署完全同权重。当前固定带日期 id 是为了避免 alias 漂移；模型版本、服务端采样实现或系统更新造成的语义差异仍由评测集衡量，不在 adapter 中猜测或改写。

## 为什么换 endpoint 会产生数量级影响

Endpoint 不只是网络地址，它同时选择了提供方网关、模型部署和协议方言。本次有两种性质不同的影响：

1. **确定性的 wire 差异**：阿里云对 `deepseek-v4-flash` 的一次工具调用实测产生 106 个 tool delta，其中首片段之后 105 个片段都显式携带空 `id/name`；`deepseek-v4-flash-0731` 为 88 个片段，其中 87 个为空。DeepSeek 官方常见形状是在后续片段省略这些字段。旧 accumulator 将“空字符串”理解为“新值”，把已经获得的工具身份覆盖掉，导致有效调用变成 `unknown tool ""`。策略层随即看不到 catalog 已完成，错误被 loop 放大为 12 次无效模型调用。
2. **概率性的模型语义差异**：修复 wire 后，模型仍会对没有完整定义的业务字段作不同猜测。例如旧 catalog 把 `statement_type` 叫作“报表类型”，模型曾猜成中文 `年报`；但数据库中它实际表示合并/母公司、调整前后、累计/单季口径，年报频率应由 `report_period=YYYY-12-31` 表达。一个部署省略该条件会有数据，另一次生成 `statement_type=年报` 就会变成零行。Endpoint 暴露并放大了 catalog 歧义，但不是歧义的来源。

对应修复也保持分层：提供方 adapter 只归一化 wire，模型配置固定精确部署并显式指定 thinking/effort，金融 catalog 唯一真源补齐字段值域和年度报告期规则。没有在 loop 中加入公司名或单题关键词特判。

## 审计矩阵

| DSH 特有或关键契约 | 阿里云实测 | 处理结论 |
|---|---:|---|
| `POST /chat/completions`、SSE、`stream_options.include_usage`、`[DONE]` | 通过 | 保持 DSH 原协议 |
| Bearer、匿名 user id、session id、归因 header | 通过 | 保持 |
| `thinking.type=disabled` | 通过 | off 阶段无 reasoning 块 |
| `thinking.type=enabled` + `reasoning_effort=low/high/max` | 通过 | 保持 DSH 四档映射；`off` 不发送 effort |
| 省略 thinking 使用提供方默认 | 通过 | 阿里云当前默认返回 reasoning；显式阶段配置不依赖默认值 |
| `reasoning_content` 流式接收 | 通过 | 保持 reasoning/text 分块 |
| assistant 工具轮 `content:""` | 通过 | 保持，未改为 null |
| 工具轮 reasoning + tool_calls 历史回放 | 通过 | 工具结果后的下一次生成成功 |
| `role:tool` + `tool_call_id` | 通过 | 保持 |
| function tools JSON Schema | 通过 | 单工具与并行工具均成功 |
| 并行工具按 wire index 隔离 | 通过 | 两个 index 均正确组装 |
| 后续 tool delta 省略 id/name（官方形状） | 通过 | 行为不变 |
| 后续 tool delta 重复空 id/name（阿里云形状） | 修复后通过 | 通用归一化；不覆盖首个非空身份 |
| 冲突或终态缺失的工具身份 | 本地协议测试通过 | 完整 tool block 输出前失败，禁止进入工具执行 |
| 同一流中 reasoning、可见文本与 tool call | 通过 | 分块次序与 DSH assembler 兼容 |
| `finish_reason=stop/tool_calls/length` | 通过 | 映射为 stop/tool-calls/max-tokens |
| prompt、completion、cached、reasoning token usage | 通过 | 按 DSH 不重叠 token 桶映射 |
| `temperature`、`stop`、`max_tokens` | 通过 | stop 截断与 length 结束均符合预期 |
| 默认启用的 `dsh_plugin_packages` 顶层扩展 | 通过 | 阿里云接受；字段不进入模型提示词 |
| `dsh_session_log` 扩展 | 当前组合未启用 | 没有线上兼容风险；启用前另做一次 live probe |
| 非 2xx OpenAI error envelope | 已观察 | `{error:{message,type,code,id}}` 可被现有错误解析器消费；状态码仍为权威 |
| 图片、DeepSeek Files API、陈旧 file-id 恢复 | 不在金融查数路径 | 未宣称兼容；若未来金融工具加入图像输入，单独验证 `/files`，必要时选择支持该协议的 adapter |

## 证据

- DSH 聚焦翻译测试：39/39 通过；`llm-deepseek` 包测试：383/383 通过。
- DSH package TypeScript build、聚焦 lint、双语 pairing 与 diff check 均通过。
- 既有 DeepSeek 官方评测：184 个 case、682 次金融工具调用，空工具名 0。证据来自 `outputs/financial_qa_dsh_opt_full_20260903/dsh_opt_full_results.jsonl` 与 `outputs/financial_qa_mainland_full_increment_20260903/dsh_opt_increment_results.jsonl`。
- 阿里云修复前复现：一次金融请求 14 次模型调用，其中 12 次工具名为空，实际数据 API 未执行。
- 阿里云修复后隔离 MCP：
  - 海光信息 2022—2025 营收及同比：13 秒，`stock.financial_3_table`，1 个数据集、16 行，空/unknown tool 0。
  - 中际旭创近三个月研报倾向 + 净利润走势：20 秒，`stock.report` + `stock.financial_3_table`，2 个数据集、共 9 行，空/unknown tool 0；覆盖同阶段并行目录调用。
- 生产进程重载后公开 MCP：`execution_mode` 已出现在工具 schema；同一海光问题 6 秒、2 次模型调用、工具身份正常。该次模型自行加入了数据源不接受的 `statement_type = 年报`，因此返回 0 行。这是 catalog 业务枚举表达与模型语义选择问题，不是 wire 兼容问题，也说明不同部署不能用 adapter 保证逐次业务输出相同。
- 远端数据库核验：三张财务表的 `statement_type` 实际值域由 `HB/HBTZ/HBDJ/HBTZDJ/MGS/MGSTZ/MGSDJ/MGSTZDJ` 构成；海光信息数据也验证 `HB` 是普通合并累计口径，年度记录由报告期的 12 月 31 日筛选。catalog 已在唯一文件 `api_view_catalog.json` 补齐含义、默认行为和年度查询例子，CC 与 DSH 读取同一投影。
- catalog 修复后生产 MCP 首次回归：4.9 秒、2 次工具调用、`stock.financial_3_table`、4 行，报告期精确为 2022/2023/2024/2025 年末。
- 5 路并发快速模式：4 路按上述 4 行成功；1 路模型漏传 `operation`，快速模式按“不检查、不重试”的既定语义结束。这是可观测的残余模型服从性差异，不再是空工具身份问题。
- 5 路并发标准模式：5 路均取得上述 4 个年度报告期；4 路为 2 次工具调用，1 路额外产生一次冗余数据调用。说明标准模式可用 bounded loop 吸收偶发遗漏，而快速模式用稳定性换低延迟。

## 同步时发现的部署偏差

服务器当时并非只差模型 adapter：Fin Agent 的快速模式参数链和字符串型 API thread id 展示兼容未同步，导致 `execution_mode=fast` 被旧 MCP schema 忽略，以及成功取数后 presentation 尝试把 `finance-api-*` 转成整数。相关已通过本地 28 项 Python 测试和 17 项 loop-policy 测试，并同步到服务器；新进程的隔离 MCP 已验证成功。

生产 `fin-agent-finance-api` systemd unit 当前仍为 inactive，22054 由手工 Python 进程提供。进程已在 catalog 同步后重启并通过 `/health` 验证，健康响应的 catalog revision 为 `b394b1b484beaa2b3839c7520c6a5539a7c27de26901bcf0633e4d6f9b0b3140`。后续仍应择机把进程所有权切回 systemd，避免双重启动来源。

## 边界

适配器只保证稳定的 wire 语义，不能消除模型本身的规划差异。例如模型若错误地把 `revenue_yoy` 视为另一个 catalog operation，这是业务语义选择，应通过 catalog 描述、阶段提示和评测处理；adapter 不猜 operation，也不替换工具参数。这样保持 DSH 的模型自主性，同时把提供方协议差异严格隔离在边界层。

本次公开 MCP 暴露的独立 catalog 问题已经在唯一真源中修复：`statement_type` 的字段说明、八个实际值、默认 `HB` 和年度 `report_period` 用法会随选中的 query execution pack 一次完整加载。该改动对 CC 与 DSH 同时生效，adapter 和 loop 均未加入字符串特判。
