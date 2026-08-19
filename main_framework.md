# Fin Agent 主框架交接说明

> 用途：供新任务快速恢复主框架上下文。本文是当前实现的简明地图，不替代代码、协议测试或业务 Skill 文档。

## 1. 当前基线

- 仓库：`/Volumes/ext/fin_agent`
- 分支：`agent/financial-tool-design-protocol`
- 基线提交：`a255a92 Integrate framework tools skills and backtesting`
- 本文整理日期：2026-08-19
- 最近一次完整验证（2026-08-16）：后端 `1024 passed, 27 skipped`；前端 `110 passed`；TypeScript typecheck 和生产构建通过。
- 当前未跟踪的 `tmp/` 与 `output/pdf/` 是本地运行/报告生成物，不属于主框架代码。

## 2. 主框架的职责边界

项目分为四个相对独立的方向：

1. **主框架**：CC/Codex 基础串联、自然语言理解与问答、上下文、顶层路由、Provider/Session 生命周期、前后端交互、流式事件、持久化和渲染协议。
2. **工具开发**：自定义工具的需求、设计、编码、测试、版本和执行效率。
3. **Skill 体系**：Skill 制定、优化、测试、候选版本、管理与 Studio。
4. **回测体系**：回测服务、基准、运行协议和回测工具。

主框架只负责后三个方向接入系统所需的稳定骨架，不替它们固化业务规则：

- 可以修改：统一入口、路由、上下文、会话、权限、执行适配、事件、Surface、前端消费、可观测性。
- 原则上不修改：具体 Tool 逻辑、业务 Skill 内容、回测计算规则；除非修复它们与主框架之间的明确集成问题。

## 3. 总体设计原则

核心模式是：

```text
自然语言语义（SOFT） -> 稳定业务协议（HARD） -> 场景化表达（SOFT）
```

- SOFT：理解用户目标、上下文、反馈和表达方式。
- HARD：只保存跨阶段必须稳定传递的身份、状态、引用、修订、权限和执行约束。
- SOFT：由 Agent/Skill 生成解释、卡片内容和最终回答。

实施时优先保持主线简单：

- 不为假设风险增加状态、枚举、Validator 或协议层。
- 系统保存已有事实，模型只输出本轮新增的判断或语义资产。
- Provider 差异封装在适配器后，前端不消费原始 Codex/Claude 事件。
- trace、debug、测试证据与正式业务结果分离。
- Surface/Renderer 服务展示，不反向扩张核心业务协议。

## 4. 当前主链路

```text
React Composer
  -> POST /api/chat/stream/start
  -> GET /api/chat/stream/<run_id> (SSE)
  -> Flask 对话入口
  -> 身份、附件、Application、Thread/Turn
  -> AssistantDispatchPlanner
  -> ConversationPreprocessService
       -> 输入标准化
       -> ContextResolutionService
       -> 顶层意图与 turn_mode
       -> Agent 内部规划
  -> Tool / Skill / Finance CC / Direct 等执行分支
  -> agent.run.v1 事件适配 + LlmStreamBlockBuilder
  -> Surface Blocks / task_state / result refs
  -> RuntimeConversationService 持久化
  -> frontend surface.ts 归一化
  -> BlockRenderer / 专用 Renderer
```

同步入口 `/api/chat/dispatch` 仍然存在，主要链路优先以 SSE 入口观察真实交互。

## 5. 各层实现

### 5.1 前端入口

- `frontend/src/App.tsx`：对话工作台和运行状态的顶层组织。
- `frontend/src/components/Composer.tsx`：文本、附件、资产选择和提交。
- `frontend/src/api.ts`：同步 dispatch、SSE start、EventSource 消费。
- `frontend/src/surface.ts`：流事件合并、最终 Surface 对账、历史兼容转换。
- `frontend/src/components/BlockRenderer.tsx`：统一 Renderer 入口。

前端只应依赖标准事件与 Surface Block。后端最终 Surface 是权威顺序；流式中间块是临时状态，完成后要与最终 Surface 对账。

### 5.2 HTTP 与 SSE 入口

集中在 `src/web/flask_app.py`：

- `POST /api/chat/dispatch`：同步执行。
- `POST /api/chat/stream/start`：创建流式运行请求并返回 `run_id/stream_url`。
- `GET /api/chat/stream/<run_id>`：SSE 消费。
- `/api/custom_tool/stream/*`：与聊天共用当前流式骨架。

