# DSH 金融查询优化：本 session 交接

更新日期：2026-09-09。本文完整承接本 session 的任务、实施结果、验证边界和下一步，不要求新 session 重读聊天。这里只记录和交接，不增加业务实现或启动新评测。

## 1. 阅读顺序与职责

1. 根目录 [AGENTS.md](../../AGENTS.md)：SOFT → HARD → SOFT、最小改动、生产操作与证据门禁。
2. [当前主框架交接](main_framework_session_handoff_20260909.md)：跨 session 合并后的主框架现状、部署记录与最新问题。
3. 本文：本 session 的 DSH 效率优化范围及延续任务。
4. [第一批实现与验证](financial_qa_dsh_lossless_termination_20260908.md)：具体机制、历史实验和复现方式。

本文不复制维护另一套架构定义。实现细节以当前代码为准；历史报告描述当时的版本，不代表当前完整行为。

## 2. 核心任务与用户约束

目标：在金融查数场景下，减少 DSH 的模型调用、冗余上下文、token 消耗及耗时，效果和稳定性不能下降。CC 保留比较基准，不能为了优化 DSH 改坏现有接口、权限或对话行为。

- 优化可证明的多余操作，不能靠缩减必要证据、推理预算或修复机会制造提速。
- 优先采用 DeepSeek Harness 原生 hook 和扩展点；本项目负责金融协议与策略，不能把这些业务逻辑称为 DSH 内置能力。
- 不采用候选检索替代目录路由，不针对单道评测题增加关键词、条件分支、提示词补丁或语义 validator。
- Catalog 唯一真源，按阶段精准加载；不另造丢失信息的摘要副本。
- 原始 row-dict 结果通过 `result_ref` 保存；summary 独立返回。不能把大量原始数据直接放入模型上下文。
- MCP 必须认证；默认请求独立，显式传入 `conversation_id` 时保留历史续聊能力。MCP 金融查数不能绕到通用 web search。
- 评估不止看入口和 HTTP 成功：还要看实际 API、筛选条件、时间窗口、单位、数据覆盖、必要修复及最终解释证据。

本地仓库：`/Volumes/ext/fin_agent`。DSH 源码：`/Volumes/ext/deepseek-harness`。

## 3. 本 session 已实施

第一批只处理确定性冗余，未升级或修改 DSH 框架本体：

1. **确定完成的全零结果省去无效最终生成。** 原先模型生成的最后一段会被系统固定空结果说明覆盖；现在共享原有 Python 渲染函数，通过内部 trace 和原生 `agent/pre-step` 在满足完成条件后直接返回同一说明。
2. **没有进度订阅者时跳过展示处理。** 不再额外构造、解析进度，但保留 SDK 原始事件、工具记录和 usage。
3. **内部 JSON 无损紧凑编码。** trace 和运行索引信息不删减，不改为可能丢失末尾记录的异步持久化。

空结果优化不是“查到零行就结束”：必须有完整成功结果及引用、显式完成标记、当前 trace 版本一致、没有必需动作或待处理输入。缺少条件、部分失败、非零、data-only 等不走此分支。**当前代码还排除了 Skill 引导的分析回答**，不能恢复为早期缺少此边界的实现。

固定回复以真实 plugin message 记录，不伪造模型 assistant 输出或生成 token。宿主只有发现本轮结果对应的精确 handoff 证据，才将 SDK 的 `blocked` 识别为成功提前返回；其他失败、取消或 blocked 仍按原行为处理。

现有 `FINANCE_DSH_LOOP_POLICY_CONFIG` 的可选项 `emptyResultEarlyStop` 默认 true，可设 false 单独关闭；保留 JSON 中其他设置。`empty_result_early_stop` 是观测字段，不是新业务状态。

主要文件：

- `src/scenarios/financial_qa/empty_result.py`、`service.py`：共享固定空结果说明。
- `src/scenarios/financial_qa/dsh_mcp_server.py`、`dsh_loop_policy.mjs`、`dsh_service.py`：内部证据、原生 hook、宿主判定与观测。
- `tests/dsh_finance_loop_policy.test.mjs`、`tests/test_financial_qa_dsh_runtime.py`：边界回归。
- `scripts/verify_finance_dsh_empty_result_replay.py`：确定性原生链路回放。
- `scripts/eval_financial_qa_dsh_context_ab.py`：增加 `--cases-file`、`--[no-]empty-result-early-stop`，保持原题做开关对照。

