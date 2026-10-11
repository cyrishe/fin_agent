# DSH 与 Fin Agent 联合复核及发布（2026-09-10）

## 复核范围

复核 Fin Agent `516cbf0`（保留追问原意、通用数据入口复用金融会话）、`864fd00`（入口模型固定为 0731）及另一会话的 `9241143`（效率优化），配套 DSH `0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4`。

检查了原始问题与指代参考的传递、通用数据入口执行分派、目录授权时机、Skill 正文精确去重、成功查询完成事实的前置投递、失败补救和预算收尾、原生历史选择及 token meter。未发现本次改动的阻断性代码问题；没有新增业务分支或修改 Skill 金融方法。

## 验证

- 本机 Python 3.12：相关测试 152 条通过（129 + 23）；服务器 Python 3.10：相同测试 152 条通过。
- Fin Node 执行策略：64 条通过。
- DSH Session、Agent loop、token meter 与现有 DeepSeek translator：22 个文件、477 条测试通过。
- DSH Host TypeScript 编译通过。
- 本机与服务器均完成实际 Python SDK + sdk-minimal 子进程三轮固定回放：6 次无费用模型响应；独立问题隐藏历史、追问恢复历史、原生日志记录均通过。
- 这是针对改动的回归，不代表全仓测试、全仓 lint、并发长稳或金融正确率评测通过。

## 公网真实模型回归

在新 Web worker 上通过访客 chat API 运行 4 组、12 轮，全部正常完成、无执行错误；每组沿用同一个原生 Session，后两轮 `resumed=true`。前三组首问按编号列出茅台、五粮液最新研报，随后问“第二个怎么样”“和第一个比呢”；人工阅读确认回答分别分析五粮液、对比两家公司，并有真实研报查询。

| 入口 | thread | 三轮耗时（秒） | 三轮主 Agent 模型步骤 |
|---|---|---|---|
| 自动路由 | 4367 | 21.97 / 32.59 / 30.66 | 3 / 4 / 5 |
| 首轮指定研报 Skill | 4369 | 13.74 / 21.45 / 19.51 | 5 / 4 / 3 |
| 每轮指定 finance_data_query | 4370 | 11.40 / 18.26 / 20.46 | 3 / 3 / 4 |
| 独立话题后返回 | 4368 | 21.86 / 12.12 / 41.70 | 3 / 3 / 4 |

第四组第二轮查询宁德时代代码和上市日期，第三轮问“回到第一问，第二家公司怎么样”。第二轮语义引用为空，原生日志 `request/history={fromTurn:2}`；第三轮引用 `turn:1:assistant`，原生日志 `request/history={}`，正确回到五粮液并读取研报。三轮 `turn/end` 均为 `completed`。

请求摘要见 [live_summary.json](evidence/dsh_release_review_20260910/live_summary.json)。完整本机过程位于 `outputs/dsh_release_review_20260910/live/`，只包含本次测试创建的会话。12 轮已上报统计口径合计 552,962 tokens（包含上报缓存量），按用户约定 2 元/百万估算约 1.11 元；非供应商账单。当前网页访客测试会按普通对话计数，该入口尚无测试标记，不能宣称这些请求进入了单独测试栏。

单次样本的取证范围和模型选择会变化；本次没有完成固定 workload 的重复 A/B，不能据此宣称统一提速或固定节省比例。

## 发布

Fin Agent 功能提交同步至 GitHub 与 Codeup，服务器代码更新到 `b62e4ce`。DSH 通过 Git bundle 快进到 `0433ae6`；可审查补丁保存在 `deploy/dsh/`，未向 DeepSeek 上游仓库直接推送。

本机与服务器既有的 translator 未提交源码 diff SHA-256 一致：`5139613e00c2f207056bac6ec746f5f7e4553232dd3383735c5c8a31405015ca`。本次未覆盖这些修改，测试环境并非纯净 DSH commit。

Web 通过 Gunicorn 原生 SIGHUP 平滑重载：主进程 `3214443`，新 worker `2901328`。网页真实请求使用 `deepseek-v4-flash-0731`。

**REST/MCP 服务尚未重启**：PID `3617358` 保持 active；`systemctl --no-ask-password restart fin-agent-finance-api.service` 返回 `Interactive authentication required`。代码已同步，但不能据此宣称运行进程加载了全部更新。需管理员执行：

```sh
sudo systemctl restart fin-agent-finance-api.service
```

## 保留的限制

当前 SDK create 入口的跨进程恢复缺口仍存在。本次证明的是连续 Session 内的上下文恢复和原生日志重建，不承诺进程重启后保留完整会话。新 `request/history` 日志不能直接交给旧 DSH 读取，回退 Fin 策略时应保留新 DSH。

公网样本用于验证路由、取数、对象继承和追问流程，未逐条校验研报金融结论。仍可见模型将分红与回购合并回报称作“股息率”等表达问题，不能将流程通过等同于业务质量全面通过。
