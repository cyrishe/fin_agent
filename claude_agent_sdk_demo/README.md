# Claude Agent SDK Harness Demo

这是一个与当前主线代码完全隔离的 Claude Agent SDK Python demo。它展示：

- 前端以 `POST` 提交问题，后端通过 SSE 增量返回文本、tool 和 run 事件；
- Claude Agent SDK 的 filesystem Skills、系统提示词和 `CLAUDE.md` 约束；
- Claude 内置 `WebSearch`，以及 MaaS 不支持内置搜索时的 SearXNG in-process MCP fallback；
- 自定义 Python tools、严格工具可见性、`dontAsk` 权限模式和 `PreToolUse` hooks；
- 可显式开启的 session resume、turn/budget/idle/hard timeout 和并发限制；
- Anthropic 直连与 Anthropic Messages-compatible MaaS gateway 两种配置；
- 官方 DeepSeek Anthropic API 的 `deepseek-v4-flash` 独立 provider profile 与兼容性探测；
- 无凭据可运行的 fake backend，用于先验证前端和 harness 合同。

这个目录不 import 主项目的 `src`，也不要求修改根目录依赖。

## 1. 离线验证

```bash
cd claude_agent_sdk_demo
python3.13 -m venv .venv  # 或任意 Python 3.10+
.venv/bin/pip install -e '.[dev]'
CLAUDE_DEMO_BACKEND=fake .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8008
```

浏览器打开 `http://127.0.0.1:8008`，或另开终端运行：

```bash
cd claude_agent_sdk_demo
.venv/bin/python scripts/smoke_sse.py
```

fake 输出带有显式 `[FAKE BACKEND]` 标记，不会被误认为模型结果。

## 2. Anthropic 直连

```bash
cd claude_agent_sdk_demo
cp .env.example .env
```

在 `.env` 中配置：

```dotenv
CLAUDE_DEMO_BACKEND=claude
CLAUDE_PROVIDER=anthropic
CLAUDE_MODEL=sonnet
CLAUDE_API_KEY=your-key
WEB_SEARCH_BACKEND=builtin
```

加载环境并启动：

```bash
set -a
source .env
set +a
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8008
```

`GET /healthz` 只返回配置是否齐全，不返回密钥。

## 3. 自有 MaaS / LLM gateway

Claude Agent SDK 运行的是 Claude Code agent loop，不是通用 OpenAI-compatible client。自有 MaaS 至少要暴露 Anthropic Messages 格式，包括 `/v1/messages` 流式语义、`/v1/messages/count_tokens`，并透传 `anthropic-version` 和 `anthropic-beta` headers。

```dotenv
CLAUDE_DEMO_BACKEND=claude
CLAUDE_PROVIDER=gateway
CLAUDE_BASE_URL=https://maas.example.com
CLAUDE_AUTH_TOKEN=your-bearer-token
CLAUDE_MODEL=your-model-id
```

配置探测（调用 count_tokens，不生成回答）：

```bash
.venv/bin/python scripts/check_gateway.py
```

如果 MaaS 不支持 Claude 内置 `WebSearch`，使用独立搜索后端：

```dotenv
WEB_SEARCH_BACKEND=searxng
SEARXNG_BASE_URL=https://search.example.com
```

这时 agent 调用 `mcp__demo__web_search`，搜索服务与模型供应商解耦。

## 4. DeepSeek V4 Flash

DeepSeek 已提供原生 Anthropic 格式接口，因此官方服务不需要 LiteLLM 转换层：

```bash
cd claude_agent_sdk_demo
cp .env.deepseek.example .env.deepseek
```

把 `.env.deepseek` 中的 `DEEPSEEK_API_KEY` 替换成真实 key，然后执行：

```bash
set -a
source .env.deepseek
set +a
.venv/bin/python scripts/check_deepseek.py
```

该命令会产生少量真实 API 费用，并依次验证：Anthropic SSE、强制 tool call、Claude Agent SDK agent loop、filesystem Skill 和本地 MCP tool。额外验证 DeepSeek/Claude Code 的内置 WebSearch：

```bash
.venv/bin/python scripts/check_deepseek.py --with-web-search
```

验证通过后启动同一个前端 demo：

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8008
```

浏览器打开 `http://127.0.0.1:8008` 即可提问。详细兼容边界、测试判定和 CLI 直连方式见 [DEEPSEEK_V4_FLASH.md](DEEPSEEK_V4_FLASH.md)。