## 4. 历史验证结论与限制

以下是 9 月 8 日实施批次证据，**不是当前 HEAD 的全量回归或生产质量门**。

- Python 专项：146 passed、2 skipped；Node：32 passed；diff 检查及 Python 编译通过。
- 确定性回放：真实 MCP 认证、DSH SDK/插件、内部进程、目录、远端 DB 只读查询、结果存储；模型由本地 SSE fixture 替代，不是实际模型准确率测试。
- 完成全零请求的模型 HTTP 调用 3 → 2；显式续聊 2 → 1；先零行但未完成、再查询的案例 4 → 3，首次零行不提前停；非零 3 → 3；data-only 2 → 2。
- 回放返回的行数、样例与 summary 一致；首个空结果案例前两轮模型请求体逐字节一致。缺少认证返回 401；默认独立请求不继承前次回复，显式续聊保留实际返回文本。
- 首次回放曾因两组共用显式 conversation_id 导致输入不等价；修正的是评测隔离，不是取消运行时上下文能力。最终通过记录为 v3。

真实模型为阿里云 `deepseek-v4-flash-0731`，3 题各跑两种设置，共 6 个请求：

| 案例 | 观测 | 能得出的结论 |
|---|---|---|
| BUS053 定增信息 | 调用 3 → 2；17.683 → 11.434 秒；累计输入 16049 → 9584 | 同入口、主要条件和零行结果，触发本次优化 |
| 贵州茅台近 5 个交易日 | 调用 4 → 3；10.690 → 8.213 秒；最终 API、参数和 5 行数据相同 | 未触发优化，基线多读目录；不能归因于本次改动 |
| RTEF070 上汽增长逻辑 | 调用 4 → 4；自选研报范围不同，14 → 6 行 | 不是效果等价的性能对照，不纳入无损提速结论 |

BUS053 明确删去的末轮为 2.049 秒、6465 输入 token、101 输出 token；整体 6.249 秒差还包含启动、缓存和模型波动。累计输入含服务端缓存命中，不能等同未缓存 token 或按同价推算费用。推理 token 若是输出的子集，不能重复相加。

本地原始证据（已确认存在，不随本次文档推送上传）：

- `outputs/finance_empty_result_replay_20260908_v3/report.json` 及相邻模型请求、原生事件文件。
- `outputs/finance_empty_result_ab_20260908/baseline/`、`optimized/`。
- 首次失败及 v2 记录仍留本地，不能将失败目录冒充最终成功证据。

## 5. 交接时的代码与部署状态

本次文档编辑前直接核对：

- 分支 `agent/financial-tool-design-protocol`；本地 HEAD `a42ab5f659d55df9dc606a91c7b0f96eefcf98de`。
- 开始整理时，本 session 上述相关实现已经提交，无待提交的已跟踪改动；现有未跟踪文件不应混入本次提交。
- 后续会有本交接文档提交，因此 `a42ab5f` 是检查基点，不是声称永久的最新 HEAD。

整理期间工作区又出现前后端及金融提示词等已跟踪修改，不属于本次交接整理。本次只提交这份专项交接、主框架交接和旧实施报告的状态补记共 3 个文档；不包含、覆盖或清理其他修改。接手者必须重新检查工作区，不能理解为全仓干净。

最新主框架交接记录业务版本 `923a94a` 已同步服务器，两项服务于 2026-09-09 19:37:51 CST 重启且 active。本次仅引用该已有记录，**未重新 SSH 核实，未部署或重启**。旧报告“尚未提交／待重启”只代表写作当时状态。

当前代码已包含后续 session 的完成控制、确定性结果过滤排序、全量读取修复、Skill 预算和交互调整。它们不是本次三项优化的实验变量，不可覆盖回旧版。详情只在 [当前主框架交接](main_framework_session_handoff_20260909.md) 维护。

