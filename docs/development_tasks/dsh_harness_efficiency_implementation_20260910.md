# DSH Harness 效率优化实施与接手记录（2026-09-10）

## 范围与状态

本轮按用户要求，只处理有明确冗余证据的输入、往返和重复收尾。保留 Skill 正文、取证范围、模型与推理/输出预算、结果存储、权限、查询契约和失败修复能力。没有修改对象理解、统计口径、金融结论或业务 Skill 方法。

前置证据：[五维分析](dsh_harness_five_dimension_review_20260909.md)、[线上请求复核](dsh_production_case_review_20260910.md)。实现跨 Fin Agent 和相邻的 DeepSeek Harness 源码仓库，未部署、未重启生产服务。

实现基线：Fin Agent `9f1ea9eae7669790aa7620d65e34e1aafde4fbf2`；DSH `cd5ef8148158c3a752a658978873241fdf8e2bbc`（本项目当前使用的源码基线）。没有升级到更新的 DSH 发布版本，也没有修改 DSH 原先未提交的 DeepSeek translator 工作。

实现提交：Fin Agent `924114397d9bfc8dde05bd966087db368331f26a`；DSH `0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4`。验证环境与原有 DSH 未提交代码的指纹见[证据清单](evidence/dsh_efficiency_20260910/verification_manifest.json)，避免将当前工作区运行误称为纯净 commit 运行。

## 1. 独立问题：Session 保存历史，请求选择可见范围

### 判断复用现有语义模块

`ContextResolutionService` 已要求：完整且与前文无关的问题输出空 `context_refs`；追问保留实际依赖引用。本轮沿用其判断，不增加判定模型、关键词匹配或金融对象分支。

`AssistantDispatchPlanner` 将现有解析来源透传为 `semantic_turn.context_resolution_source`。只有来源明确为 `llm` 且 `context_refs == []` 时，金融 DSH 请求携带 `_finance_history_independent=true`。缺少来源、旧调用协议、显式命令、`no_context` 快路和有引用的追问均保留完整上下文。没有解析上下文不等于已判定无需上下文。

### DSH 原生协议

当前版本的 `startsRequestSeries` 只划分请求序列，不负责隐藏历史。新增原生、可回放的 `request/history` 事件：`{"fromTurn":2}` 选择已有第 2 轮及其后续消息；`{}` 恢复完整的当前 surface。数字必须指向已存在的 `turn/start`，拒绝不存在、负数、分数等边界。

既不清空 Session，也不将历史改写成摘要。角色、内容、工具配对和原始日志保留。`Session.requestHistory()` 读取选择，`deriveMessages()` 用同一日志重建请求；选择变化产生新请求序列。

Fin 策略在每轮 `agent/pre-step` 应用一次选择。独立问题只发送本轮；下一轮有依赖或没有明确独立判断时恢复完整 surface。目录复用继续依据**选择之后真正可见的执行包**，隐藏的旧目录不能授权新查询。旧 DSH 没有该能力时保守保留历史。

Token meter 同步区分请求压力与保留的 surface：请求总量按选中范围计量，归档节点统计保留；切换时废弃旧请求压力样本，等待新提供方样本，累计用量不变。

### 主机结果索引

独立问题的请求只注入 `next_result_name` 和空结果索引，避免再次复制整个旧 working_set。所有结果 handle、metadata、result_ref 与归属仍保存在原作用域；后续追问恢复完整索引。保留分配游标，避免别名冲突。

这是 HARD 的请求可见范围事实与 SOFT 的语义判断衔接，没有新增业务状态机。

## 2. 双终稿：将旧完成声明提示前移

对应案例 A、B、E。成功查询返回 `data_request_complete=false` 后，模型本来可能已完成回答，终止 hook 却基于旧声明要求重新回答。

本轮将同一份完成事实提示移到成功但未声明完成的查询之后、下一次本来就需要发生的模型步骤之前。使用已有 `stageSteers` 记录哪批事实已投递；模型据此选择回答后，不再仅因同一旧声明强制重写。

- 真实查询失败、失败后缀与成功 result_ref 仍由原终止检查补救。
- 后续新目录产生的新必需动作仍会检查。
- data-only 和 fast 完成语义不变；预算耗尽的收尾不额外注入这些提示。
- 不通过回答关键词、长度或措辞判断完成，不修改原推理与输出预算。

这是 Agent 执行事实的投递时机调整。线上历史样本中三次冗余重写合计输入 67,592、输出 3,579 token、约 39 秒；它们是定位证据，不是修改后的实测节省承诺。

## 3. 身份解析与目录读取并行

对应案例 F。标准模式首次方法/目录发现同时允许 `resolve_security`；身份解析本身不依赖某个金融执行包。模型已生成的独立身份与目录调用可在同一步执行，不再先拒绝身份、再原样重试。

金融查询仍要求事先可见的执行包。权限、参数校验、调用预算和重复调用保护不变，fast 保持原契约。

## 4. 已加载 Skill 正文去重

