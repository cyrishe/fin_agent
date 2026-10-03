# 用户后台任务系统

当前实现把一次性、预约和周期任务放入同一条持久化执行链：

`自然语言要求 → 任务编译与授权 → Task 定义 / Run 快照 → 独立 worker → 业务工具或 Skill → 任务中心与产物`

外层负责执行和归属；股票 AutoML 是其中一个业务资产。训练目标、样本选择、模型、回测和评审仍属于 `src/quant_research/automl/`，没有加入调度数据库的业务字段。物理表名和 Python 模块名保留 `scheduled_task`，避免另建一套任务队列。

本文描述当前代码。架构取舍见 [通用任务设计](development_tasks/general_task_runtime_automl_20261003.md)，股票研究口径见 [股票 AutoML](stock_automl.md)。

## 任务定义和运行

Task 保存用户要求、Tool/Skill 执行计划、触发时间、运行预算和修订号；Run 保存某次运行采用的不可变要求/计划/预算快照。公共接口提供 `task_id`，同时保留兼容字段 `schedule_id`。

| 触发输入 | 创建行为 |
|---|---|
| 不提供 `trigger`，或 `{}` | 同一事务创建定义和立即执行的 Run |
| `{"at":"2026-10-10T09:00:00+08:00","timezone":"Asia/Shanghai"}` | 同一事务创建定义和未来 Run，到时间才能领取 |
| `{"cron":"0 9 * * 1-5","timezone":"Asia/Shanghai"}` | 保存定义，worker 到期后创建 Run |

无时区的 `at` 按指定时区解析；未指定时区时使用 `Asia/Shanghai`。cron 使用五段表达式，不包含交易日历。周期漏跑会合并，同一定义有 `pending` 或 `running` 运行时不继续堆积周期 Run；结束后合并处理一个到期槽。

修改定义只影响随后创建的运行，已经入队/运行的快照保持不变。`enabled` 控制周期定义是否继续产生运行；停止某次已产生的运行应调用取消 API。对未来一次性任务点击“立即运行”会提前现有 pending Run，不另建重复运行。对已有活动运行再次点击返回该 Run。

默认 `budget.max_runtime_seconds=3600`，接受 1 秒到 7 天。截止时间在首次领取时保存；worker 恢复沿用原截止时间，停机期间也会消耗这个时间窗口。等待首次领取的排队时间不消耗执行预算。

运行状态只有 `pending / running / completed / failed / cancelled`。训练、校准、回测等是进度说明，不是新的运行状态。“研究完成但没有模型达标”对应 `completed`，由业务结果解释未达标原因。

## 本地启动：SQLite 和独立 worker

SQLite 是同一存储实现的本地持久化方言，支持进程重启和同机多个 worker。它不依赖 MySQL 任务表，适合单机开发、测试及受控使用；不是多机共享文件系统方案。

在仓库根目录安装原项目依赖；需要执行 AutoML 的 Python 环境还需安装 `requirements-automl.txt`。Web 服务可以不安装机器学习依赖，worker 必须安装它负责执行的业务模块依赖。

给 Web 和 worker 设置相同的绝对路径：

```bash
export TASK_SQLITE_PATH="$PWD/outputs/user_tasks/tasks.sqlite"
export TASK_ARTIFACT_ROOT="$PWD/outputs/user_tasks/artifacts"
FIN_AGENT_PORT=22053 python -m src.web.flask_app
```

在另一个终端使用相同的环境变量启动 worker：

```bash
export TASK_SQLITE_PATH="$PWD/outputs/user_tasks/tasks.sqlite"
export TASK_ARTIFACT_ROOT="$PWD/outputs/user_tasks/artifacts"
PYTHONPATH=. python scripts/run_scheduled_task_worker.py --lease-seconds 60
```

SQLite 文件和表由该本地存储初始化。Web 只编译和入队；没有 worker 时，任务保持 `pending`，不会在请求线程中训练。每个 worker 同时执行一个 Run；增加进程可以执行不同任务。本机 Web 和 worker 必须都能读取产物根目录。

单次领取诊断：

```bash
PYTHONPATH=. python scripts/run_scheduled_task_worker.py --once --lease-seconds 60
```

worker 参数：`--poll-seconds` 默认 5 秒，`--lease-seconds` 默认 3600 秒。续租周期不超过 10 秒；实际取消检查在监督循环中进行，不需要等待一个完整租约。

## MySQL 初始化与部署 Gate

未设置 `TASK_SQLITE_PATH` 时，Web 和 worker 使用 `SYSTEM_DB_URL` 对应的现有系统库。任务表继续使用：

- `aiia_scheduled_task`：定义、触发、预算、幂等请求和修订。
- `aiia_scheduled_task_run`：运行快照、租约 token、截止时间、取消请求、检查点、进度和结果。

本次已更新建表 SQL 和已有表的增量迁移代码，但**没有对真实 MySQL 执行迁移或完成 MySQL 联调**。SQLite 的测试通过不等同于 MySQL 已就绪；MySQL 发布状态为 `NOT_READY`，需要绑定目标环境、当前提交和真实 workload 的证据。

