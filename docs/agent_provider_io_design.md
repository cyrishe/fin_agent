# 慢 Agent 通用输入输出设计

日期：2026-07-20
状态：已实现（`agent.run.v1`）

## 1. 目标与当前边界

本设计统一 Codex 与 Claude 在 Fin Agent 中的业务输入、业务结果和用户交互，但不强行统一两家 SDK 的原始事件。

当前主线约束：

- 编排层分别通过 `CUSTOM_TOOL_DESIGN_PROVIDER` 和 `CUSTOM_TOOL_CODING_PROVIDER` 选择执行适配器；当前默认由 Claude + DeepSeek V4 Flash 承担需求、设计和测试规划，由 Codex `mid` 承担代码实现；
- 不增加自动 failover；provider 失败按真实错误返回，避免一次业务执行被隐式重复；
- 不用 provider 选择扩张业务状态或 Skill Schema；
- provider、模型、thinking、tool call 等实现细节对用户和上层业务透明。

稳定边界只有三层：

```text
用户 SOFT 输入
  -> 系统持有的最小执行上下文 HARD
  -> Codex / Claude adapter
  -> 现有业务 Skill Schema HARD
  -> Surface Block SOFT 输出
```

## 2. 输入侧：保持一个用户入口

### 2.1 用户输入协议

继续沿用现有对话入口，不新增 provider 专用字段：

```json
{
  "text": "用户原始文字，可以为空",
  "interaction_response": {
    "interaction_id": "当前交互卡片",
    "action_id": "用户选择的受信动作",
    "expected_revision": 1,
    "answers": []
  },
  "attachments": []
}
```

三者至少有一个有效值。`interaction_response` 只承载稳定动作和结构化答案；只有确认设计、启用实现这类会提交指定版本的动作需要 `expected_revision`。业务补充、修改意见和个性化语义仍由 `text` 承载。附件在进入 Agent 前先解析为受控引用和必要摘要，不把上传实现细节交给模型判断。

用户输入中不允许出现以下字段：

- `provider`、模型名和推理强度；
- Skill 路径和 Output Schema 路径；
- MCP server、credential 或工具白名单；
- run 状态、owner、revision 的最终判定值。

这些都属于系统执行策略或系统事实，不应让用户知道当前由 Codex 还是 Claude 处理。

### 2.2 系统执行输入

系统在调用 provider adapter 前，内部形成一个最小执行对象。它是编排层输入，不是新的业务协议，也不原样发送给模型：

```json
{
  "request": {
    "text": "已合并本轮文字和结构化回答后的自然语言请求",
    "attachments": []
  },
  "task": {
    "stage": "design",
    "skill_ref": "financial-tool-development",
    "context": {},
    "output_schema_ref": "schema.json"
  },
  "runtime": {
    "complexity": "high",
    "capabilities": {
      "web_search": "disabled",
      "mcp_servers": {}
    }
  },
  "identity": {
    "run_id": "system-owned",
    "thread_id": "system-owned",
    "turn_id": "system-owned",
    "owner_id": "system-owned"
  }
}
```

字段职责如下：

| 部分 | 性质 | 规则 |
|---|---|---|
| `request` | SOFT | 保存本轮用户真实意图；结构化答案转换为可读语义后与原文一起进入请求 |
| `task` | HARD | 只指定阶段、Skill、Context 和 Schema；均由系统选择和解析 |
| `runtime` | HARD 策略 | 只保存四级复杂度和独立 capability policy；可整体省略并使用部署默认值 |
| `identity` | HARD 事实 | 只用于归属、版本、持久化和 trace；除 Skill 明确需要的身份摘要外，不进入 prompt |

上层业务只依赖这一语义，不依赖 provider。现有 `run_skill(skill_path, output_schema_path, user_request, context, session_id, stage)` 已能承接其主要内容，后续实现时只需在调用前完成适配，不需要改变当前业务调用路径。

### 2.3 输入处理顺序

1. 校验归属、权限、revision 和附件引用。
2. 将按钮答案转换为第一人称语义，与用户原文合并；不使用关键词猜测动作。
3. 由工具内 Skill planner 根据自然语言和已有资产选择需求、设计、流程图、实现、测试或查看资产；用户不能直接指定 provider。
4. 系统解析 Skill、Context Bundle、Output Schema 和运行策略。
5. adapter 将同一份输入翻译为 Codex 或 Claude SDK 参数。

不增加 `provider + stage + action` 的组合状态机。用户在按钮旁输入了文字时，文字仍走正常语义路由；按钮只是精确的上下文信号。

## 3. 输出侧：统一业务语义，不统一原始事件

### 3.1 三层输出

```text
Provider Raw Event
  -> adapter 内部事件
  -> Surface Compiler
  -> 用户可见 Surface Block

Provider Final
  -> 同一份 Skill Output Schema 校验
  -> 系统合并、版本化和持久化
  -> 最终 Surface Block
```

三层职责必须分开：

1. **Provider Raw Event**：Codex 的 plan/reasoning/command/MCP 事件与 Claude 的 thinking/content/tool/result 事件各自保留，不作为稳定业务合同。
2. **业务最终结果**：两个 provider 都必须通过当前 Skill Output Schema；模型只输出本轮业务贡献，状态、revision 和归属由系统补齐。
3. **用户输出**：统一为现有 `narrative / artifact / workflow / assessment / interaction` 五类 Surface Block。

因此不需要让 Codex 和 Claude 输出完全相同的中间 JSON，也不需要为两者各写一套业务 Skill。共享 Skill 和 Schema 即可；provider adapter 只处理 SDK 参数、事件读取和最终结果提取。

