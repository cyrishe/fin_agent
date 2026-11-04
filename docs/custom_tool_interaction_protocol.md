# Custom Tool 交互协议

Requirement 阶段的内容组织、局部选择题和自然语言修正规则见
[自定义工具 Requirement 交互设计](./custom_tool_requirement_interaction_design_20260818.md)。

所有输入统一由以下三部分组成：

```json
{
  "text": "用户在输入框中的原始文字，可以为空",
  "interaction_response": {
    "interaction_id": "当前交互卡片",
    "action_id": "用户点击的按钮",
    "expected_revision": 1,
    "answers": []
  },
  "attachments": []
}
```

按钮是结构化输入，不是独立状态机。状态用于提供上下文；只有权限、归属、版本冲突、明确验证门禁和执行安全可以拒绝操作。

## 实际交互

当前创建主线默认不把 Requirement 和 Design 变成连续确认关卡：

- DSH-opt 结合当前会话和按阶段注入的业务 Skill 做语义编排；Codex 只在 flow 资产保存后接手实现与验证，provider 选择不进入用户协议。
- Agent 先用上下文、金融知识和可说明默认值自行收敛 Requirement。
- 只有仍会改变核心结果的问题才生成 `custom_tool.requirement_clarification`；问题为空时不生成交互卡，直接继续 Design、流程资产、实现和验证。
- Design 与流程图按 revision 保存并可查看，但不生成独立确认交互。它们是 Coding 的权威输入，不是用户必须经过的页面。
- 实现后的候选只有在对应 revision 的技术验证成功时才显示启用动作；验证缺失或失败时只显示修复与复测入口。

DSH 的 `request_user_interaction` 和 flow 保存是两个原生终止边界：前者等待用户补充，后者立即回到父进程进入 Codex。模型不能在子进程中执行 Coding；同轮模型重复提交同类资产时，系统只合并最后一份贡献，不制造多个用户确认轮次。

### 待确认选项

每个选项作为 `answers` 中的一项提交；输入框文字只承载“告诉 Fin Agent 如何处理”的自定义内容。最后的“确定”只是统一提交当前答案。

```json
{
  "interaction_id": "custom_tool.requirement_clarification",
  "action_id": "custom_tool.submit_clarification",
  "action": "submit",
  "expected_revision": 1,
  "answers": [
    {
      "question_id": "Q1",
      "question": "金额单位是什么？",
      "mode": "option",
      "value": "元",
      "label": "元"
    }
  ]
}
```

系统将结构化答案转换为第一人称的本轮输入，例如“关于金额单位，我选择元”。

### 纯动作

重新执行实现、修复并复测、启用已测试版本都允许输入框为空。按钮携带当前交互对象和版本，系统直接执行该按钮明确表达的操作。`custom_tool.confirm_design` 只为旧会话兼容保留，不属于当前创建主线。

### 按钮和文字同时提交

“告诉 Fin Agent 如何处理”只负责把输入框与当前设计或实现绑定。提交时，按钮信息和用户原始文字一起进入正常的上下文补全、顶层意图和 Agent 内部判断；按钮不能绕过语义理解，也不能把用户锁在当前工具流程。

## 路由边界

- `/custom_tool create|edit` 是明确命令，可以规则直达。
- 输入框为空的确认按钮可以直达对应操作。
- 只要存在用户文字，就走正常语义路由；当前按钮作为精确的上下文信号传入。
- 不使用“确认”“OK”“重试”等文字匹配决定阶段。
- 不使用 `状态 + 按钮` 矩阵拒绝用户输入。
- 版本冲突、权限、归属和测试门禁仍由系统校验。
