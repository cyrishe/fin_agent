# Fin Agent 系统框架基线审计报告

- 审计日期：2026-08-26
- 审计提交：`90be897`（工作区存在未提交改动，本次只读检查，不评价这些改动的业务正确性）
- 审计范围：体系结构、代码质量、工程质量、稳定性、性能、安全风险与部署方式
- 定位：这是后续 commit 增量审查的系统框架基线，不替代金融业务口径验证或真实 Provider/数据库全链路验收。

## 1. 总体结论

Fin Agent 已经形成了较完整的单机研发基线：对话上下文、顶层路由、Agent Provider、Tool/Skill、金融问答、代码执行、回测、定时任务、Surface 渲染和 owner scope 均有实际实现，测试覆盖也明显高于普通原型项目。SOFT → HARD → SOFT 的职责原则在会话、运行资产、调度和 Provider 适配中已有较好体现。

但当前仍属于“功能完整度较高的研发态系统”，尚不能直接按 2C 公网产品或 B 端多实例服务部署。最主要的问题不是业务能力，而是产品化边界尚未闭合：管理写接口没有权限门禁，默认启动 Flask Debug Server，流式运行依赖进程内一次性状态，公开高成本入口缺少统一限流与请求体上限，本地文件资产和多实例部署冲突，测试会读取真实 `.env` 并访问真实 LLM。

结论分级：

- 本地受信任环境研发：可继续使用，但应先修复测试隔离并明确安全运行方式。
- 内部受控单机试用：有条件可用，需关闭 Debug、限制网络入口、禁用或保护 Studio 写接口、限制上传与高成本请求。
- B 端正式部署或 2C 公网：当前不建议上线；P0 项均为发布阻断项。

## 2. 当前架构判断

### 2.1 已形成的正确骨架

1. Provider 差异主要收敛在 `src/services/agent_providers/`、Codex harness 和 Finance CC session 适配层，业务层没有直接消费原始 Provider 事件。
2. Thread/Turn、owner、revision、scheduled run lease、artifact revision 等真正需要寻址和恢复的事实进入 HARD 协议；大量业务表达仍由 Skill、Prompt 和 Surface 承载。
3. 对话上下文加载有固定窗口和回答预览上限，避免完整历史结果直接重复进入 Prompt。
4. Scheduled Task 使用数据库快照、租约和心跳，Web 入队与独立 worker 执行分开，失效恢复边界比普通内存调度器可靠。
5. 未信任 Python 代码默认只接受 formal sandbox；没有可用 bwrap/nsjail/docker/podman 时 fail closed，没有静默退化成本地执行。
6. 前端以 Surface/Renderer 为主，过程事件与最终结果已经基本分离，未知渲染对象存在降级路径。
7. owner scope 在会话、附件查找、Skill 候选、自定义工具和结果下载等主路径中已有明确实现。

### 2.2 当前结构性债务

- `src/web/flask_app.py` 约 6089 行、101 个 `@app.route`，同时承担装配、身份、上下文、同步/流式编排、资产编辑、运行分支和响应映射。
- `src/services/custom_tool_service.py`、`tool_plan_runtime_service.py`、`mysql_utils.py` 均超过 3000 行，职责集中且测试替身/依赖边界复杂。
- Flask 模块在 import 时创建大量进程级 service、线程池和 session manager，不利于 app factory、按环境配置、worker fork 安全和测试隔离。
- 同步 `/api/chat/dispatch` 与 SSE 流式入口仍各自编排身份、Thread/Turn、上下文、执行与写回，行为漂移风险客观存在。
- 新旧 Surface、render payload、legacy runtime axes 仍并存。兼容层目前有必要，但应按真实调用证据逐步收敛，不能继续扩张第二套协议。

## 3. 发布阻断项（P0）

### P0-1：Studio 管理写接口没有认证和角色授权，并缺少安全名称约束

证据：

- `src/web/flask_app.py:5024`、`:5062`、`:5090`、`:5356`、`:5389` 直接暴露 Application、Agent、Skill、Finance Catalog 和 Tool 的 PUT/写接口。
- 这些路由没有调用身份解析，也没有管理员/组织角色授权。
- `src/services/application_studio_service.py:146`、`agent_studio_service.py:142`、`skill_studio_service.py:306` 直接把 URL 名称拼到仓库目录并写文件，没有统一 slug 正则、`resolve + relative_to` 或保留名称检查。
- 多文件 Bundle 也不是事务式原子发布，写到一半失败时可能留下部分新版本。

