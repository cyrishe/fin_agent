# 金融工具设计交互原型说明

日期：2026-07-11

## 现有原型判断

现有三个 HTML 原型已经验证了 render block、SSE 逐步输出和场景化结果展示，但仍然以“如何展示一条复杂 assistant 消息”为核心。对金融工具设计任务，这个边界偏窄：

- 过程区和回答区固定分割，设计、代码、测试都被压成消息内容。
- 工具设计稿没有成为可持续编辑、可比较、可确认的独立 artifact。
- 对话、设计、数据映射、编码和测试之间缺少稳定的任务位置感。
- 金融数据的 subject/dataview、字段、粒度、时间边界和 freshness 没有成为一等 UI 对象。
- stream 体现了“系统正在做事”，但用户不容易判断当前产物能否检查、修改或批准。
- 静态风格方案主要是配色和密度差异，信息架构差异不足。

## 五套原型

### 1. 对话画布

默认工作入口。对话保持 ChatGPT 类产品的低门槛体验，右侧长期存在当前 artifact。

- 适合需求理解、追问、设计确认和小幅修改。
- 设计契约按契约、输入输出、逻辑、数据分层。
- 用户确认的是 artifact，而不是某一段聊天文本。
- 移动端将 artifact 变成覆盖式抽屉，聊天仍是主界面。

### 2. Agent IDE

编码阶段的专业工作区，参考 Codex 类 agent coding 产品的信息组织。

- 文件、代码、Agent 过程和测试终端同时可见。
- Agent event 显示为工作进程，不占据代码主区域。
- 金融 API context 和工具调用作为可展开的 tool call 证据。
- 对话输入用于修改代码，而不是替代代码和测试界面。

### 3. 金融数据编排台

把金融数据协议作为主要设计对象。

- 一个节点对应一个 subject/dataview 或确定性计算步骤。
- 节点检查字段、粒度、窗口、as-of 时间、freshness 和缺失策略。
- 同时提供协议请求、数据形态图和样例行。
- 适合数据查询工具、因子设计、筛选器和跨数据源工具。

### 4. 人工审核门

面向金融规则、权限边界和高影响设计变更。

- 以差异、决策矩阵和确认清单替代长篇自然语言确认。
- 只有关键条件全部确认后才能进入 coding。
- 明确区分规则判断、投资建议和自动交易动作。
- 适合发布、修改核心规则、扩大数据权限和接入副作用工具。

### 5. 阶段自适应工作区

同一任务根据当前阶段改变主内容，避免用户长期面对多栏复杂界面。

- 顶部阶段条保持任务位置感。
- 中央只展示当前最重要 artifact。
- Agent 进程、输入数据和运行边界作为上下文侧栏。
- 测试阶段直接展示业务结论、规则得分、图形证据和原始输出。

## 推荐收敛方向

最终产品不应把五套方案作为五种皮肤或永久模式暴露给用户。建议采用一个自适应壳层：

```text
对话画布（默认入口）
  -> 设计 artifact（右侧或移动端抽屉）
  -> 数据编排工作区（需要检查金融数据契约时）
  -> Agent IDE（进入 coding 时）
  -> 自适应测试结果（执行 test 时）
  -> 审核门（设计确认、权限扩大、发布前）
```

用户始终处于同一个 thread/task，不需要理解“我现在应该打开哪个产品”。系统根据状态自动选择主工作区，用户仍可从 artifact tabs 手动回看其他产物。

## 与当前代码的映射

| 当前能力 | 建议 UI 映射 |
| --- | --- |
| `custom_tool_state.status` | 顶部阶段条和主工作区选择 |
| `codex/agent_update` | Agent 进程侧栏 |
| `harness/tool_call` / `harness/tool_result` | 可展开的工具调用证据 |
| `model/draft_design` | 可持续更新的设计 artifact |
| `model/final.design` | 输入、输出、逻辑和数据契约 |
| `model/code_plan` | IDE outline 和实现计划 |
| `model/artifact` | 文件树、代码、schema 和测试文件 |
| `model/test_result` | 业务测试矩阵和原始日志 |
| `final.status=need_design_fix` | 从 coding/test 回到审核门的显式反馈 |
| `finance_data/catalog` | 数据节点 inspector 与字段选择 |
| `SessionVariable` | 数据预览、schema、row count、full-data reference |

## 建议补充的前端状态

当前 stream block 适合增量渲染，但工作区还需要稳定的 artifact identity：

```text
ToolDesignTask
- task_id
- thread_id
- active_stage
- active_artifact_id
- stages[]
- artifacts[]
- approvals[]
- run_events[]

Artifact
- artifact_id
- type: design | schema | data_flow | code | test | output
- revision
- status: draft | needs_review | confirmed | superseded
- source_event_ids[]
- content
```

这样对话消息、SSE 事件和可编辑产物不会混成同一份状态；刷新页面后也能恢复当前工作区，而不只恢复一段聊天记录。

## 首轮落地建议

1. 先将对话页升级为“对话 + artifact pane”，复用现有 custom tool stream。
2. 将 `model/final.design` 编译成稳定的 Tool Design Contract，不直接按 markdown 展示。
3. 将 coding 事件映射为文件、测试和进程三类 artifact。
4. 为 `awaiting_design_confirmation`、`need_design_fix`、`draft_ready` 增加明确 approval gate。
5. 数据编排和完整 IDE 可以在 artifact pane 基础上逐步扩展，不需要一次重写现有会话页。
