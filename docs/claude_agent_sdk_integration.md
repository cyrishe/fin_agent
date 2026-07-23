# Claude Agent SDK 主线接入

日期：2026-07-20

## 目标与边界

Fin Agent 的 Agent Core 分三层：

1. 基础 LLM + context：普通问答、意图理解和轻量路由；
2. Codex SDK：复杂 Design/Coding 和高上下文工程任务；
3. Claude Agent SDK：与 Codex 对等的复杂任务 provider，用于分散并发和连接风险。

本次只增加第三层 provider adapter，不增加新的业务状态机、Design/Coding schema 或 finance validator。Codex 和 Claude 共享 `financial-tool-development` Skill、context bundle 与 Output Schema。

```text
CustomToolAgentService
        |
        v
AgentSkillHarness protocol
   |                 |
   v                 v
Codex adapter    Claude adapter
   |                 |
   +--------+--------+
            v
现有 Design/Coding HARD 结果协议
```

## 使用

安装依赖后，慢 Agent 总入口通过统一配置选择 provider。当前正式本地配置使用 Claude：

```bash
pip install -r requirements.txt
export CUSTOM_TOOL_AGENT_PROVIDER=claude
export CUSTOM_TOOL_AGENT_COMPLEXITY=fast
export CUSTOM_TOOL_AGENT_WEB_SEARCH=live
```

默认通过 DeepSeek 官方 Anthropic 兼容入口使用 DeepSeek V4 Flash：

```bash
export CUSTOM_TOOL_AGENT_PROVIDER=claude
export DEEPSEEK_API_KEY=...
# 以下两项已有默认值，可以省略
export CLAUDE_PROVIDER=deepseek
export CLAUDE_MODEL=deepseek-v4-flash
```

需要时仍可显式切换到 Anthropic 官方 API：

```bash
export CUSTOM_TOOL_AGENT_PROVIDER=claude
export CLAUDE_PROVIDER=anthropic
export ANTHROPIC_API_KEY=...
export CLAUDE_MODEL=<anthropic-model-name>
```

`deepseek` 固定使用官方入口 `https://api.deepseek.com/anthropic`。原有基础 LLM 仍可独立使用 OpenAI-compatible endpoint；两个协议入口与凭证由 adapter 隔离。若需回切百炼，可显式设置 `CLAUDE_PROVIDER=dashscope`、对应的 `DASHSCOPE_API_KEY` 和官方 `/apps/anthropic` 入口。

也可在代码中显式选择，不依赖全局配置：

```python
from src.services.agent_providers import (
    AgentCapabilityPolicy,
    WebSearchPolicy,
    build_agent_skill_harness,
)

capabilities = AgentCapabilityPolicy(web_search=WebSearchPolicy.DISABLED)
codex = build_agent_skill_harness("codex", complexity="high", capabilities=capabilities)
claude = build_agent_skill_harness("claude", complexity="high", capabilities=capabilities)
```

## 四级复杂度协议

顶层只稳定四个业务中立等级。adapter 在 provider 边界内翻译模型、推理强度和建议最大轮数，不把这些字段加入 Design/Coding 业务结果。

| 等级 | 任务定位 | Codex | Claude + DashScope |
| --- | --- | --- | --- |
| `fastest` | 直接问答、确定分类 | `gpt-5.6-luna`, effort `none` | `deepseek-v4-flash`, effort `low`, thinking disabled |
| `fast` | 问题简单但上下文或链路较长 | `gpt-5.6-terra`, effort `high` | `deepseek-v4-flash`, effort `high` |
| `mid` | 中等复杂分析与实现 | `gpt-5.6-terra`, effort `medium` | `deepseek-v4-pro`, effort `medium` |
| `high` | 最复杂、上下文交互困难的专业任务 | `gpt-5.6-sol`, effort `high` | `deepseek-v4-pro`, effort `high` |

Claude 的三个控制面保持分离：`effort` 控制回答与工具调用的 token/工作强度；`thinking` 控制是否启用扩展思考；`max_turns` 限制 agent 链路长度。DashScope DeepSeek V4 的 `reasoning_effort` 当前支持 `low/medium/high/xhigh/max`，但 provider 当前说明 `low/medium` 的实际效果与 `high` 相同、`xhigh` 与 `max` 相同。因此四级协议保持语义稳定，不承诺每个 provider 当前都能提供四档物理差异。

不传 `complexity` 时保留旧默认，避免一次接入改变现有主线。也可通过 `CUSTOM_TOOL_AGENT_COMPLEXITY` 统一设置；显式的 provider model/effort 环境变量仍作为运维覆盖项。