当前提交绑定的完整业务评测、并发压测、长稳和恢复证据仍不足，生产运行维保持 `NOT_READY`；历史局部测试、健康检查和代码上线都不能替代上述证据。已知旧 Skill 数量断言也未全部处理，不宣称全仓测试全绿。

## 6. 未完成的优化与新发现

主框架最新代表题已出现：默认先取 100 再查全量、确定前 10 后重复读大样本、过滤子集无法命名复用、答案统计口径错误及缺少取证的因果解释。应先追踪这些已有证据，不能只凭调用次数减少判断效果改善。

历史研究提出但**未由本 session 实施**的方向：

| 方向 | 接手时须先验证 |
|---|---|
| 历史目录、工具结果精确去重 | 当前是否仍重复；保留完整语义与日志；重排输入可能降低服务端前缀缓存命中 |
| 可逆紧凑表格编码 | 序列化无损不等于模型效果等价，需同题验证理解与筛选条件 |
| 查询内部并行 | 原生工具是否 concurrency-safe、引用依赖、共享注册与 DB 池；不能只调并行上限 |
| 结果分页读取优化 | 是否仍从头扫描或加载完整 JSON，先量化瓶颈 |
| 长期 worker/session 资源回收 | 核对当前 SDK 的 session 生命周期与内存；不把潜在资源问题说成已证实串上下文 |

此前研究入口：[上下文审计](financial_qa_dsh_context_audit_20260906.md)、[此前实施](financial_qa_dsh_context_implementation_20260906.md)、[框架与 fast 模式研究](financial_qa_dsh_fast_mode_and_framework_review_20260904.md)。这些均需对照当前实现，不能直接将旧候选列表当成待办指令。

## 7. 评测与新 session 执行顺序

已有公开 MCP 评测能力，不必重新造脚本：

- [使用说明](../finance_mcp_evaluation.md)、`scripts/eval_finance_mcp.py`：query/题集、并发、access token、`--response-mode data` 或 `both`、Excel 输出。
- `scripts/create_finance_access_token.py`：临时 token，默认 4 小时，`--ttl-hours` 可配置。只提供入口，本次没有创建 token、读取密钥或修改认证。
- `scripts/eval_financial_qa_dsh_continuity.py`：历史语义连贯性回归入口；修改历史输入或会话行为时不能只测单轮。

建议顺序：

1. 读上述入口，核对 git 状态和用户新指令；继续效率主线，不自动接管 Skill 业务设计。
2. 先读现有 trace，选少量简单取数、组合筛选、Skill 分析题；不要立即全量付费评测。
3. 固定 commit、环境、配置、题目和 scorer，分别记录实际 API/条件/结果、LLM 与工具次数、退出原因、含缓存输入、未缓存输入、输出及耗时。
4. 先消除可复现的通用冗余，协议变动补正常流、兼容输入、严重错误回归；同题效果下降先找根因，不打逐题补丁。
5. 原始证据保存在获授权的本地位置；结论明确区分静态检查、固定回放、实际模型观察及生产验证。

## 8. 本次落盘与推送范围

用户在要求交接摘要后明确要求“全部落盘并推到远端”。本次范围为：完整保存上述交接、更新相关文档的过时状态、提交并推送当前分支到 GitHub/origin 与 Codeup/codeup。Git 推送不等于生产代码同步或服务重启。

不提交 `.env`、凭据、原始 DB 数据、评测 trace 或不属于本任务的未跟踪文件。原始证据已在本地，文档保存入口和必要结论；跨机器续接需另行确认数据授权和保留方式。

新 session 开场可直接使用：

> 先读 AGENTS.md、主框架交接和 financial_qa_dsh_optimization_session_handoff_20260909.md，核对当前代码。继续 DSH 金融查询效率优化，只消除冗余，不能降低效果和稳定性。第一批确定性空结果终止已实施且有历史回放证据，当前代码还有较新的完成控制和 Skill 改动。先从现有代表题的重复查询、结果子集复用和证据口径问题入手，不新增逐题提示词或业务 validator；小样本验证后再决定下一步。生产操作遵守人工 Gate。
