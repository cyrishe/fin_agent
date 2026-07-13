# Claude Code + DeepSeek V4 Flash

调研日期：2026-07-13。

## 结论

可以直连。DeepSeek 官方 API 已提供 Anthropic Messages 兼容端点：

- base URL：`https://api.deepseek.com/anthropic`
- model：`deepseek-v4-flash`
- 鉴权：本 demo 从 `DEEPSEEK_API_KEY` 读取，并只在 Claude Code 子进程中映射为 bearer token

这不是把 OpenAI Chat Completions 生硬塞进 Claude Code。Claude Code/Agent SDK 仍然说 Anthropic 协议，DeepSeek 官方端点负责协议兼容。

官方资料：

- [DeepSeek：Claude Code 接入](https://api-docs.deepseek.com/guides/agent_integrations/claude_code)
- [DeepSeek：Anthropic API 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api)
- [DeepSeek：模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)
- [Claude Code：环境变量](https://code.claude.com/docs/en/env-vars)

## 已知兼容边界

根据 DeepSeek 官方兼容表，V4 Flash 支持 text、stream、thinking、effort、普通 tools、tool choice、tool use/result，以及 web-search 相关 server blocks。以下能力不能当成完全兼容：

- image 与 document input 不支持；
- Anthropic MCP connector 的 `mcp_tool_use` / `mcp_tool_result` blocks 不支持；
- `anthropic-beta` 与 `anthropic-version` 被忽略；
- 部分 Anthropic 专属字段被忽略。

本 demo 的 Python MCP tools 是 Claude Code 本地执行的工具，并不依赖 Anthropic 托管 MCP connector blocks。内置 WebSearch 虽有协议声明，仍应通过真实账号运行 `--with-web-search` 验证；如果某个 MaaS 不支持，可切换到已有的 SearXNG MCP fallback。

## 安全边界

`CLAUDE_PROVIDER=deepseek` 只允许把 `DEEPSEEK_API_KEY` 发往精确地址 `https://api.deepseek.com/anthropic`。第三方托管的 DeepSeek 模型必须使用 `CLAUDE_PROVIDER=gateway` 和单独的 gateway token，避免误把官方 DeepSeek key 发给其他域名。

所有 Claude Code 模型角色都固定为当前选择的 DeepSeek 模型，避免后台任务意外切换到其他模型。`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` 用于清除工具子进程中的模型凭据。

## 一次性验证

```bash
cd claude_agent_sdk_demo
python3.13 -m venv .venv  # 或任意 Python 3.10+
.venv/bin/pip install -e '.[dev]'
cp .env.deepseek.example .env.deepseek
```

编辑 `.env.deepseek` 写入 key，然后：

```bash
set -a
source .env.deepseek
set +a
.venv/bin/python scripts/check_deepseek.py
```

默认探测包含两个付费请求：

1. 直接请求 DeepSeek Anthropic `/v1/messages`，要求 SSE 并强制产生标准 tool call；
2. 通过 Claude Agent SDK 运行 agent loop，要求调用 `financial-research` Skill 和本地 `get_current_time` MCP tool，并接收最终 `ResultMessage`。

WebSearch 是额外的第三个请求：

```bash
.venv/bin/python scripts/check_deepseek.py --with-web-search
```

脚本返回码含义：

- `0`：所有请求的协议与行为断言通过；
- `1`：真实接口、stream、tool、Skill、SDK 终态或可选 WebSearch 有一项失败；
- `2`：本地配置不完整，未发起模型请求。

脚本不打印 key，也不把响应中的 hidden trace 暴露给前端。

## 启动前端验证

保持上述环境变量已加载：

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8008
```

打开 `http://127.0.0.1:8008`。请求继续使用统一的 `POST /v1/runs/stream` 和 `agent_stream.v1`，因此前端无需知道模型是 Claude 还是 DeepSeek。

## 只测试 Claude Code CLI

如果本机另行安装了 Claude Code CLI，可按 DeepSeek 官方方式运行：

```bash
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY"
export ANTHROPIC_MODEL=deepseek-v4-flash
export ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-flash
export ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-flash
export ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash
export CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-flash
export CLAUDE_CODE_EFFORT_LEVEL=max
claude
```

Python demo 不要求全局安装 `claude` 命令；`claude-agent-sdk` 包会管理它自己的 Claude Code runtime。
