# Fin Agent 生产可信与架构实现质量评审（本地工作区快照）

- 评审时间：2026-09-08（Asia/Shanghai）
- 系统标识：`SYS-FINAGENT`（来源于《研发域系统生产可信与架构评审操作指引》附录快照；正式登记仍需 Owner 核验）
- 范围：`/Volumes/ext/fin_agent` 单仓组件切片；未确认是否覆盖全部运行组成仓，不能上卷为完整系统生产验收
- Git 锚点：`5d775c629d5a7ed5d306254c2bee28515248626e`，分支 `agent/financial-tool-design-protocol`
- 工作区状态：dirty；57 个 tracked 文件发生约 `+1519/-311` 变更，另有较多 untracked 代码、测试、数据和文档。本报告评价的是**当前本地工作区快照**，不是 HEAD 提交本身
- 方法入口：SkillHub `ai-infra/kma-architecture-quality-assess@0.68.2`、`ai-infra/kma-production-trust-assess@0.68.0`
- 报告性质：试点、待校准、非绩效、非人事、非生产验收

## 首屏结论

Fin Agent 已具备较强的功能研发与自动化测试基础，但当前本地快照尚不具备可声明“生产可信”的证据闭环。架构与实现质量试评分为 **2.5/5.0**；静态四维成熟度试评分为 **2.6/5.0**；三个运行维均为 `NOT_READY`，所以七维综合分必须 `LOCKED/null`。安全门禁为 **待取证**，其生产放行效力等同阻断。

主导短板不是单个 bug，而是四个结构性问题：核心模块巨石化、权限矩阵尚缺系统级负向取证、缺少 current-ref 运行证据、缺少可执行的恢复回执。前后端默认测试门当前健康，但前端构建出现 500KB 以上 chunk 警告，性能预算尚未闭环。

## 方法与 Skill 分发限制

SkillHub 页面确认两个版本可见，并通过登录态下载 ZIP。下载包存在方法分发缺陷：两份 `SKILL.md` 均只含未展开的 `--8<-- "skills/.../SKILL.md"`，而引用目标未包含在包中；Doctrine 文件名乱码导致标准解压异常。包内维度文件仍可读取。本次因此是依据操作指引与包内维度判据的**降级直评**，不是通过完整 Skill 合同和机器门的正式评审。不能生成或宣称已通过正式 `result.json`/bundle gate。

## 架构与实现质量（D4）

| 诊断视角 | 分数 | 主要证据与判断 |
|---|---:|---|
| 系统边界与依赖方向 | 2.5 | `src/services`、`src/scenarios`、`src/finance_api` 已有领域分层，但 Flask 总装配与大量业务路由集中在 `src/web/flask_app.py`；未见可复算循环依赖报告 |
| 接口契约与数据/状态模型 | 3.0 | 有较多 schema、契约测试、错误码和 revision/owner 校验；新增鉴权边界对应的测试身份装配已校正并全量通过，系统级角色/owner 组合取证仍待补齐 |
| 配置与运行 profile | 2.7 | `.env.example` 覆盖面较完整、依赖版本有 pin；Web、Finance API、DSH、外部模型和数据库配置面较广，缺统一 profile 清单及配置契约门禁 |
| 实现健壮性与资源治理 | 2.8 | 多处具备 timeout、并发和 fail-closed 处理；但长任务、模型调用、文件/数据库/子进程等资源面广，缺 current-ref 压测与统一 admission/取消/隔离证据 |
| 可观测性与可诊断性 | 2.2 | 有运行 trace、数据状态页和健康接口；未发现统一 metrics/tracing/SLO、告警、关联 ID 与生产诊断回执闭环 |
| 可维护性、可测试性与复杂度 | 2.0 | 约 17 万行；`src/web/flask_app.py` 6386 行、`custom_tool_service.py` 4515 行、`mysql_utils.py` 3697 行。测试多但变更仍造成 5 个合同回归，非作者修改成本高 |
| 可演进性与技术债治理 | 2.4 | 有 revision、Skill、Renderer、协议演进意识和大量设计文档；多套 runtime/实验路径并存，缺模块退役、依赖方向和复杂度预算的持续门禁 |

算术平均：`(2.5+3.0+2.7+2.8+2.2+2.0+2.4)/7 = 2.5`。

本表为同一“架构手艺质量”构念的七个侧面，非七项独立证据。分数可比区间按指引解释：`±0.3` 内同档；`0.3–0.7` 需回到证据；`>0.7` 才值得追查实质差异。

### 循环依赖与供应链声明

- 循环依赖：本轮仅做 import/目录静态检索，未形成完整 Python/JS 模块图与 SCC 结果，因此证据不完整，不得声称“无环”。
- 供应链：Python 依赖在 `requirements.txt`/`requirements-finance-api.txt` 中 pin；前端同时存在 `package-lock.json` 与新增未跟踪的 `pnpm-lock.yaml`/`pnpm-workspace.yaml`，包管理真源不唯一。未执行 current vulnerability scan，漏洞状态未知。

## 生产可信七维