当前工作区已经准备好 Python 3.13 的独立 `.venv`；不需要修改或激活主项目环境。

### 阿里云百炼 DashScope 上的 DeepSeek V4 Flash

百炼的 `/compatible-mode/v1` 是 OpenAI 协议，不能直接给 Claude Code 使用。按量计费的 Anthropic 兼容入口是 `https://dashscope.aliyuncs.com/apps/anthropic`。demo 可复用主项目 `.env` 中的 `LLM_KEY` 与 `LLM_DEFAULT_MODEL`，不会读取或改写 `LLM_ENDPOINT`：

```bash
cd claude_agent_sdk_demo
set -a
source ../.env
set +a
export CLAUDE_DEMO_BACKEND=claude
export CLAUDE_PROVIDER=dashscope
.venv/bin/python scripts/check_deepseek.py
```

也可以复制 `.env.dashscope.example` 使用独立 `DASHSCOPE_API_KEY`。通过后按相同环境启动 uvicorn。该 profile 将凭据限制到百炼官方 Anthropic endpoint。

## 5. API 合同

### `POST /v1/runs/stream`

```json
{
  "question": "今天影响 A 股市场的主要因素是什么？",
  "session_id": null,
  "skill_names": ["financial-research"],
  "enable_web_search": true,
  "output_mode": "text",
  "client_request_id": "web-001",
  "metadata": {}
}
```

`output_mode="research_json"` 使用服务端预定义 JSON Schema，验证后的对象出现在终态事件的 `data.structured_output`。请求方不能上传任意 schema；主线以后应由可信的 skill/stage registry 选择 schema。

响应为 `text/event-stream`。每个 `data` 都是 `agent_stream.v1`：

```json
{
  "version": "agent_stream.v1",
  "event_id": "evt_...",
  "run_id": "run_...",
  "seq": 3,
  "type": "assistant.delta",
  "source": "claude",
  "channel": "user",
  "timestamp": "2026-07-13T...Z",
  "data": {"text": "..."}
}
```

稳定事件包括：

- `run.started`
- `session.started`
- `assistant.delta`
- `tool.started`
- `tool.input.delta`（diagnostic channel）
- `tool.input.completed`（diagnostic channel）
- `tool.requested`（diagnostic channel）
- `tool.completed`
- `provider.rate_limit`
- `run.completed`
- `run.failed`

SSE 注释 heartbeat 不占用业务事件 `seq`。前端应以 `run.completed` 或 `run.failed` 为终态，不把最后一个 token 当作完成。

### 其他端点

- `GET /healthz`：SDK/凭据/gateway/search 配置自检，不做付费模型调用。
- `GET /v1/skills`：本实例允许启用的 Skills。
- `GET /`：最小验证 UI。

设置 `DEMO_CLIENT_API_KEY` 后，`/v1/*` 请求必须携带 `X-Demo-Api-Key`。生产环境仍应在 API gateway 处理真实身份、租户和限流。

`session_id` 默认被拒绝。只有宿主已经校验“当前用户拥有该 session”后，才应设置 `DEMO_ALLOW_SESSION_RESUME=true`。这个开关只演示 SDK resume 能力，不替代 ownership 表。

## 6. 设计取舍

- 默认是非编码 chat/research agent，因此使用自己的系统提示词，而不是硬套 Claude Code terminal prompt。
- Skills 保留 Claude 原生 filesystem discovery；prompt、tools、permissions、事件协议由外层 wrapper 管理。
- `allowed_tools` 在 Claude SDK 中只是免审批规则。真正的能力收窄同时依赖 `tools`、`disallowed_tools`、`permission_mode="dontAsk"`、strict MCP config 和 hooks。
- Python SDK 的 session transcript 默认写本机；本 demo 将 `CLAUDE_CONFIG_DIR` 隔离到本目录 `.runtime/`，并关闭 auto memory，避免用户级上下文泄漏。
- 默认不提供 Bash/Read/Write/Edit/Task。将来若开放代码 agent，应放进独立容器和独立 workspace，不应只改一个 prompt。

后续开发先读 [HANDOFF.md](HANDOFF.md)，进一步说明见 [RESEARCH.md](RESEARCH.md)、[ARCHITECTURE.md](ARCHITECTURE.md) 和 [MERGE_GUIDE.md](MERGE_GUIDE.md)。