入口负责：

- 解析用户身份和 owner scope。
- 校验附件、交互动作及修订号。
- 创建或验证 Thread，并创建 Turn。
- 加载 Application/Agent 上下文。
- 调用统一规划和具体执行分支。
- 完成 Turn、保存结果并输出 SSE。

### 5.3 上下文与顶层路由

关键服务：

- `src/services/assistant_dispatch_planner.py`
- `src/services/conversation_preprocess_service.py`
- `src/services/context_resolution_service.py`
- `src/services/assistant_interaction_preprocessor.py`
- `src/services/agent_runtime_llm_planner_service.py`
- `src/services/execution_plan_service.py`

正确顺序是：

```text
输入标准化 -> 上下文语义补全 -> 顶层路由 -> Agent 内部规划
```

顶层路由只决定少量稳定事实：

- `selected_agent`
- `turn_mode`：`normal_qa`、`system_operation`、`tool_development`
- `domain / interaction_mode / execution_path`
- `semantic_turn`：原问题、解析后的问题、上下文引用

`normal_qa` 并不代表“禁用工具”，Agent 仍可在内部组合 Tool/Skill。可信的 slash command 和 UI action 优先走规则 shortcut，不必再次让模型猜测。

`/api/chat/dispatch` 当前可进入 custom-tool flow、vision intake、Finance CC、Skill、planned tool run、catalog browse、asset open 或 direct response 等分支。

### 5.4 Application 与 Agent 配置

- `src/services/application_runtime_service.py`
- `src/services/agent_runtime_service.py`
- `src/applications/`
- `src/agents/`

`investment_workbench` 是当前前端使用的 Application；它解析默认 Agent、允许的 Tools/Skills 和运行配置。主框架应通过配置和目录契约发现能力，不在 Flask 路由中硬编码具体金融业务逻辑。

### 5.5 Provider 与 Agent 运行

- `src/services/agent_providers/protocol.py`：统一 `agent.run.v1` 协议。
- `src/services/agent_providers/`：Claude/DeepSeek 等 Provider 适配。
- `src/services/codex_exec_skill_harness.py`：Codex/CC Skill 运行和事件桥接。
- `src/services/finance_claude_session_service.py`：Finance CC 会话、恢复和事件输出。

主框架关注统一输入、超时、取消、错误、usage 和事件；Provider 的模型名、URL、原始事件形态不能泄漏成前端或业务协议。

### 5.6 会话、上下文和持久化

- `src/services/runtime_conversation_service.py`
- 表：`aiia_runtime_thread`、`aiia_runtime_turn`

已实现的关键约束：

- Thread/Turn 按 `owner_type + owner_id` 隔离。
- 外部传入 thread_id 时校验归属，缺失与越权使用同类响应，避免枚举。
- 当前请求加载最近 5 轮 context window。
- 历史输出只保留 message、Surface、render payload、workspace、task_state 等可恢复内容；省略 raw events/stdout/stderr。
- Surface 的嵌套深度允许到合理范围，避免历史回放丢失表格和指标值。

上下文中应该传摘要、引用和当前 working set，不应重复注入完整表格、长文档或原始 Provider 日志。

### 5.7 事件与 Surface

- `src/services/llm_stream_block_service.py`
- `frontend/src/types.ts`
- `frontend/src/surface.ts`
- `frontend/src/rendering/`
- `frontend/src/components/BlockRenderer.tsx`

`LlmStreamBlockBuilder` 把 Codex/Claude/Tool 的原始事件转换为稳定的进度块和语义块。核心要求：

- 过程块与业务结果块分开。
- 同一逻辑进度可 patch/merge，不在 UI 堆积重复日志。
- 最终 Surface 是权威结果。
- 历史 payload 和旧 render block 目前由前端兼容层归一化。
- 未知 Block 应有安全降级展示，不能让整轮回答消失。

## 6. 当前实现的优点

- 顶层路由与 Agent 内部规划已经分层。
- 上下文解析发生在路由前，可保留用户原始问题和引用证据。
- Thread/Turn 有 owner scope，历史可恢复。
- Provider 已有统一协议和 event sink 边界。
- 前端基本统一到 Surface Block/Renderer。
- Tool、Skill、回测可以通过稳定入口接入，不要求主框架理解其业务细节。
- 对话主线、过程展示和最终结果已有较完整的回归测试。

