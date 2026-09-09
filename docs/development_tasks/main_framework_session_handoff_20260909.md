# 主框架开发交接：当前状态与下一步

更新日期：2026-09-09。用途：新 session 接手，不必重新追溯整段聊天。

本文记录已核实的状态、职责和断点，不将方案、历史评测或健康检查当作当前生产效果证明。根目录 `main_framework.md` 是 8 月架构地图，其中版本、运行分支和测试数字已过时；近期状态以本文及对应代码为准。

## 1. 先看结论

- 主框架近期重点是 **DSH 金融问答、Skill 接入、数据工具执行、完成控制、过程展示和可观测性**。继续以 DSH 为优化主线，CC 保留冻结比较基准，不为兼容 CC 增加复杂度。
- 已修复“读完样例就提前收尾”“第二次查询错误就结束”“显式全量仍截断为100条”等确定性问题；中间结果已能由程序筛选、排序，而不是必须让模型逐行挑选。
- Skill 请求的资源上限已提高，但不是增加必须跑满的轮数。最终效果仍有冗余调用、答案统计口径和无证据归因问题，不能宣布全部解决。
- 当前代码已推送 GitHub、Codeup，并同步服务器；**服务器两项服务已于 9 月 9 日 19:37:51 重启且处于 active**。不要照旧发布报告再次把“待重启”当作当前状态。
- 最近完成的是数据库对话/MCP轮次统计，Excel 已产出。当前没有正在跑的评测；下一步应先看统计和已有 trace，再选择主框架优化点。

## 2. 代码与服务器状态

| 项目 | 当前值 |
|---|---|
| 本地仓库 | `/Volumes/ext/fin_agent` |
| 分支 | `agent/financial-tool-design-protocol` |
| 本地、服务器 HEAD | `923a94a7538767aa3a92e1b36b9f1d9f38e32c60` |
| GitHub remote | `git@github.com:cyrishe/fin_agent.git` |
| Codeup remote | `git@codeup.aliyun.com:684beabd28a6beb51d765af2/fin_agent_2c.git` |
| 服务器 | `che@39.106.248.18` |
| 服务端仓库 | `/home/che/cyris/fin_agent` |
| Web服务 | `fin-agent-web.service`，端口22056 |
| API/MCP服务 | `fin-agent-finance-api.service`，端口22054 |
| 外部入口 | `https://ai-agent.kingdomai.com/fin_agent/` |

本次只读核实：两个服务均 active，启动时间均为 `2026-09-09 19:37:51 CST`；API `/health` 返回 `ok=true`、`default_runtime=dsh`，API key认证已配置。服务器无已跟踪文件修改。健康检查不代表真实问答、MCP或Skill效果已经全链路验证。

最近提交：

| Commit | 内容 |
|---|---|
| `923a94a` | 提高Skill工具调用上限，完成的请求不因上限增大而强制多跑 |
| `9d66556` | DSH完成控制解耦、中间结果程序筛选、全量截断修复 |
| `1c647fe` | Skill分析预算、回答与参考数据分离 |
| `4bda852`、`9dab007` | 当时的部署回执；“待重启”现已被上述新状态覆盖 |

CC冻结基准：`cc-reference-20260902-rebuilt-v1`。这是经用户接受、基于9月4日源码重建的9月2日比较基准，不是9月2日原始完整源码快照。不要继续修改它。比较说明见 [同五题报告](cc_reference_vs_dsh_five_20260908.md)。

## 3. 主框架负责什么

核心原则：**SOFT理解 → HARD事实与执行契约 → SOFT解释**。完整约束见根 `AGENTS.md`。

| 层级 | 主框架职责 | 不应越界 |
|---|---|---|
| 对话入口 | 身份、权限、Thread/Turn、附件、上下文、顶层路由 | 不按具体股票或题目硬编码分支 |
| Skill发现和运行 | 可选能力发现、渐进加载、工具补充、生命周期、真实加载事件 | 不强迫所有取数题选Skill，不代替Skill写业务方法 |
| DSH/Harness | 原生扩展点、工具可见性、执行包契约、超时/取消/预算、结束事实 | 不增加逐题must清单、金融语义validator或额外审查模型 |
| 数据执行接入 | 稳定API协议、引用、权限、执行结果、可确定的过滤和分页 | 不让模型生成真实SQL，不让展示页数决定任务完成 |
| 结果和前端 | 标准事件、Surface、历史恢复、过程状态、折叠参考数据 | 不为漂亮UI添加虚假推理状态、进度或业务字段 |
| 运维与统计 | 配置适配、usage、终止原因、回归证据和可恢复性 | 不把工具调用次数当LLM轮数，不把健康检查当发布质量门 |

工具开发、Skill资产、回测体系仍各有自身职责。主框架维护它们接入系统的骨架；只有明确集成问题或用户授权才改其内部实现。

当前关键入口：