影响：服务一旦对外可达，匿名访问者可以修改系统运行资产；特殊名称还可能越出预期的一级资产目录。运行中的 Agent/Skill/Tool 行为会被直接改变，属于系统完整性风险。

最小整改：

1. 生产默认关闭全部 Studio 写路由，显式配置开启。
2. 写路由必须要求 member + admin/tenant-admin 权限；身份和角色是必要 HARD 事实。
3. 统一资产名规则，例如 `^[a-z][a-z0-9_-]{1,63}$`，并在最终路径上执行 containment 校验。
4. Bundle 先写临时目录、完整校验，再以 revision/目录切换方式发布；不要逐文件覆盖在线资产。

### P0-2：默认启动方式在 `0.0.0.0` 上运行 Flask Debug Server

证据：`src/web/flask_app.py:6081-6085` 无条件调用 `app.run(debug=True, host="0.0.0.0", ...)`；README 和本地启动脚本均直接运行该模块。

影响：这不是生产 WSGI 部署方式。Debug 模式会扩大异常信息和调试器暴露风险，单进程开发服务器也不提供明确的并发、优雅退出、worker 管理和容量模型。

最小整改：

1. `debug` 默认必须为 false，仅显式本地开发变量可开启。
2. 增加 app factory 和生产 WSGI 入口。
3. 提供至少一种正式部署清单：容器或 systemd/进程管理器、反向代理、环境变量、迁移、Web、schedule worker、sandbox runtime、健康检查和优雅退出。

### P0-3：公开高成本入口没有统一资源治理，请求体和上传也缺少总量上限

证据：

- `/api/chat/dispatch`、`/api/chat/stream/start`、回测、Tool/Skill job 和多数 Studio/执行入口允许 guest 或匿名调用，没有统一速率、并发、租户配额或成本预算。
- `src/web/flask_app.py:817-821` 接受任意 JSON/form payload；Chat/SSE 没有 content-length 和用户文本长度上限。
- `src/web/flask_app.py:4304-4318` 与 `attachment_service.py:70-106` 没有单文件、总文件数、单请求总字节和用户磁盘配额；文件先完整落盘后才获得大小。
- Backtest 路由 `src/web/backtest_routes.py:18-31` 直接执行，没有 owner、限流或并发容量门禁。

影响：公网环境下可造成 LLM 费用滥用、线程/连接耗尽、内存增长、磁盘写满和数据库压力放大。

最小整改：在 HTTP 边界增加少量统一的 HARD 保护：请求字节上限、文本长度、附件数/单文件/用户总配额、按 owner/IP 的并发与速率限制、昂贵能力的每日预算。不要为每个业务场景增加不同状态机。

### P0-4：SSE Run 是无 TTL、无容量限制、一次性 pop 的进程内字典

证据：

- `src/web/flask_app.py:157` 定义全局 `custom_tool_stream_requests`。
- `:4228` 写入后没有 TTL/容量清理；客户端只调用 start 而不连接 SSE 时记录会永久留在进程内。
- `:4252` 第一次 GET 即 `pop`；断线、EventSource 重连、进程重启、多 worker 或负载均衡到另一实例时无法恢复。
- `:4274` 为每个连接启动 daemon thread；`:4293` 无超时阻塞队列，客户端断开没有向 worker 传播取消。
- 前端 `frontend/src/api.ts:482` 在任何 SSE error 时直接关闭并报错，没有 resume 或状态查询。

影响：单机也存在内存/后台任务泄漏与断线丢结果；多进程和多实例部署必然出现“start 成功、stream 不存在”。

最小整改：建立 owner-scoped、带 TTL 的最小 Run 记录和事件游标；start、claim/attach、running、terminal、cancel 只保留确实需要恢复的生命周期事实。HTTP SSE 只是传输适配，执行必须独立于当前连接。支持 Last-Event-ID 或结果查询，不要求重放 Provider 原始日志。

## 4. 高优先级问题（P1）

### P1-1：测试环境会加载真实 `.env` 并调用真实 LLM

证据：`src/utils/ai_service.py:12-16` import 时加载仓库 `.env` 并构造全局客户端；`tool_plan_runtime_service.py:2388-2401` 的普通运行测试路径直接调用 `chat_qwen_json`。本次全量测试出现 1 个稳定失败；显式清空 Key 并把 LLM endpoint 指向不可达本地地址后，同一用例通过。

