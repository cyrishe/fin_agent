# 调研结论：现有 Codex harness 与 Claude Agent SDK

调研日期：2026-07-13。

## 1. 当前项目中 Codex SDK 的实际职责

当前 Codex SDK 主要服务于“自定义金融工具的需求理解与编码”支线，并不是整个金融 Agent 的唯一运行时。

关键实现：

- `src/services/codex_exec_skill_harness.py`
  - `CodexExecSkillHarness`：CLI fallback，执行 `codex exec --json`，读取 skill 与配套 schema，捕获 stdout/stderr，区分 idle/hard timeout，归一化过程事件和最终结果。
  - `CodexSdkSkillHarness`：通过 `openai_codex` 创建 thread/turn，配置 sandbox、deny-all approval、model、reasoning summary 和 strict structured output，消费 SDK notifications。
  - `CodexCustomToolDesigner` / `CodexCustomToolCoder`：把 provider harness 适配成 design/coding 两个业务阶段。
- `src/services/custom_tool_context_bundle_service.py`
  - 把任务、金融 API catalog、runtime contract 和 custom tool SDK 说明写成文件化 context bundle，模型按需读取。
- `src/services/custom_tool_service.py`
  - 业务状态机：需求澄清、设计审核、确认、编码、sandbox smoke test、draft、commit；provider harness 不拥有这套业务状态。
- `src/services/llm_stream_block_service.py`
  - 把 Codex/model/tool/harness 原始事件变成前端稳定 block。
- `src/web/flask_app.py`
  - 负责 SSE、thread/turn 持久化、interaction response、最终 surface blocks 和错误收口。

所以值得保留的边界是：

```text
业务状态机 / 前端协议
        ↓
provider-neutral harness
        ↓
Codex adapter | Claude adapter | future provider adapter
```

不应让 Claude adapter 伪造 Codex notification；只应把两者映射到统一事件语义。

## 2. Claude Agent SDK 已确认能力

官方当前名称是 **Claude Agent SDK**，旧名 Claude Code SDK。Python 包是 `claude-agent-sdk`；本 demo 固定调研时最新版本 `0.2.116`。SDK wheel 自带 Claude Code native CLI，因此 Python 进程实际上管理一个本地 Claude Code subprocess 和 agent loop。官方概览说明 SDK 复用 Claude Code 的 tools、agent loop 和 context management，并直接提供 Read/Edit/Bash/WebSearch 等能力：[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)。

能力与设计影响：

| 能力 | 官方行为 | demo 设计 |
|---|---|---|
| 流式输出 | `include_partial_messages=True` 才会得到 raw `StreamEvent` token/tool input delta；完整 `ResultMessage` 最后到达 | adapter 归一化 delta；harness 以 Result 为唯一终态 |
| 自定义 tools | Python `@tool` + `create_sdk_mcp_server`，在当前 Python 进程内执行，无需单独 MCP subprocess | 时间与 SearXNG 搜索作为 in-process MCP tools |
| Skills | 必须是 `.claude/skills/<name>/SKILL.md` 文件；按描述渐进加载；`skills` 参数控制可见集合 | demo 自带 `financial-research` skill；API 只能选择本地 registry 中存在的名称 |
| Prompt | 可选 minimal、完整 `claude_code` preset、preset append 或完整 custom prompt | 非 terminal 场景默认 custom system prompt，CLAUDE.md 只放 workspace 规则 |
| Permissions | `allowed_tools` 只表示 auto-approve，不是可见性白名单；deny rules 优先；hooks 可在执行前阻断 | 同时使用 `tools`、`disallowed_tools`、`dontAsk`、strict MCP 和 `PreToolUse` |
| Sessions | `ClaudeSDKClient` 支持进程内多轮；`resume=session_id` 支持重启后继续；Python transcript 默认持久化本地 | 每个 HTTP turn 新建 client，可传 resume；config 目录隔离到 demo 内 |
| Structured output | `output_format={"type":"json_schema","schema":...}`，结果在 `ResultMessage.structured_output` | chat demo保留自然流式文本；未来业务 stage 可开启 schema |
| Hooks | Python 支持 Pre/Post tool、prompt submit、stop、compact、subagent 等 hooks | 权限和审计是确定性代码，不依赖 prompt 自觉 |
| 成本/边界 | `max_turns`、`max_budget_usd`、usage、cost、rate limit event | 直接映射到 run terminal event |