- Web/同步/SSE：`src/web/flask_app.py`。
- 上下文与路由：`src/services/assistant_dispatch_planner.py`、`conversation_preprocess_service.py`、`context_resolution_service.py`。
- 金融DSH：`src/scenarios/financial_qa/dsh_service.py`、`dsh_loop_policy.mjs`、`tools.py`。
- 中间结果视图：`src/scenarios/financial_qa/result_view.py`。
- 金融独立服务：`src/finance_api/`；部署说明 `docs/finance_api_service.md`。
- 会话持久化：`src/services/runtime_conversation_service.py`；表 `aiia_runtime_thread`、`aiia_runtime_turn`。
- 前端：`frontend/src/surface.ts`、`components/MessageItem.tsx`、`SkillActivity.tsx`、`AnswerEvidence.tsx`、`TurnProcess.tsx`、`RunPanel.tsx`。
- DSH原生源码：本地 `/Volumes/ext/deepseek-harness`。金融控制是本项目利用原生hook/guard实现，不能说所有金融逻辑都是DSH自带。

Skill体系历史文档 `docs/skill_system_v2_architecture.md` 包含设计和迁移设想，不能全文视作已实现；当前资产数量也不是最早的两个Skill。最近测试已反映15个入口，旧测试仍期待12个，需核对而非机械改数。

## 4. 最近已经实现的改动

### 4.1 完成控制与预算

- 普通模式查询成功后，目录、查询和结果读取继续开放。`sample_complete`只表示样例展示事实，不表示整个任务完成。
- 取消整轮第二次查询错误即终止的独立限制；保留总预算、重复调用、超时等保护。
- 利用DSH原生 `agent/turn-stopping`，检查已提交执行流的成功前缀、失败步骤、未执行后缀和显式未完成声明，默认最多一次补充引导。
- 未强制启用DSH原生 `todo_write`。Todo是模型计划，不是条件已查齐的证明。
- **模型从未表达过的遗漏条件，现有机制不能确定性识别。** 不应对外声称实现了语义全覆盖保证。

实际加载Skill后的标准分析预算：目录16次、`finance_query`12次、结果读取8次；此前分别12/8/6。普通基础预算6/3/2未改，fast/data-only未扩张。查询一次可以含多个API步骤，这不是12次LLM上限。

分析 `maxTokens=8192`、推理low保持；默认请求超时300秒，具体环境可覆盖。提示词没有“剩余轮数”或“至少跑几轮”。资源上限不是运行目标。

### 4.2 确定性数据处理

- `load_finance_result` 增加可选 `filter`、`order`，沿用现有安全协议；Python先处理完整已存结果，再投影、分页，最多展示50条。
- 支持既有contains及 `code in rN.code` 集合引用，不使用eval，不让模型写SQL。
- 原始rN不变；当前过滤视图不生成新的命名子集。因此后续复用“刚才的前10”仍可能需要再表达条件，这是待观察的通用效率缺口。
- 修复行情窗口、估值明细/窗口、财务明细的 `limit=-1` 全量行为；默认100和正数上限保留。**没有审计全部provider的全量行为。**
- 执行流部分失败时保留成功步骤真实条数，未执行或失败步骤不再显示为“0条查询成功”。

### 4.3 交互

- 实际加载Skill时展示专业方法活动，不以猜测显示“已走Skill”。
- 分析答案与“参考数据”折叠区分离，纯取数仍可直接展示；保留分页和延迟挂载。
- 整个请求尚未结束时持续显示处理中，不能因最后一个工具成功就让界面像停住。
- 沿用真实run状态，不增加虚假思考阶段。展示fixture：`frontend/tests/fixtures/answer-experience.html`。

详见 [实施与验证](finance_harness_completion_implementation_20260909.md)、[预算说明](skill_execution_budget_20260909.md)、[答案体验](skill_answer_experience_20260909.md)。

## 5. 关键案例：沪深300筛选

原题要求60日走势较强、PE TTM<50、最近一期ROE>0，前10及理由。

原生产请求 `066b77822dea43dd8e7cbef567297c3f`，thread4244、turn2211：8次DSH加3次外层LLM，共11次，约162秒。不是“确实没有符合股票”，而是没有完整取证：PE/ROE未执行、走势只有100条；旧query修复限制导致结束。

最近本地隔离DSH的v3回归：12次LLM、93.5秒；走势、PE、ROE均覆盖300只，程序交集排序前10及数值复核正确。简单行情对照题3次LLM、8.9秒。

仍有问题：

1. 先默认查100，再显式查300，重复执行。
2. 前10已经选出后仍读两组前50，再按10个代码读取指标，存在冗余。
3. 答案把229只PE/ROE交集说成229只同时上涨；实际再加上涨为112只。前10相同不意味着整篇回答正确。
4. “资金流入”“景气回升驱动”等原因缺对应取证。

这次是本地、隔离DSH、不同数据时点，不是当前服务器HTTP全链路效果，也不是扩大预算后的完整回归。不能据此计算生产提速比例或宣布100%正确。