只读检查命令：

```bash
PYTHONPATH=. python scripts/manage_scheduled_task_schema.py
```

生产数据库操作遵守仓库人工 Gate。由负责人确认目标库、备份、表规模/锁影响，并先在隔离 MySQL 验证后，停止旧 worker，再执行：

```bash
PYTHONPATH=. python scripts/manage_scheduled_task_schema.py --apply
```

脚本会创建缺少的表，给已有表补上运行预算、预约时间、来源引用、请求指纹、检查点/进度、租约 token、取消和 deadline 等字段，将 cron 改为可空，并添加手动 Run 请求唯一索引。MySQL DDL 可能自动提交，不能把脚本中的事务当成整次迁移可回滚的保证。

新旧 worker **不能混跑**：旧 worker 不理解一次性任务、取消和新的租约写入条件。切换前处理旧活动运行，迁移完成后启用新 worker 和入口。回滚应先停止新入口/worker、保留新任务记录和产物并制定兼容方案；管理脚本不删除数据、不回退列。不要直接回滚到无法处理新任务的旧 worker。

## 执行、取消与恢复

创建时按 owner 授权资产，模型可见目录也按 owner 筛选；开始执行及每个新步骤前重新授权。Skill 内部每次真实 Tool 调用也重新授权，可信身份通过绑定的上下文传递，不采信普通参数中的 `_runtime`。任务提交/查询/取消工具不能作为后台步骤递归调用。

默认执行器为每个 Tool/Skill 使用 `spawn` 子进程，POSIX 上建立独立进程组。父 worker：

1. 领取唯一 lease token，保存首次截止时间，后台续租。
2. 接收并持久化业务进度/检查点，每个步骤完成后提交步骤输出。
3. 轮询取消、截止时间、运行状态和 token；停止条件发生时终止子进程组。
4. 仅在 token 仍有效、租约未过期时保存检查点或发布终态。

子进程用 parent-PID watchdog 处理 worker 突然退出。进程组在工具返回后也会清理，避免派生进程在任务结束后继续运行。子进程消息上限为 8 MiB，大型数据通过产物保存；BLAS 线程数设为 1。此机制限制执行时间并管理进程生命周期，**不是任意代码的安全沙箱，也没有通用内存/GPU配额**。远端 HTTP 服务中已经发出的计算或副作用不能靠终止本地进程撤回。

取消 pending Run 会直接标记 `cancelled`。运行中的取消先持久化请求，worker 停止进程后写入终态，保留已经保存的证据。`SIGINT/SIGTERM` 停止当前子进程并释放租约，其他 worker 可以立即恢复；进程崩溃则等待租约过期。

恢复使用同一个 Run 的快照，只跳过已经持久化为 completed 的步骤。正在执行但尚未提交完成的步骤可能再次执行，所以整体是**至少一次执行语义**；外部写操作需要业务工具自身的幂等设计。

每次领取都写入 `run_id / step_id / lease_token` 独立目录。恢复先将上次检查点目录复制到新目录，并重写检查点中的路径。过期进程即使尚未退出，也不能修改新尝试的目录或发布结果。普通失败/用户取消是终态；“再次运行”创建新的 Run，不自动复用另一个 Run 的内部状态。自动恢复针对被中断、尚未完成的同一个 Run。

业务工具明确返回 `ok:false` 会停止依赖步骤并标记执行失败；`ok:true` 内的未达标研究结论仍可以正常交付。

## 业务执行上下文与 AutoML

调度器注入少量通用上下文：

- `task_run_id / task_id / task_step_id / owner_user_id`：可信归属与执行标识。
- `scheduled_for`：该 Run 的冻结时间锚点。
- `task_output_dir`：当前尝试可写的产物目录。
- `task_progress(dict)`：更新可读进度。
- `task_checkpoint` 与 `task_save_checkpoint(dict)`：业务自行定义的恢复数据。
- `task_check_cancel()`：合作式停止检查；不合作的长步骤由父进程终止。

`stock_automl_research` 仅接受来自后台任务的上下文。自然语言由领域规划器落实为 `ResearchSpec`，显式结构化配置可以绕过这次语义编译。首次运行固定研究设计、行情快照、公司/时间划分和实验候选；内部已完成实验可恢复。`source=demo` 只验证流程，`source=kingdomai` 读取实际行情。评审状态和数值报告分开保存，LLM 评审失败不需要重训。

通用调度器不解析行业、市值、收益标签或样本规则，也不把目标胜率当成执行成功条件。AutoML 的报告分别说明预测指标、信号、组合表现、公司与时间留出结果及局限。模型二进制、原始行情、逐行预测默认不通过任务下载接口发布。

## API 和 Agent 工具

所有 owner 由服务端 session 或可信系统调用上下文提供。写入仍遵循应用现有登录权限。JSON 请求最大 128 KiB，任务说明最多 4000 字符，幂等键最多 128 字符；浏览器写请求校验 Origin。响应使用 `Cache-Control: no-store, private`。