## 7. 已知问题与技术债

### P0：流式运行请求仍是进程内状态

`src/web/flask_app.py` 当前使用：

```python
custom_tool_stream_requests: dict[str, dict] = {}
```

start 写入、SSE GET 时 `pop`。因此进程重启、多 worker、跨实例请求或 GET 前状态丢失都会导致 run 不存在。后续若进入多实例部署，应替换成 owner-scoped、带 TTL 的共享运行记录，并明确启动、消费、完成、失败和取消的最小生命周期。

### P1：同步与流式链路存在重复编排

`/api/chat/dispatch` 和 SSE worker 都处理身份、上下文、分支、Turn 完成和错误。应逐步收敛为一个执行函数，HTTP 同步与 SSE 只做不同的传输适配，避免同一输入在两个入口行为不同。

### P1：Flask 入口过于集中

`src/web/flask_app.py` 同时承担路由、装配、执行分支和响应组装。拆分时按真实主线提取 application service，不增加新的抽象层或状态机；先消除重复，再移动代码。

### P1：新旧协议仍并存

- 路由结果同时保留 canonical axes 和 legacy runtime axes。
- 前端仍兼容 `surface_blocks`、`render_blocks`、`render_payload`、`surface` 和 message/items。

在所有生产调用方完成迁移前不能直接删除兼容字段；应通过调用证据和回归测试逐项收敛。

### P1：上下文策略仍偏固定窗口

最近 5 轮能保证基本连续性，但长任务需要基于摘要、结果引用和 working-set 索引恢复，而不是简单扩大历史轮数。未来应优先做可寻址引用和预算控制。

### P2：真实运行证据需要持续补充

全量单元/协议测试通过不等于真实 Provider、数据库、SSE 断线重连和浏览器端到端全部成功。涉及主链路发布时至少补一条真实用户视角的流式 smoke，并区分“代码测试通过”和“外部依赖已验证”。

## 8. 推荐的后续开发顺序

1. 建立同步/SSE 共用的单轮执行入口，先保证结果、错误和 Turn 写回一致。
2. 把进程内 stream request 改成最小共享 Run 记录，支持 TTL、owner 校验和取消。
3. 统一 Provider timeout/cancel/error/usage 到 `agent.run.v1`，清理调用方特判。
4. 让最终 Surface 成为唯一权威展示协议，按证据逐步缩小 legacy adapter。
5. 强化上下文摘要、result refs 和 working set，避免长历史直接进入 Prompt。
6. 增加端到端可观测性：`thread_id/turn_id/run_id/trace_id` 串联，但 trace 不进入正式业务回答。

每一步都应是可验证的窄改动，不同时重写路由、Provider、上下文和 UI。

## 9. 常用验证命令

```bash
# 后端全量
PYTHONPATH=. pytest -q

# 主框架常见定向测试
PYTHONPATH=. pytest -q \
  tests/test_agent_runtime_llm_planner_service.py \
  tests/test_runtime_conversation_history.py \
  tests/test_llm_stream_block_service.py \
  tests/test_conversation_runtime_redesign.py

# 前端
cd frontend
npm test
npm run typecheck
npm run build

# 本地服务
cd /Volumes/ext/fin_agent
scripts/restart_local_services.sh
```

启动脚本返回后仍需独立验证，不能只相信脚本内 ready 文案：

```bash
curl -f http://127.0.0.1:22053/
curl -f http://127.0.0.1:22054/
lsof -nP -iTCP:22053 -sTCP:LISTEN
lsof -nP -iTCP:22054 -sTCP:LISTEN
```

## 10. 新任务接手清单

1. 阅读本文件和根目录 `AGENTS.md`。
2. 执行 `git status --short`、`git log -5 --oneline`，确认是否有其他任务的新改动。
3. 明确本次问题属于主框架、Tool、Skill 还是回测；只处理主框架及必要集成点。
4. 从用户输入沿真实链路追到最终 Surface、持久化和前端消费，不只检查单个函数。
5. 先复现和定位主线根因，再做最小修改；禁止用关键词、状态或 Validator 堆补丁。
6. 至少运行相关协议测试；涉及共享入口、上下文、SSE、持久化或 Renderer 时运行全量回归。
7. 报告中分开说明：已验证事实、推断、外部依赖阻塞和仍未解决的问题。