影响：单元测试受外部模型输出、网络、配额和费用影响，无法成为可信 CI gate；还可能意外消耗生产 Key。

整改：pytest 默认强制 external-off；Provider 通过依赖注入或统一 fake transport 替换。真实 LLM/E2E 使用显式 marker 和独立凭据运行，结果与单元测试分开报告。

### P1-2：数据库连接缺少统一 timeout/pool/readiness 策略

证据：`src/utils/system_db_utils.py:33-43` 每次直接 `pymysql.connect`，未设置 connect/read/write timeout，也没有连接池；身份和会话服务频繁新建连接并执行 `SHOW TABLES`。`src/utils/mysql_utils.py:105-117` 还保留公网 IP 和用户名作为默认值，配置缺失时可能误连固定环境。

影响：数据库网络抖动可长时间占用 Web worker；高并发下反复握手和 schema probe 放大延迟；错误环境可能访问错误数据库。

整改：删除真实环境默认主机/用户，配置缺失 fail fast；统一连接参数、短连接超时和 statement/read timeout；按实际吞吐选择轻量连接池；schema readiness 在启动/健康检查验证，不在每个请求重复探测。

### P1-3：本地文件仍是多类权威运行资产，多实例无法保持一致

附件、session variables、runtime file artifacts、部分 custom tool context/session 和 Studio Bundle 使用本地目录。多实例下会出现元数据在数据库、文件在另一节点或资产版本不一致的问题；Pod/主机重建也会丢失数据。

整改：把“代码仓库内置资产”和“用户/租户运行资产”分开。内置资产随版本镜像只读发布；用户上传和生成物进入对象存储；需要可查询版本的运行资产进入数据库/对象存储并保留 content hash。不要直接把在线 Studio 写入源代码目录当作生产持久化。

### P1-4：附件只信任客户端 MIME，缺少内容识别与恶意文档边界

`attachment_service.py:52-63` 优先采用上传请求提供的 mimetype；没有 magic-byte 检查、压缩炸弹/宏文档策略或恶意内容扫描。下游又会解析图片、Excel、Word 和文本。

整改：保存前流式计数，保存后按文件签名识别类型；解析进程设置 CPU/内存/时间限制；B 端按安全要求接入病毒扫描。正式下载/预览必须走 owner 校验或短期签名 URL。

### P1-5：错误响应大量返回原始异常字符串

Flask 主文件多处在 500 响应中拼接 `str(exc)`，会把路径、依赖、SQL/Provider 细节暴露给客户端，同时错误契约不统一。

整改：系统日志记录完整异常和 trace/run/thread/turn 标识；客户端只返回稳定 code + 场景化说明。不要把 debug trace 混入正式业务 Surface。

### P1-6：缺少可复现的交付与迁移流水线

当前没有 CI 配置、Dockerfile/compose、正式 WSGI 依赖、健康检查端点、版本化 migration runner、lint/type/security gate。Python 只有单层 `requirements.txt`，运行、测试、爬虫、文档、PDF 和 Agent SDK 依赖混在一起；数据库初始化主要依赖手工执行散落 SQL。

整改顺序：先补最小 CI 和部署契约，再讨论复杂平台化。建议 gate 为 Python 单测、前端测试/typecheck/build、静态格式/质量、迁移 dry-run、无真实外部调用；发布前增加一条真实 SSE smoke。

## 5. 中优先级问题（P2）

1. 前端生产构建开启公开 sourcemap（`frontend/vite.config.ts:19-22`）。本次 `dist` 约 54 MB，其中 map 约 31 MB；应按部署环境关闭或上传到受控错误平台，不直接公开。
2. Vite 报告多个 500 KB 以上 chunk，最大 Mermaid 相关 chunk 约 691 KB（gzip 约 155 KB）。当前已有动态切分，但首屏与实际 Renderer 加载应通过浏览器性能数据决定下一步，不建议无证据大改。
3. `PromptContextCompilerService` 的 `token_budget` 目前主要是元数据，没有在编译器内执行截断/择优；候选能力增长后，Prompt 成本可能随目录线性增长。应在适配层做可测的 section budget，而不是增加更多协议字段。
4. 两处核心行情 HTTP 调用没有 timeout：`src/market_info/market_info.py:562`、`:698`。线程池批量行情可能被单个悬挂请求拖住。
5. `src/web/flask_app.py`、`custom_tool_service.py`、`tool_plan_runtime_service.py` 等超大文件降低变更可审查性。拆分应围绕真实主线消除重复编排，不应新增平行状态机或抽象层。
6. 工作区存在大量被忽略的 macOS `._*` 文件；本次 `compileall src` 因 AppleDouble 空字节失败。它们未被 Git 跟踪，但会污染本地扫描和未配置 `.dockerignore` 的构建上下文。
7. Guest session token 在数据库中按明文保存，而 member token 使用 SHA-256 存储。若 guest 持有会话、工具和报告资产，数据库泄露会直接形成会话接管风险，应统一只保存 token digest。