| 接口 | 用途 |
|---|---|
| `POST /api/task-definitions/preview` | 编译预览，不入队 |
| `POST /api/task-definitions` | 创建定义及必要的初始 Run；返回 `{ok, task}` |
| `GET /api/task-definitions` | 当前用户最近更新的定义，最多 200 条 |
| `GET /api/task-definitions/{task_id}` | 定义详情 |
| `PATCH /api/task-definitions/{task_id}` | 修订后续计划、启停周期产生运行 |
| `POST /api/task-definitions/{task_id}/run` | 手动运行，接受 `Idempotency-Key` |
| `GET /api/task-definitions/{task_id}/runs` | 当前定义运行历史 |
| `GET /api/task-runs` | 当前用户运行历史 |
| `GET /api/task-runs/{run_id}` | 状态、进度、结果和产物 |
| `POST /api/task-runs/{run_id}/cancel` | 请求取消 |
| `GET /api/task-runs/{run_id}/artifacts/{artifact_id}` | 归属校验后的产物下载 |

历史列表默认 50 条，`limit` 上限 200。定义列表首期没有游标分页。创建请求通过 `Idempotency-Key` 去重；同一键改变输入返回 HTTP 409。编译错误返回可读错误及 code；不存在或不属于当前用户的资源返回 404。

`/api/schedules*`、`/api/schedule-runs/{run_id}` 保持兼容并使用同一服务。旧 `/api/tasks/{job_id}` 仍属于旧异步 Skill API，没有复用为新对象或静默迁移其历史。

Agent 工具为 `task_submit / task_get / task_list / task_cancel`。提交返回任务标识和任务中心链接，不等待训练结束；状态查询只读保存结果。系统代表用户操作时沿用该用户身份，不允许通过输入 owner 获得更高权限。周期定义启停目前由任务中心/API 完成。

结构化提交示例：

```json
{
  "instruction": "立即验证股票机器学习研究流程",
  "draft": {
    "trigger": {},
    "budget": {"max_runtime_seconds": 300},
    "execution_plan": {
      "steps": [{
        "step_id": "research",
        "type": "tool",
        "target_ref": {"kind": "tool", "name": "stock_automl_research"},
        "inputs": {
          "requirement_brief": "使用合成数据验证训练和报告，实际投资效果不作判断",
          "source": "demo",
          "llm_review": false,
          "spec": {
            "start": "2023-01-02", "end": "2023-12-29", "max_symbols": 6,
            "max_trials": 4, "rounds": 1, "folds": 2,
            "models": ["linear"], "tasks": ["classification", "regression"],
            "horizons": [1], "samplers": ["all"], "feature_sets": ["technical"]
          }
        },
        "depends_on": []
      }]
    }
  }
}
```

多步执行通过 `depends_on` 与 `{"$from":"step_1.result.some.path"}` 引用前一步结果；引用对象必须是显式依赖。最多 12 步，按依赖顺序执行，首期不提供外层动态分支、循环、审批状态或自动失败重试树。自主探索留在有预算的业务 Tool/Skill 中。

## 任务中心和结果权限

工作台左侧“任务中心”统一展示一次性、预约和周期定义；提供自然语言预览、提交、任务查找、运行历史、进度、取消、周期启停和结果下载。对话工具回执链接到同一页面。页面轮询持久化状态，离开页面不会中断后台任务。

Run 接口不返回 lease token、业务恢复检查点或内部文件路径。下载必须同时满足当前 owner、Run 结果中真实登记的 artifact、以及解析后的路径仍位于该 Run 的产物根目录；知道路径或引用本身不构成授权。文件读取/生成工具在任务上下文内使用当前 Run 的隔离存储，不接受任意本机路径。报告和模型的保留/清理策略仍需部署方制定，当前没有自动删除已完成产物的守护进程。

## 验证入口和边界

针对性回归：

```bash
python -m pytest tests/test_scheduled_tasks.py tests/test_user_task_runtime.py tests/test_user_task_tools.py -q
```

真实 API → SQLite → spawn worker → 产物下载链路：

```bash
python scripts/eval_user_task_runtime.py
python scripts/eval_user_task_runtime.py --llm
python scripts/eval_user_task_runtime.py --llm --kingdomai
```

第一条包含非 ML 的文件写入/读取两步任务和合成行情 AutoML；后两条分别使用真实配置的 LLM 和 KingdomAI 只读行情。输出保存在 `outputs/`，不会自动上传原始数据。聊天工具链路另见 `scripts/eval_user_task_chat.py`；模拟导航身份的本地评估 viewer 仅用于回环地址的 UI 验证，不是生产登录方案。

当前已有本地 SQLite 的并发、幂等、租约失效、步骤恢复、子进程取消/截止时间、检查点目录隔离、权限和产物下载回归。MySQL、生产多机部署、长稳压测和生产恢复演练尚不能据此宣称通过；具体运行证据以本次评测报告记录的环境、配置和代码版本为准。
