# 对话上下文与顶层意图协议 V1

## 主线

`多模态输入标准化 → 上下文语义补全 → 顶层意图路由 → Agent 内部规划`

附件可以在输入阶段解析并持久化，但长文档、完整中间结果和代码不会复制进本轮提示词。上下文协议只传递引用和必要的轻量摘要。

## 上下文语义补全

上下文模块只消除指代和省略，不选择 Agent，不判断 Design、Coding、Tool 或 Direct。

```json
{
  "ori_question": "那么五粮液呢？",
  "resolved_question": "查询五粮液昨日的开盘价。",
  "context_refs": ["turn:102"]
}
```

原问题完整且不依赖前文时，`resolved_question` 与 `ori_question` 相同，`context_refs` 为空。

大附件只显式引用：

```json
{
  "ori_question": "请总结这份文件",
  "resolved_question": "总结附件《新能源汽车行业深度研报》。",
  "context_refs": ["attachment:report_1024"]
}
```

## 顶层意图路由

顶层路由在一次 LLM 推理中同时输出两个正交维度：

```json
{
  "agent_name": "investment_analyst",
  "turn_mode": "normal_qa"
}
```

- `agent_name` 必须来自当前 Application 的 `available_agents`，不写死 Agent 枚举。
- `turn_mode` 仅允许 `normal_qa`、`system_operation`、`tool_development`。
- `system_operation` 是处理模式，不对应独立的 System Agent；系统操作由当前 Agent 上下文进入平台执行能力。
- 进行中的工具开发由系统保存所属 Agent；`tool_development` 延续该上下文时直接沿用所属 Agent，不让模型重复猜测。

### 斜杠命令边界

- 当前输入是 `/custom_tool create ...` 或 `/custom_tool edit ...` 时，由规则直接设为 `tool_development`。
- 其他斜杠命令均由规则直接设为 `system_operation`。
- 斜杠命令不调用上下文补全和顶层意图 LLM。
- 自然语言可以表达系统操作或工具调用；自然语言不能开始新的工具创建或优化。
- 已经存在工具开发上下文时，后续自然语言由顶层意图判断是在延续开发，还是回到普通问答。
- 进行中的工具上下文不能锁住新业务问题。
- `/xxxx` 是高置信度输入信号，但仍产生相同的顶层路由协议。

顶层协议不输出 `domain_hint`、`needs_reference_resolution`、`info_ready` 或 Agent 内部执行类型。迁移期间，运行时可以从新字段派生旧字段作为内部兼容别名，但模型不再负责输出旧协议。

## Agent 输入

路由决定不会替换语义数据。目标 Agent 同时接收：

```json
{
  "ori_question": "流程图再给我看一下",
  "resolved_question": "展示最近的金叉工具设计流程图。",
  "context_refs": ["custom_tool:golden_cross"],
  "agent_name": "investment_analyst",
  "turn_mode": "tool_development"
}
```

`normal_qa` 进入业务 Planner，`system_operation` 进入系统操作能力，`tool_development` 进入所属 Agent 的工具开发上下文，再由 Agent 内部判断后续执行方式。