官方依据：

- [Python SDK reference](https://platform.claude.com/docs/en/agent-sdk/python)
- [Streaming output](https://code.claude.com/docs/en/agent-sdk/streaming-output)
- [Custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)
- [Agent Skills in the SDK](https://code.claude.com/docs/en/agent-sdk/skills)
- [Modifying system prompts](https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts)
- [Permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
- [Hooks](https://code.claude.com/docs/en/agent-sdk/hooks)
- [Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)
- [Structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
- [Hosting](https://code.claude.com/docs/en/agent-sdk/hosting)

## 3. 自有 MaaS 的真实接入边界

`ANTHROPIC_BASE_URL` 支持把 Claude Code/Agent SDK 指向网关，但这不代表它支持任意模型协议。官方 gateway 要求至少提供以下一种 Claude Code 能理解的 API 格式；本方案选择最便于自有 MaaS 实现的 Anthropic Messages 格式：

- `/v1/messages`
- `/v1/messages/count_tokens`
- SSE streaming content blocks
- 转发 `anthropic-version` 与 `anthropic-beta`
- 正确处理 system、tools、tool use/result、thinking/stream events 和 stop reasons

详见 [LLM gateway configuration](https://code.claude.com/docs/en/llm-gateway) 与 [environment variables](https://code.claude.com/docs/en/env-vars)。

主要风险：

1. 只有 `/v1/chat/completions` 的 OpenAI-compatible MaaS 不能直接承载 Claude Agent SDK。
2. 即使做了 Messages 外壳，底层模型若不能稳定遵循 Claude tool-use/content-block 语义，agent loop 仍会失效。
3. Claude 内置 WebSearch、prompt cache、beta tool features 在 gateway 上取决于网关和底层模型的实现。
4. Claude Code 会使用一些 Claude-specific headers 和行为；网关不能随意剥离。

因此 demo 把搜索做成两层：

- `builtin`：模型/provider 原生支持时使用 `WebSearch`；
- `searxng`：由本应用执行搜索并以 MCP tool result 返回，减少对 MaaS server-tool 的依赖。

这也是 wrapper 层适配的典型例子：业务只要求“可搜索并返回证据”，不要求所有 provider 的内部工具都同名。

## 4. Claude 方案中值得反向吸收的点

- Skills 原生采用元数据先发现、正文按需加载，适合降低长 prompt 成本。
- in-process MCP server 是清晰的 Python tool boundary，适合把现有金融工具 adapter 化。
- Hooks 比 prompt 中写“不要做危险操作”更可靠，可用于审计、deny、输入改写和审批桥接。
- `ResultMessage` 明确承载 session、usage、cost、turns 和 final result，过程流与终态分离。
- provider-specific session transcript 不应成为唯一业务状态；主线现有 thread/turn/artifact 状态机仍应掌握业务真相。

### 2026-07-13：DeepSeek V4 Flash 补充调研

DeepSeek 官方已发布 `deepseek-v4-flash`，并提供 `https://api.deepseek.com/anthropic` Anthropic 兼容端点及 Claude Code 官方配置。它支持 streaming、tool calls、thinking/effort，适合通过现有 Claude adapter 接入；无需在官方 DeepSeek 服务前再部署协议转换代理。

兼容并非等同：image/document、Anthropic 托管 MCP connector blocks 等不支持，部分 Anthropic 字段被忽略。因此 wrapper 继续保留 provider capability 边界，并增加真实 SSE/tool/SDK/WebSearch 探测，而不是仅凭一次文本回答判定兼容。详见 [DEEPSEEK_V4_FLASH.md](DEEPSEEK_V4_FLASH.md)。

## 5. 不建议照搬的部分

- 不把 Claude 本地 transcript 当成多租户产品数据库。
- 不为追求“像 Codex”而启用 Bash/Write/Edit；金融问答与工具设计应按任务建立不同 capability profile。
- 不把 Claude raw content blocks 直接暴露成前端长期协议。
- 不在共享容器加载 user settings/auto memory；官方 hosting 文档明确提示存在跨租户上下文泄漏风险。
- 不把 `bypassPermissions` 当成无人值守默认值。