## 6. 验证结果

### 后端

- `pytest -q`：`1087 passed, 27 skipped, 1 failed`，耗时约 138 秒。
- 唯一失败：`tests/test_code_runtime_tool_plan.py::test_tool_plan_runtime_executes_code_step_and_downstream_binding`。
- 同一用例在显式禁用外部 LLM 后：`1 passed`。判断为测试隔离/非确定性问题，不是代码执行步骤本身失败。
- `pip check`：通过，无已安装依赖冲突。
- `compileall src`：因本地忽略的 `._*.py` AppleDouble 文件失败；真实受跟踪 Python 源码未显示语法错误。

### 前端

- Vitest：`19 files / 110 tests passed`。
- TypeScript：`tsc -b` 通过。
- Vite production build：通过，存在大 chunk 告警。

### 未覆盖

- 未执行真实数据库迁移和数据库故障注入。
- 未执行真实 Codex/Claude/金融数据 Provider 全链路。
- 未执行浏览器断网重连、SSE 中途断开、多 worker/多实例测试。
- 未执行第三方依赖漏洞扫描；本机没有 gitleaks、pip-audit、bandit 等工具，前端 pnpm 的供应链策略检查也因构建脚本审批中止。

## 7. 建议整改顺序

### 第一阶段：先封住部署风险

1. 关闭 Debug，提供 production WSGI 入口和健康检查。
2. 生产禁用 Studio 写接口；补 admin 权限和资产名/path containment。
3. 给 Chat/SSE/附件/回测/执行入口增加统一请求、并发和配额上限。
4. pytest 默认断开所有真实 Provider，恢复全绿且不消耗真实凭据。

### 第二阶段：稳定主线

1. 提取一个同步/SSE 共用的单轮 application service，统一 Thread/Turn、错误和写回。
2. 以带 TTL 的共享 Run 记录替换进程内 dict，支持断线查询/恢复和取消。
3. 清理 DB 默认地址，增加 timeout/readiness，减少每请求 schema probe。
4. 明确内置资产、用户资产和临时运行物的不同持久化位置。

### 第三阶段：工程化交付

1. 增加 CI、版本化 migration runner、容器/进程部署清单和 worker 拓扑。
2. 拆分 Python production/dev/eval 依赖；补依赖安全扫描和 secret scanning。
3. 根据真实观测优化 Prompt budget、数据库连接、前端 chunk 和长任务吞吐。
4. 建立 thread_id / turn_id / run_id / trace_id 的端到端关联，但 trace 只进观察通道。

## 8. 后续 commit 固定审查口径

每个新 commit 默认检查：

1. 主线与职责：属于 SOFT 框架、HARD 协议还是业务 Skill；是否把职责混写。
2. 协议：是否新增非必要状态/枚举/必填字段；兼容输入和生命周期是否清晰。
3. 权限与归属：owner、tenant、revision、路径、资产可见性和副作用授权。
4. 稳定性：超时、取消、重试、幂等、并发、进程重启、多 worker、部分失败和写回一致性。
5. 性能与成本：LLM 调用次数/上下文、DB 查询与连接、批量边界、线程/内存/磁盘、前端 bundle。
6. 部署：环境配置、migration、Web/worker/sandbox 拓扑、健康检查、可观察性和回滚。
7. 测试：正常流、兼容输入、真正严重错误；单元测试不得隐式访问真实外部服务。
8. 风险：是否扩大公网攻击面、泄露敏感信息、允许未授权资产写入或运行未信任代码。

审查输出采用：问题等级（P0-P3）→ 代码证据 → 对主线的确定性影响 → 最小修复建议 → 验证结果。没有证据的问题不为了“更完整”而扩张成新框架。
