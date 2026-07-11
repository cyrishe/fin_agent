# 输出字段语义

本文件补充业务字段含义，不定义运行时、流事件或前端组件。

## 状态

- `clarification`：存在至少一个 `required=true` 的阻塞问题。
- `review`：不存在阻塞问题，设计已达到可实现、可测试程度，等待用户检查。

用户确认由外层状态机记录，不由模型输出确认状态。

## understanding

- `goal`：要解决的核心问题。
- `usage`：谁在什么场景使用。
- `expected_result`：最终业务结果，而不是页面形式。
- `confirmed_requirements`：用户明确要求或现有实现中直接观察到的事实。
- `constraints`：不能突破的业务、数据或系统边界。
- `assumptions`：为了继续设计采用的低风险假设。

## questions

- `id`：同一问题跨轮保持稳定。
- `question`：一次只询问一个决策点。
- `reason`：说明该问题会影响什么。
- `answer_type`：`single_choice | multiple_choice | number | text | boolean`。
- `required`：不回答是否会阻塞实现。
- `options`：选择题的候选答案；非选择题为空数组。
- `allow_custom`：是否允许用户在对话中给出其他答案。

## design

- `tool_name`、`display_name`、`description`：稳定标识和用户可理解的工具说明。
- `inputs`、`outputs`：公开调用接口。
- `modules`：内部职责划分，不等同于多个公开工具。
- `flow`：只在复杂分支或循环中使用。
- `rules`：确定性业务规则和缺失数据行为。
- `data_requirements`：字段、频率、用途、系统映射状态和替代策略。
- `exceptions`：异常或边界输入下的行为。
- `acceptance`：可以直接用于测试的业务场景与预期结果。

## existing_analysis

只在实际检查已有工具、代码或文档后设置 `analyzed=true`。每条 evidence 必须包含可定位的位置和观察结论。无法访问时保持空结构，不得推测。

## 后端校验

Schema 只保证结构；后端还需要校验：

- 每轮问题不超过 5 个。
- `clarification` 至少有一个必答问题。
- `review` 不得包含必答问题。
- 问题 ID、流程节点 ID 和字段名在同一设计内唯一。
- flow link 引用的节点存在。
- `verified` 数据需求必须有 `source_ref`。
- `review` 不得含有占位式核心规则。
