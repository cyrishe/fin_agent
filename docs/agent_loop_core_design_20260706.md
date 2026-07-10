# Agent Loop Core 调研与设计要点

日期：2026-07-06

## 目标

把现在金融查询内部已经出现的 check / retry / final check 能力，抽成更通用的运行时 loop 核心。它服务于金融查询、文件工具、搜索工具等所有大工具，但不改变各工具内部协议。

同时，旧工具要从 planner 可见面退出。`theme_leaders` 这类老工具不再进入 hub、候选工具、参数规划和 LLM planner 上下文。

## 外部框架调研结论

OpenAI Agents SDK 的 Runner 是清晰的单循环：调用模型，若有 final output 则结束；若有 handoff 或 tool call，则执行后继续；同时用 `max_turns` 和 guardrail 作为停止与安全边界。这个模式适合我们：loop 本身只管推进状态，不把业务判断塞进 loop。参考：<https://openai.github.io/openai-agents-python/running_agents/>、<https://openai.github.io/openai-agents-python/guardrails/>

Claude Code Agent SDK 把 agent loop 表达为“接收输入 -> 评估/响应 -> 执行工具 -> 重复直到无工具调用”，并强调权限、hooks、checkpoint、observability 和 context compaction。对我们的启发是：每轮执行要可观测，retry 前要知道上一次状态。参考：<https://code.claude.com/docs/en/agent-sdk/agent-loop>

OpenClaw 的实现更强调运行时事件和失败恢复：tool start/update/end 事件、工具结果摘要、compaction 后重试、失败最多重试有限次数。对我们的启发是：前端体验应订阅 loop 事件，而不是等最终结果。参考：<https://docs.openclaw.ai/concepts/agent-loop>

LangGraph 的 interrupt/checkpoint 模式适合人工确认或可恢复流程：节点执行可暂停，状态持久化，后续用同一个 thread 恢复。对我们的启发是：高风险动作不要靠 prompt 约束，应由 runtime checkpoint / interrupt 控制。参考：<https://docs.langchain.com/oss/python/langgraph/interrupts>

本地 `../ref_repos` 也有几个可借鉴点：

- `openai-agents-python/src/agents/run.py`：Runner 明确 `max_turns`、guardrail、session/conversation 状态。
- `OpenHands/openhands/controller/agent_controller.py` 和 `openhands/controller/stuck.py`：有 stuck detection，能截断到 loop 前的状态后恢复。
- `openclaw/src/agents/pi-embedded-runner/run.ts`：run 级别有 session lane、global lane、模型 failover、compaction、tool result truncation。
- `openclaw/docs/automation/standing-orders.md`：强调 Execute -> Verify -> Report，失败重试有限次数，不能无限 retry。

## 对我们当前系统的判断

当前最大问题不是缺少更复杂的 agent 框架，而是 loop 边界还散在各模块里：

- phase1 / phase2 / final check 有各自的反馈和重试逻辑。
- 金融查询内部已有 session 结果 schema，但应该提升为全局 session scope。
- 工具输出需要统一进入 `schema | sample_data | full_data_ref`，让后续步骤可引用。
- 前端需要看到每步的 start/check/retry/done，而不是只看到最终表格。

所以这里不建议引入 LangGraph 这类新依赖。先做一个 repo 内部轻量 runtime loop，后续如果流程复杂到需要 DAG/checkpoint 引擎，再迁移。

## Loop Core 最小设计

建议新增一个通用 loop runner，职责只做五件事：

1. 执行当前 node。
2. 把结果注册为 session variable。
3. 调用 checker 进行静态检查、结果检查或 final check。
4. 根据 check 结果决定 advance / retry / rollback / stop。
5. 发出前端事件。

核心数据结构：

```text
LoopRun
- run_id
- session_id
- user_request
- nodes[]
- variables
- max_attempts
- events[]

LoopNode
- node_id
- tool_name
- request
- depends_on[]
- status: pending | running | done | retrying | failed | skipped
- result_var
- attempts[]

LoopCheckResult
- status: ok | retry | rollback | blocked
- category: protocol_error | semantic_miss | provider_missing | runtime_error | empty_result | final_need_check
- feedback
- rollback_to_node_id
```

执行伪代码：

```text
for node in plan.nodes:
  while attempts < max_attempts:
    emit node_started
    result = execute(node, session_variables)
    var = register_session_variable(result)
    check = checker(node, result, session_variables)

    if check.status == ok:
      emit node_done
      break

    if check.status == retry:
      emit node_retry
      node.request = repair(node.request, check.feedback)
      continue

    if check.status == rollback:
      emit node_rollback
      jump_to(check.rollback_to_node_id)
      break

    emit node_blocked
    stop
```

## Retry 规则

retry 必须有上限，默认建议：

- 单节点最多 2 次 retry。
- 整个 run 最多 1 次 final check repair。
- 如果是 provider_missing，不 retry，直接标注缺能力。
- 如果是同类 protocol_error 连续出现，停止并暴露 feedback。
- 如果是非交易日导致当日行情为空，checker 应允许 empty_result 被判为 ok。

## Check 分层

建议 checker 分三层，保持简单：

1. `static_check`：请求串/参数/schema 是否合协议。
2. `result_check`：执行结果是否为空、字段是否完整、行数是否合理。
3. `final_check`：原始需求是否被最终结果满足。

这三层都返回同一个 `LoopCheckResult`，不要各自发明协议。

## Session Variable 统一

变量能力应在顶层 runtime，而不是金融查询内部单独维护。

每个工具结果统一注册：

```text
SessionVariable
- variable_id: r1 / r2 / ...
- producer: tool_name + node_id
- schema
- sample_data: 前 3 行或简短摘要
- row_count
- full_data_ref
```

LLM 上下文只注入 schema、row_count、sample_data；完整数据通过 `full_data_ref` 给 runtime 使用，不塞进 prompt。

## 前端事件

前端不需要知道工具内部实现，只需要消费标准事件：

```text
loop_started
node_started
node_result_ready
node_check_started
node_check_failed
node_retry_started
node_done
loop_final_check_started
loop_done
loop_blocked
```

动态代码触发时，事件里额外带：

```text
code_generated
code_execution_started
code_execution_done
```

这样前端能显示“这是代码计算”，并展开代码。

## 实现边界

第一步只做 runtime loop skeleton 和 session variable 接入，不做复杂 DAG 引擎：

- 不引入新依赖。
- 不做多 agent。
- 不做全量自动修复。
- 不让 LLM 自己决定无限重试。
- 不把业务规则写死在 loop core。

金融查询、文件工具、搜索工具各自保留自己的 planner 和 executor。loop core 只看统一的 node/result/check 协议。

## 本次代码收口

- `theme_leaders` 已从 `src/tools/tool_hub.json` 移除。
- `ToolArgumentPlanner` 对 disabled definition 返回 `status=disabled`。
- `AgentRuntimeLLMPlannerService` 会过滤 disabled 工具候选，并剔除 disabled tool work item。
- fallback execution plan 和 deep thinking 预览不再主动偏好 `theme_leaders`。
- 相关测试改为验证旧工具 disabled、新工具仍可规划。
