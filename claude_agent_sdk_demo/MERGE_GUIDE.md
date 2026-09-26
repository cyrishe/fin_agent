# 主线合并建议

本 demo 不应整目录原样塞入主线。建议按下面顺序小步合并。

## 第一阶段：只引入 provider abstraction

可复用：

- `app/event_stream.py` 的 provider-neutral envelope 思路；
- `app/backend.py` 中 `AgentBackend` protocol；
- `app/normalizer.py` 中 Claude message → neutral event mapping；
- `app/harness.py` 中 idle/hard timeout 和 terminal-result 规则。

主线落点建议：

```text
src/services/agent_providers/base.py
src/services/agent_providers/codex.py
src/services/agent_providers/claude.py
src/services/agent_provider_harness.py
```

不要删除现有 `CodexSdkSkillHarness`；先写 adapter，让 Codex 与 Claude 同时通过新 protocol 跑同一组 contract tests。

## 第二阶段：接入现有 custom tool design/coding flow

现有 `CodexCustomToolDesigner` / `CodexCustomToolCoder` 前面增加 provider factory，而不是在类内部写 Claude 分支。

建议配置：

```text
CUSTOM_TOOL_AGENT_PROVIDER=codex|claude
CLAUDE_PROVIDER=anthropic|deepseek|dashscope|gateway
CLAUDE_BASE_URL=...
CLAUDE_MODEL=...
```

`deepseek` 只代表 DeepSeek 官方 Anthropic endpoint，并使用专用 `DEEPSEEK_API_KEY`；第三方 MaaS 即使承载 DeepSeek weights，也应使用 `gateway` profile 和独立凭据。不要根据 model name 在业务层散落 DeepSeek 分支，provider adapter 应输出统一 capability：text、stream、tools、web search、vision、structured output 等。

`dashscope` 代表百炼官方 Anthropic endpoint。主线原有 `/compatible-mode/v1` 继续服务 OpenAI SDK；Claude adapter 必须改用同地域的 `/apps/anthropic`，不能把两个 base URL 混用。

design/coding stage 继续使用现有业务 schema。Claude SDK 可通过 `output_format` 返回 `ResultMessage.structured_output`，adapter 再包装成现有 `final`，不要求 Claude 输出 Codex NDJSON。

## 第三阶段：工具与 Skills

- 先把只读工具适配成 in-process MCP；写/删/发布工具后做。
- 现有 Skill 不一定能直接复制为 Claude Skill：需要补 YAML frontmatter 的 `name`/`description`，并移除 provider transport 指令。
- Claude SDK 中 `SKILL.md` 的 `allowed-tools` 不负责 SDK 工具授权，权限必须由外层 profile 配置。
- 生产金融工具的 owner/tenant 检查仍放在工具执行端，不能交给模型或 MCP 描述。

## 第四阶段：前端协议适配

现有前端消费的是项目 block/surface 协议。推荐：

```text
Claude raw message
  -> neutral harness event
  -> existing LlmStreamBlockBuilder / future agent_surface.v1 compiler
```

不要在 `LlmStreamBlockBuilder` 中遍布 `source == "claude"` 分支；优先让 provider adapter 输出统一的 `assistant.delta`、`tool.started`、`tool.completed` 与 final result。

## 合并前必须补的生产项

1. API gateway 身份认证、tenant ownership 和每用户限流。
2. session transcript 的共享存储或显式放弃 provider resume，不能依赖单机 `~/.claude`。
3. 每 run 独立 workspace/container；研究与编码 profile 分离。
4. 工具级 timeout、retry、idempotency、credential proxy 和审计持久化。
5. MaaS contract tests：Messages streaming、tools、stop reason、thinking/partial blocks、beta headers、context limits。
6. 内置 WebSearch 不可用时的 capability negotiation 和 fallback。
7. structured output schema 的真实模型 eval，尤其是设计澄清和 coding failure path。
8. DeepSeek 先运行 `scripts/check_deepseek.py`；只有 `--with-web-search` 也通过后，才能把内置 WebSearch capability 标记为可用。

## 建议的兼容测试

用同一组业务 case 跑 Codex 与 Claude adapter：

- 简单自然语言回答；
- skill 被正确触发；
- tool call/result 顺序；
- schema final success；
- missing tool / permission denied；
- idle 与 hard timeout；
- session resume；
- prompt injection in web/tool output；
- SSE 重连或 snapshot 恢复（由主线协议层实现）。
