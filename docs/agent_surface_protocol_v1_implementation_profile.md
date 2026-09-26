# Agent Surface Protocol v1 - Conversation Core Profile

状态：首期实现约束。它收窄 `agent_surface.v1` 的落地范围，不改变完整协议的长期能力。

## 1. 首期目标

首期只完成一个闭环：金融问答和自定义工具任务都在同一条对话流水中完成，业务结果以少量稳定语义块增强展示，用户可以继续输入、确认或要求修改。

这一阶段不以覆盖所有图表和复杂编辑能力为目标。K 线、大表格、流程图、脑图、代码查看器等继续保留在完整协议中，但不是统一对话主线落地的前置条件。

## 2. 谁决定什么

模型可以输出：

- 自然语言正文和业务摘要；
- 当前产物的业务内容、版本语义和变更说明；
- 计划、验证结果和需要用户决定的事项；
- 交互意图，例如确认、修改、补充输入或重试。

业务 Skill 不直接输出上述 UI 块。它先返回自身领域 Schema，随后由受信的 Surface Compiler 映射为这些语义块。这样 Skill 负责业务正确性，Surface 协议负责跨前端的一致展示。

模型不能输出：

- HTML、CSS、组件树、图标或像素级布局；
- 可直接执行的命令、任意 URL 脚本或前端事件处理代码；
- `action_id`、`resume_token`、权限范围和审批结果；
- Run 状态、序号、持久化 ID、数据来源和审计字段的最终值。

系统负责校验模型草稿并补齐稳定 ID、状态、安全 action、来源和恢复信息。前端只渲染系统编译后的协议对象。

## 3. 首期语义块

首期实现以下五类：

| kind | 首期用途 | 默认渲染 |
|---|---|---|
| `narrative` | 回答、解释、阶段小结 | 普通文本或安全 Markdown |
| `artifact` | 工具规格、因子规格等可版本化产物 | 摘要、少量关键字段、折叠详情 |
| `workflow` | 计划、运行、验证和修复进度 | 简单状态列表 |
| `assessment` | 验证结论、问题和证据摘要 | 结论加问题列表 |
| `interaction` | 确认、修改、补充输入和重试 | 提示加有限操作按钮 |

`data` 和 `resource` 仍是协议核心类型，但首期允许统一降级为结构化列表或资源链接，不要求专用可视化。

## 3.1 金融工具设计的三层合同

三层状态和对象不能混用：

| 层 | 合同 | 负责内容 |
|---|---|---|
| Design Skill | `financial-tool-development/schema.json` | `clarification/review`、需求理解、问题、完整设计快照、现有实现分析 |
| 业务工作流 | 后台状态机 | 收集需求、等待设计确认、实现、验证、等待提交、完成或失败 |
| Agent Surface | `agent_surface.v1` | Narrative、Artifact、Workflow、Assessment、Interaction 及流式更新 |

固定映射：

| Design 结果 | Artifact | Workflow | Interaction |
|---|---|---|---|
| `clarification` | `draft`，保存当前已知设计 | `waiting_user` | `submission_mode=conversation`，fields 来自 required questions |
| `review` | `reviewable`，内容为完整工具规格 | `waiting_user` | `submission_mode=action`，确认 action 绑定 Artifact revision |
| 用户自然语言修改 | 生成新 Design 结果和新 revision | 重新进入 design | 旧确认 action 失效 |
| 用户确认 | Artifact 变为 `approved` | 进入 implementation | 不再次调用 Design LLM |

Artifact 的 `content_schema_version` 固定为 `finance.tool_spec.v1`；首期其内容与 Design Skill 输出中的 `design` 对象一致。`understanding` 用于 Narrative 和评审摘要，`questions` 用于 Interaction，二者不重复塞入 Artifact。

Design Skill 的 `clarification/review` 不是运行状态，Artifact 的 `draft/reviewable/approved` 也不是 Coding 状态。后台必须显式转换，不能让前端从文案推断。

## 4. 首期交互规则

### 确认

确认按钮提交结构化响应：

```json
{
  "interaction_id": "custom_tool.design_review",
  "action_id": "custom_tool.confirm_design",
  "action": "accept",
  "expected_revision": 3
}
```

确认块使用 `submission_mode=action`，并绑定具体工具规格 Artifact 的 `subject_ref + subject_revision`。前端不能把模型输出的 `command` 直接执行或回填。后端根据当前 thread、Run 状态、Artifact revision 和受信 action 注册表决定实际状态迁移，并在同一条对话中追加一条等价的用户决定消息。

### 澄清

澄清块使用 `submission_mode=conversation`。协议中的 `fields` 可以提供问题、选项和推荐项，但前端控件只负责辅助组织答案，最终内容仍进入统一输入框并作为普通用户消息提交。没有适合控件的问题直接降级为文字问题。

### 修改

“修改”默认是客户端行为：聚焦对话输入框，让用户直接说明修改点。它不创建独立编辑器，也不自动提交空的修改请求。

只有少量高价值、强约束字段适合提供控件，例如市场、基准、时间窗口和复权方式。控件提交后仍转换为结构化 interaction response，由后端校验；自由业务逻辑继续使用自然语言修改。

## 5. 渲染降级

每个首期块必须有不依赖图形库的降级方案：

- `artifact` 降级为标题、摘要和字段列表；
- `workflow` 降级为带状态文本的有序列表；
- `assessment` 降级为结论和问题列表；
- `interaction` 降级为问题文本，用户可在输入框中回答；
- 未识别的 block 显示安全摘要，不显示空白区域，也不阻断整条消息。

图标只表达辅助状态，不能成为理解内容或触发操作的唯一方式。

## 6. 首期验收标准

1. 普通问答和工具设计共享同一 thread、消息列表和输入框。
2. 工具规格无需表格、流程图或代码视图也能被理解和确认。
3. 确认操作不依赖模型提供命令，重复提交可被后端拒绝或去重。
4. 修改操作回到普通对话输入，用户可以用自然语言继续。
5. 任一增强块渲染失败时，正文和后续对话仍可使用。
6. 流式增量只更新目标 block，不重排已经提交的历史消息。

## 7. 界面文案

- 只保留业务内容、必要状态和可执行动作。
- 不在界面中解释“这是对话”“这里可以恢复”“这是增强块”等产品机制。
- 同一信息只出现一次；完成态不重复展示 Agent 名称、完成说明和完成徽标。
- 空间和层级优先于旁白。没有用户决策价值的说明直接删除。