## 独立 Capability Policy

Web 与 MCP 不属于复杂度等级，必须单独传入：

```python
capabilities = AgentCapabilityPolicy(
    web_search=WebSearchPolicy.LIVE,
    mcp_servers={
        "docs": {
            "url": "https://example.com/mcp",
            "bearer_token_env_var": "DOCS_MCP_TOKEN",
            "allowed_tools": ["search"],
        }
    },
)
harness = build_agent_skill_harness("claude", complexity="fast", capabilities=capabilities)
```

- `web_search`: `disabled | provider_default | live`；不传 capability policy 时保留 provider 的旧行为。
- MCP server 必须给出本次连接的 `command` 或 `url`，并显式列出允许的工具；adapter 不从业务 prompt 猜测权限。
- credential 只能通过 `bearer_token_env_var`、`env_vars` 或 `env_http_headers` 引用进程环境变量，禁止把 token 直接放入 policy、命令行或事件。
- Claude 使用 `setting_sources=[]`、`strict_mcp_config=True`，只加载本次 policy。
- Codex 的显式 capability run 使用临时隔离 `CODEX_HOME`，只链接现有订阅登录凭据，不继承用户全局 plugins/MCP，也不修改用户配置。未传 capability policy 时保持原有 Codex 配置行为。

这种拆分对应 SOFT → HARD → SOFT：上游用自然语言/路由选择等级和能力；HARD 只保存四级枚举与显式能力白名单；provider adapter 再把它翻译成各 SDK 的 SOFT 执行参数。

## 官方依据

- OpenAI 当前模型与 reasoning effort：<https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol.md>
- Claude effort：<https://platform.claude.com/docs/en/build-with-claude/effort>
- Claude Agent SDK MCP：<https://code.claude.com/docs/en/agent-sdk/mcp>
- Claude Agent SDK permissions：<https://code.claude.com/docs/en/agent-sdk/permissions>
- Claude Agent SDK tool search：<https://code.claude.com/docs/en/agent-sdk/tool-search>
- DashScope DeepSeek V4 模型与 `reasoning_effort`：<https://help.aliyun.com/en/model-studio/deepseek-api>

安装依赖并配置凭据后，可发起一次有成本上限的真实 Structured Output 探测：

```bash
# 默认使用 DashScope + deepseek-v4-flash
python scripts/check_claude_agent_provider.py
# 或显式指定 provider / model
python scripts/check_claude_agent_provider.py --provider anthropic --model <anthropic-model-name>
```

脚本不会打印 credential；返回码 `0` 表示结构化结果成功，`1` 表示 provider/模型请求失败，`2` 表示 SDK 尚未安装。

## 稳定协议

两个 provider 都实现：

- `available()`
- `run_skill(...)`
- `run_turn(...)`

`run_skill` 的输入继续是 Skill path、Output Schema path、user request、context bundle 和 stage；输出统一为 `agent.run.v1` 执行信封。Claude 自身的 `ResultMessage.structured_output` 和 Codex 原始 turn result 只在 adapter 内出现，随后都转成同一 `final` 与事件语义。

Anthropic 原生 provider 优先使用 SDK `structured_output`。DashScope/DeepSeek 兼容入口不稳定支持该 SDK 专用工具，因此 adapter 要求模型返回单个 JSON 对象，再用同一份 Output Schema 在本地严格校验。校验失败不会进入业务 `final`。

## 权限与可靠性

- Design/Direct 为只读 profile，只暴露 `Read`、`Glob`、`Grep`。
- Coding 只允许修改 context bundle 中 `module_files` 列出的文件。
- Bash 只允许在 SDK sandbox 中执行窄化的 Python compile/test 命令，不允许网络、重定向、命令串联或 unsandboxed execution。
- provider credential 只注入 Claude Code 子进程，不进入 prompt、event 或业务结果。
- idle timeout、hard timeout、max turns 和可选 budget 都由 adapter 控制。
- app 的 `session_id` 不直接作为 Claude resume id；adapter 单独返回 `provider_session_id`，避免绕过用户归属校验。

## 当前未做

- 不自动在 Codex/Claude 之间 failover。失败后的切换策略需要先定义幂等、workspace revision 和计费边界。
- 不让复杂度 profile 自动决定 Web/MCP；工具需求应由调用方显式判断和授权。
- 不把现有业务 Skill 复制到 `.claude/skills`。当前直接注入同一份 Skill 内容，避免双份协议漂移。
- 不让 Claude 直接调用生产 finance/database 工具。生成代码仍只依赖现有 `custom_tool_sdk` 合同。