证据：`outputs/finance_harness_completion_20260909_v3/`；固定题 `tests/evals/finance_harness_completion_smoke_20260909.json`；脚本 `scripts/eval_financial_qa_dsh_context_ab.py`。完整步骤见上述实施报告。

## 6. 最近完成：全部请求轮次统计

只读统计快照：**2026-09-09 20:13:16（北京时间）**。

Excel：[`对话与MCP轮次统计.xlsx`](../../outputs/request_rounds_20260909/对话与MCP轮次统计.xlsx)。每个请求包含核心/外层/合计LLM次数、预算耗尽及类型、状态、耗时和证据来源；不导出手机号、问题和回答正文。

| 类型 | 请求数 | 明确触发预算限制 | 未发现限制且有可判定证据 | 无法判断 |
|---|---:|---:|---:|---:|
| 对话 | 1613 | 37 | 205 | 1371 |
| MCP | 42 | 15 | 27 | 0 |
| HTTP数据API | 45 | 10 | 35 | 0 |

注意：

- 当前Fin Agent合计1700个数据库请求；共享库旧 `conversation_history` 和simpleBI记录不属于当前运行链路，未混入。
- 对话历史缺结束原因很多，不能把未知算未耗尽，更不能据此推导整体准确率。
- “耗尽”包括查询、读取、旧修复预算，不等于LLM固定轮数用完，也不必然意味着答案错误。
- 流式核心usage可能不含外层路由；同步有些已合并。统计已区分，不能简单再相加。
- 数据库尚不能独立给出完整轮数/终止原因，部分必须关联日志。改进持久化是候选后续工作，本轮统计没有实施该改动。
- 中间脚本及指标在 `outputs/request_rounds_20260909/`，未提交Git。没有数据库写入。

## 7. 下一 session 建议任务顺序

| 优先级 | 要做什么 | 如何判断完成 |
|---|---|---|
| 先验证 | 当前服务器选少量历史代表题：简单取数、组合筛选、Skill分析 | 记录实际路由、LLM/工具次数、退出原因和结果；验证新版本不是仅能启动 |
| 主线效率 | 跨题确认默认截断、重复全量查数、前N子集复用造成的浪费 | 从trace找共性，只在现有结果/调用契约做必要最小调整；不增加每题规则 |
| 结果解释 | 检查数字口径与未取证因果解释 | 使用已有结果证据改善SOFT输出，不新增个股/条件validator |
| 可观测性 | 评估请求级LLM计数、预算配置及终止原因持久化 | 明确核心/外层计数口径，历史未知保持未知；先确认方案再实现 |
| 回归维护 | 检查15个Skill与旧12入口断言；覆盖不同subject/dataview/op | 区分合理资产变化和真实行为回归，不为测试绿随意改期望 |

评价目标是路由大致合理、Skill不足能继续用工具、取数和执行可靠、效率可接受。少量模型错误可以由loop修复；优先排除结构性缺陷，不追求靠提示补丁把每道题变成100%。

不要擅自把这些候选事项全变成实现任务。先延续用户选定的主线，再提出最小改动及验证。

## 8. 已有测试与未验证边界

- `9d66556`相关Python七文件：本地及服务器隔离环境420通过、1排除；DSH Node测试56通过。
- `923a94a`预算改动：Node57通过，Python运行时/执行模式43通过。
- 最近前端124测试通过、TypeScript和Vite构建通过。
- 已知排除：旧Skill目录测试期待12，现有15；未消除该失败，不宣称全仓测试全绿。
- 未完成当前提交绑定的全量业务评测、并发压测、长稳或恢复演练。生产可信运行维不能由这些局部测试推成READY。

部署证据：`deploy/server/ai-agent-kingdomai/RELEASE_20260909_HARNESS_COMPLETION.md`。备份目录：`/home/che/cyris/fin_agent_deploy/harness-completion-20260909-8zDWUU/`，含旧源码和前端备份。旧回执“待重启”只代表当时状态。

后续生产重启、数据库和凭据操作遵守人工Gate。不要打印或提交.env；本文件不保存任何密钥。新机器配置入口是 `.env.example` 与部署文档。

## 9. 本地资产和接手注意

- 当前已有未跟踪运行目录、评测报告、前端锁文件及PDF等，不属于本次全部产物；不要 `git add .`、清理或覆盖。
- `outputs/`中的Excel与trace不随clone传播。新session在同机器可继续使用；跨机器接手先确认所需证据的授权与保留方式，不批量复制生产数据。
- 本交接文档是本轮新文件；本轮用户只要求落盘，没有额外提交、推送或部署。

新 session 可使用以下开场说明：

> 先读根AGENTS.md和docs/development_tasks/main_framework_session_handoff_20260909.md，核对当前git状态。你负责主框架，不接管业务Skill设计。当前DSH完成控制、结果程序筛选、预算上限和交互改动已上线；先复核已有证据和用户新指令，围绕真实通用根因继续，不添加case-by-case提示、业务条件清单或多余状态。统计Excel位于outputs/request_rounds_20260909/。