对应案例 H。初始显式 Skill 正文注明可直接使用、按需读参考。DSH 原生 post-execute 投影只在本次返回的**完整正文是当前可见文本的精确子串**时省去重复正文，保留 Skill ID、revision、content_hash、description 和复用说明。

正文只出现一部分、内容改变、内容不可见或读取失败时不省略。原工具执行、授权检查、方法加载记录和完整返回仍存在；必要参考读取不变。不因“Session 曾经加载过”就假设模型现在仍能看见，也不裁剪多 Skill 的专业方法。

这减少重复输入，不能保证单独减少一次模型调用，尤其是 Skill 原本与目录并行读取的案例。

## 5. 结果索引无损紧凑序列化

`FinanceResultRegistry.prompt_text()` 改为紧凑 JSON。只删除缩进和分隔空白，所有键、值、顺序、列覆盖、依赖与引用保留。测试解析 JSON 验证实际字段，不再绑定冒号后的排版空格。

## 验证与证据边界

- Fin Agent：64 条 Node 策略测试通过；相关 Python 测试共 97 条通过（分组运行，含金融查询/分页、DSH 运行时、Skill、调度来源透传）。
- DSH：针对 Session、Agent loop、token meter 的相关测试通过，覆盖独立轮次、工具配对、后续恢复、逐请求日志回放、非法边界、旧日志默认行为、请求压力与累计用量。
- 实际 `sdk-minimal` 子进程 + Python SDK + MCP Skill reader 的三轮回放通过，6 次本地固定模型响应；同一 Session 的独立问题隐藏历史，后续追问恢复历史，选择事件确实入日志。[脚本](../../scripts/verify_finance_dsh_history_replay.py)、[结果](evidence/dsh_efficiency_20260910/history_replay.json)。无真实模型费用，无金融数据库查询。
- DSH Host TypeScript 编译通过；文档和 lint 检查结果见收尾记录。

这些验证证明协议、可见证据和调用流程的边界，不证明真实模型输出逐字一致或线上业务效果已通过 A/B。真实模型效果、并发长稳和生产恢复维度仍不能标为 READY。本轮没有生产放行结论。

## 新发现但不扩展的恢复问题

实际 SDK 进程重启尝试没有恢复旧问答。当前 `packages/sdk/server/src/server.ts:createSession()` 对相同 ID 调用 `ctx.agents.create()`，没有走 `resume()`。这是现有入口实现的恢复缺口，新事件的日志重建本身已通过独立测试。

因此要区分：本轮“连续 Session 内恢复”和“原生日志重新构造”已验证；**当前 Fin SDK 入口的跨进程恢复没有通过**。本轮不将它扩展成稳定性或会话生命周期重构，后续应从 SDK 原生 create/resume 契约修复，不能靠补摘要或业务关键词弥补。

## 保留不做

- “易中天”等对象理解、评级统计与程序聚合、报告年份与观点覆盖、缺乏证据的因果解释。
- 缩短 Skill 正文、降低推理或输出预算、按是否最终使用来硬删除目录、将 limit=60 静默改成 50。
- 新业务 validator、关键词修补、业务枚举状态、全局重试/超时框架。

## 发布与回退

1. 需 Fin Agent 与配套 DSH 源码/构建一起交付。只更新 Fin、仍使用旧 DSH 时，历史裁剪保守失效，其他冗余优化仍可工作。
2. 新日志包含 `request/history`，旧 DSH 的事件词表会拒绝读取。不可直接用旧二进制降级读取新日志。可先撤回 Fin 选择策略并保留新 DSH；日志迁移与降级须单独验证。
3. 用固定合成/授权样本做小范围模型 A/B：专业分析、纯取数、追问、切换后返回旧话题。先检查对象、时间/单位、字段覆盖和正确性，再看输入、输出、请求数、耗时。输入减少本身不等于效果相同。
4. 保持生产部署、重启、回滚与数据库操作的人工 Gate；本轮没有执行这些动作。

## 收尾记录

- `pnpm exec tsc -b tsconfig.host.json --pretty false` 通过。
- `pnpm run test:docs`：15/15 gate 通过。
- 全 `doc-sync` 首次为 30/32 通过：文档编译包装命令因环境缺少 `npm` 无法启动，其等价 `doc-typecheck:contracts-ready` 已单独通过；另一项是新 Session API 的生成目录过期，已用仓库生成器更新并复查。
- 全仓 `pnpm run lint` 包装同样受缺少 `npm` 影响；直接运行 `lint:contracts-ready` 后，修正了本次唯一的 arrow-parens 问题，剩余类型错误位于未修改的 client/ui、extensions/ui 等文件。没有据此宣称全仓 lint 通过；本次改动的五个 DSH 源文件单独 lint 已通过。
- `git diff --check` 通过。没有改写其他任务的工作或执行生产写入。
- DSH 提交钩子的 staged lint、双语记录、空白与 vendor 检查均通过；仅报告一个原有行上的 unused-disable warning。

这些检查结果不代表生产部署结果。真实模型 A/B、当前 SDK 跨进程恢复以及全仓质量门仍须按上述边界分别验证。
