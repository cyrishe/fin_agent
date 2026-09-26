# 服务端定时任务

该模块把用户的自然语言要求编译成一份可确认的定时任务，并在用户 scope 内持久化、排队和执行。它遵循：

`自然语言要求（SOFT） → 定时任务/执行计划（HARD） → Tool/Skill 的场景结果（SOFT）`

首期只固化真正需要寻址和执行的字段：自然语言任务说明、五段 cron、IANA 时区、Tool/Skill 目标、输入与步骤依赖。自然语言只在创建或修订时编译一次；每次运行直接执行已确认的计划，不重新猜测用户意图。

## 初始化

先检查 `SYSTEM_DB_URL` 指向的系统库是否就绪（只读）：

```bash
PYTHONPATH=. python scripts/manage_scheduled_task_schema.py
```

确认目标确实是当前环境的系统库后，可显式执行增量建表并再次校验：

```bash
PYTHONPATH=. python scripts/manage_scheduled_task_schema.py --apply
```

该命令按顺序执行：

- `docs/sql/create_aiia_scheduled_task.sql`
- `docs/sql/create_aiia_scheduled_task_run.sql`

Web 服务仍按项目原方式启动。调度执行必须另外启动独立 worker：

```bash
PYTHONPATH=. python scripts/run_scheduled_task_worker.py
```

Web 进程只负责编译、确认和入队；如果没有独立 worker，手动运行和到期运行会保持 `pending`，不会在 Web 请求进程内执行。

单轮诊断可用：

```bash
PYTHONPATH=. python scripts/run_scheduled_task_worker.py --once
```

worker 会先将到期任务物化为不可变运行快照，再领取一段有期限的租约。进程异常退出后，其他 worker 可以领取过期租约。漏过多个时间点时只合并为一次运行，并从当前时间计算下一次触发，避免服务恢复后瞬间补跑大量旧任务。

运行期间 worker 会在后台续租，因此单个耗时 Tool/Skill 不会仅因超过一次轮询周期而被重复领取；收到 `SIGINT` 或 `SIGTERM` 后会停止领取下一条任务并正常退出。

这两个迁移只新增独立表。若需要回滚，应先停止 worker、确认运行记录不再需要，再按依赖顺序手工删除 `aiia_scheduled_task_run`、`aiia_scheduled_task`；管理脚本不会自动执行破坏性回滚。

## API

所有 owner 都由服务端 session 解析，任何请求体中的 owner 字段都会被忽略。

写接口要求 `application/json`，浏览器跨站来源会被拒绝；自然语言说明最多 4000 字符。

- `POST /api/schedules/preview`：自然语言编译与预览，不写数据库。
- `POST /api/schedules`：创建任务；支持 `Idempotency-Key` 请求头。
- `GET /api/schedules`：列出当前用户任务。
- `GET /api/schedules/{schedule_id}`：读取任务。
- `PATCH /api/schedules/{schedule_id}`：用新的 `instruction`/`draft` 修订，或用 `enabled` 启停。
- `POST /api/schedules/{schedule_id}/run`：排队一次手动运行。
- `GET /api/schedules/{schedule_id}/runs`：查看该任务的运行历史。
- `GET /api/schedule-runs/{run_id}`：查看一次运行、步骤结果或失败证据。

自然语言预览示例：

```json
{
  "instruction": "每个工作日上午九点查询贵州茅台行情，然后用 report_skill 生成一句摘要"
}
```

需要确定性集成或测试时，也可以在相同接口提交 `draft`。其中 `$from` 只允许引用显式依赖步骤的结果：

```json
{
  "instruction": "每个工作日上午九点查询并总结",
  "draft": {
    "trigger": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai"},
    "execution_plan": {
      "steps": [
        {
          "step_id": "quote",
          "type": "tool",
          "target_ref": {"kind": "tool", "name": "stock_realtime_quote"},
          "inputs": {"code": "600519"},
          "depends_on": []
        },
        {
          "step_id": "summary",
          "type": "skill",
          "target_ref": {"kind": "skill", "name": "report_skill"},
          "inputs": {"quote": {"$from": "quote.result"}},
          "depends_on": ["quote"]
        }
      ]
    }
  }
}
```

创建和每次运行前都会重新检查资产对当前 owner 是否可用。任务定义、运行快照、租约、步骤输出和错误分别持久化，正式 Tool/Skill 结果不混入 scheduler 的调试协议。

## 前端路径

在工作台左侧进入“定时任务”：

1. 用自然语言描述时间、Tool/Skill 和先后关系。
2. 查看 cron、时区、下次运行与步骤预览。
3. 明确确认后创建；预览本身不写数据库。
4. 在任务详情中启停、立即运行并查看运行证据。

## 当前边界

- 支持五段 cron，不支持秒级 cron 和自由格式日历规则。
- 最多 12 个 Tool/Skill 步骤；支持依赖排序，不提供分支、循环和重试树。
- worker 采用租约恢复，交付语义为至少一次；具有外部副作用的 Tool/Skill 自身仍需使用幂等键。
- 首期不内置通知、交易日、回测或投资业务状态。未来回测模块若发布为普通可执行资产，调度器只通过 `target_ref` 调用，不依赖其内部表和状态机。
