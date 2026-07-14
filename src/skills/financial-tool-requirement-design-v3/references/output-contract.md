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
- `modules`：内部职责划分及关键函数职责，不等同于多个公开工具，也不包含源码。
- `flow`：只在复杂分支或循环中使用。
- `rules`：确定性业务规则和缺失数据行为。
- `data_requirements`：字段、频率、用途、系统映射状态和替代策略。
- `exceptions`：异常或边界输入下的行为。
- `acceptance`：可以直接用于测试的业务场景与预期结果。

## existing_analysis

只在实际检查已有工具、代码或文档后设置 `analyzed=true`。每条 evidence 必须包含可定位的位置和观察结论。无法访问时保持空结构，不得推测。

## 系统职责

系统只负责保存权威设计、合并本轮变化、保留未修改字段和记录历史。结构无法解析或合并后的完整 Design 不符合基础 Schema 时才停止流程；不在协议层审查金融业务内容。

## 修订轮

修订轮使用独立 Schema，只包含：

- `status`：合并后应处于 clarification 或 review。
- `change_summary`：本轮变化的一句话说明。
- `questions`：当前仍阻塞的少量问题。
- `changes`：每项只包含字段路径和该字段更新后的完整 JSON 值；系统直接替换该字段。

用户反馈原文、上一轮完整设计、版本、时间戳和差异证据均由系统保存，不由模型输出。