| 维度 | 状态/分数 | 结论 |
|---|---:|---|
| 效果评测 | `NOT_READY / null` | 仓内有金融问答与 REST/MCP eval 脚本和结果材料，但本轮未得到绑定当前 dirty 快照、数据集、scorer、模型、配置和 run URI 的统一效果评测 |
| 功能测试 | `3.2` | 后端 `1504 passed / 27 skipped / 0 failed`；前端 `111 passed`，typecheck、build 成功。默认回归门健康，但仍缺 coverage/变异测试门及外部依赖测试的统一分层 |
| 稳定性与性能 | `NOT_READY / null` | 缺绑定 current ref 的固定 workload、硬件、并发、长稳、资源曲线与失败恢复 run；前端 build 报多个 >500KB chunk，最大约 691KB（gzip 155KB） |
| 架构与实现 | `2.5` | 见 D4 七视角表 |
| 发布、运行与恢复 | `NOT_READY / null` | 存在 gunicorn 依赖、systemd/nginx 示例和 Finance API 部署资产，但缺 immutable image/pipeline/deploy readback/rollback/恢复演练、RTO/RPO 与备份恢复回执 |
| 数据、样本与评测资产 | `2.4` | 有大量金融评测脚本与 evidence 文档，但缺统一 registry、稳定 ID、Owner、授权/留存、GT/scorer 校准以及与当前 ref 的端到端绑定 |
| 智能体入口与知识化接手 | `2.1` | 根 `AGENTS.md` 能说明开发原则和测试方式，但没有七维“生产可信评审入口”五字段表；可发现性判 `NOT_PRESENT`，缺运维 Owner、权威证据入口和停止边界路由 |

静态四维平均：`(3.2+2.5+2.4+2.1)/4 = 2.6`。运行三维全部未就绪，七维综合：`LOCKED/null`。覆盖度：静态 `4/4`，运行 `0/3`。

## 安全门禁卡

结论：**待取证（不得生产放行）**。

已见正向变化：`src/web/flask_app.py:301-310` 新增写接口登录限制；`src/finance_api/app.py:21-36` 与状态/usage 路由新增管理员会话校验；临时 API token 使用 HMAC 与 TTL，并有专项测试。

阻断原因：

1. 鉴权专项与全量测试已通过，但尚未形成端点×成员/管理员/访客×owner/非 owner 的系统级可达性矩阵，现有测试不能直接替代完整门禁取证。
2. 系统具有代码生成、工具执行、模型调用、文件与数据库访问等高风险能力；本轮未完成从外部入口到危险执行链的可达性与隔离边界取证，不能判绿。
3. 未执行 secrets、依赖漏洞、越权/跨租户、上传与 SSRF/RCE 负向测试套件；缺失本身不直接定黄，但使门禁保持待取证。

解除条件：形成端点×角色×租户×资源归属矩阵及负向测试；对代码执行/子进程/网络/文件/数据库能力给出隔离、allowlist、超时、取消和审计证据；完成 secrets 与依赖漏洞扫描并人工复核高危项。

## 当前本地测试证据

- Backend：校正测试身份装配后运行 `.venv/bin/python -m pytest -q`，`1504 passed, 27 skipped, 0 failed`，134.93 秒。
- Frontend：Vitest `19 files / 111 tests passed`；TypeScript typecheck 通过；Vite production build 通过（23.64 秒），但存在大 chunk 警告。
- 初次运行的 5 个失败均由旧测试继续使用匿名身份、请求被新鉴权边界提前拒绝造成。测试现已按既定权限契约装配会员身份；业务实现未修改，相关专项 15 项及后端全量均通过。

## 已完成的低干预优化

1. 仅调整 5 个失真测试的身份前置条件，恢复默认后端质量门；未放宽鉴权或修改任何业务模块。
2. 抽取 custom-tool workbench 测试的统一会员身份装配，避免同一测试文件重复且不完整地 mock 身份来源。
3. 在根 `AGENTS.md` 增加七维生产可信证据导航，明确权威入口、责任边界和 `NOT_READY` 规则；未新增协议字段、状态、validator 或运行依赖。

## 优先治理顺序

1. **P0：完成安全门禁取证。** 优先覆盖公网/客户可达入口到工具执行、代码执行、数据库与文件访问链，形成角色与 owner 隔离负向测试。
2. **P1：补 current-ref 运行证据。** 建立固定金融 QA workload、效果 scorer、并发/长稳 run，以及部署、回滚、恢复演练回执；三类证据必须绑定同一 commit、镜像、配置和数据快照。
3. **P1：拆分巨型变化热点。** 先从 `flask_app.py` 的认证/会话/工具/流式路由边界，以及 `mysql_utils.py` 的连接、查询、schema 职责入手；目标是建立清晰依赖方向和独立合同测试，不做纯行数拆分。
4. **P2：统一供应链与性能预算。** 前端只保留一个 lockfile 真源，CI 加依赖与 secret 扫描；对 Mermaid/Cytoscape/Highcharts 等大依赖做按需加载和 chunk budget。

## 复评 Gate

下一次 commit 评审至少重跑：受影响测试 + 后端全量 + 前端 test/typecheck/build；同时比较新增协议/枚举、权限与生命周期变化、巨型文件变化、依赖变化、安全暴露面和 current-ref 运行证据。只有三项运行维均 `READY` 且安全门禁转绿，才允许给七维综合分或进入生产达标讨论。