### 3.2 两家事件的统一投影

| Provider 事件 | 系统理解 | 用户侧投影 |
|---|---|---|
| turn/init/start | 本轮已开始 | 创建或更新 `workflow` |
| plan、reasoning summary、thinking | Agent 仍在工作 | 仅维持进度活性，不展示思考链，不据此伪造业务完成度 |
| text/structured delta | 正在形成业务结果 | 有完整语义单元时更新 provisional `artifact` 或 `interaction` 预览 |
| tool call / tool result / command output | 正在读取、实现或验证 | 只展示安全的动作摘要和结果摘要；原始参数、日志进入 trace |
| validation/test result | 已获得可举证结果 | 更新 `assessment` 和对应 `workflow` 步骤 |
| final/result | 本轮业务结果完成 | Schema 校验后生成最终五类 Surface Block |
| timeout/rate limit/error | 执行受阻 | 用户看到可行动的简短说明；provider 细节和堆栈进入 trace |

`source=codex/claude`、provider session id、token usage 和原始事件名只属于诊断信息。前端不应通过这些字段决定业务渲染。

### 3.3 中间过程展示原则

中间过程要让用户感知真实进展，但不能暴露私有思考链，也不能用事件数量伪造进度。用户可见信息优先级如下：

1. 系统已经理解的目标和当前阶段；
2. 已完成的业务里程碑；
3. 已稳定形成的设计项、模块、验证结果等局部产物；
4. 当前需要用户决定的问题；
5. 可恢复的阻塞原因和下一步。

以下内容默认不直接展示：

- Codex reasoning、Claude thinking 和内部 plan 原文；
- 完整 prompt、Context Bundle 和工具参数；
- 流式生成中的完整源代码；
- 原始 shell 输出、MCP payload、异常堆栈和 credential；
- provider 名称、模型名和内部 session id。

Coding 阶段应展示“正在处理哪些模块、完成了什么验证、当前有哪些问题”，完整源码作为持久化 Artifact 供 View 场景按需查看。原始日志保留在 trace 中，不在对话里持续追加。

## 4. 用户交互：以业务检查点跨 provider 恢复

Codex 与 Claude 的 SDK resume/session 机制不同，不把 provider resume id 提升为业务协议。所有需要用户输入的场景都使用应用层检查点：

```text
Agent 返回 clarification / review / blocked result
  -> 系统保存当前 Artifact、revision 和上下文
  -> 输出稳定 interaction block
  -> 用户通过同一输入框回复
  -> 新 turn 从系统保存的上下文继续
```

首期只保留三类高价值交互：

| 检查点 | 触发条件 | 用户动作 |
|---|---|---|
| 需求澄清 | 缺失信息会实质改变目标或结果 | 回答问题，也可直接用自然语言改写需求 |
| 设计确认 | 已形成完整、可实现的设计快照 | 确认或提出修改 |
| 实现/验证阻塞 | 权限、真实依赖或关键验证无法继续 | 补充信息、授权受控重试或结束 |

普通低风险选择不应反复打断用户。模型可以流式形成问题预览，但只有系统保存了稳定问题和 revision 后，`interaction` 才可提交。

用户在 Agent 运行期间继续输入时，不将文本注入正在执行的 provider turn。系统将其保存为下一 turn 的反馈，并在当前安全检查点衔接；这样不会依赖某一家 SDK 的中断和 resume 行为，也避免同一产物被并发修改。

## 5. 系统模块与用户视角

内部仍可保留现有 conversation management、task analysis、capability selection 和 execution runtime 模块用于编排与 trace，但不把模块名直接暴露给用户。用户只看到与业务有关的四段过程：

```text
理解与确认 -> 形成方案 -> 实现或执行 -> 验证与交付
```

View 场景通常只需要“读取已有资产 -> 展示或解释”，不重新运行 Design/Coding，也不为了统一形式补齐无意义的四段进度。

这四段不是新的生命周期状态机，只是对现有执行证据的展示投影。真实状态仍由当前工作流持有。

## 6. 已落地的运行协议

两个 adapter 现在统一返回 `agent.run.v1` 执行信封：

- 固定字段：`provider / stage / ok / error / timeout / events / final / session_id / provider_session_id / llm_usage / context_bundle / duration_ms`；
- `final` 仍严格使用当前 Skill Output Schema，不被执行信封包办或改写；
- `events` 统一为 `source / type / content / metadata`，由同一 Surface Compiler 投影；
- Web Search 与 MCP 通过 `AgentCapabilityPolicy` 独立配置，不写入复杂度等级或业务 prompt；
- `CUSTOM_TOOL_AGENT_MCP_CONFIG_PATH` 指向显式 JSON 白名单，credential 只引用环境变量名。

原始 provider 事件仍只用于 adapter 解析与 trace，不提升为前端协议。

## 7. 验收标准

1. 上层请求和用户界面不含 provider 专用字段，切换 Codex/Claude 不需要修改业务调用方。
2. 相同 Skill、Context Bundle 和 Output Schema 可由两个 provider 使用，不维护双份业务提示词或 Skill。
3. 两家 provider 的最终结果通过同一 Schema，系统状态、revision 和身份事实不由模型决定。
4. 用户可看到阶段、稳定局部产物、验证摘要和可行动问题，但看不到私有思考链、provider 名称和原始日志。
5. 所有需要用户响应的流程都通过持久化 Artifact + revision + interaction block 跨 turn 继续，不依赖 provider session 恢复。
6. 不新增业务状态机、Design/Coding Schema 或金融业务 validator。
