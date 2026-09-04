# 独立金融数据 API 服务

## 目标与边界

该服务把现有金融查数能力作为一个独立进程对外提供，同时保留两种消费方式：

1. 普通 HTTP 应用使用 REST API；
2. Agent/MCP 客户端通过工具描述发现并调用同一个 `finance_data_query`。

服务不复制金融 Provider，也不另建目录。REST、问答和 MCP 最终都调用
`FinancialQaCcService`，共用当前 Catalog、API DSL、结构化静态检查、结果存储以及
CC/DeepSeek Harness 执行路径。

```text
REST /v1/finance/query ─┐
REST /v1/finance/answer ├─> FinanceApiGateway ─> FinancialQaCcService
MCP  /mcp               ┘                         ├─> CC
                                                  └─> DSH
                                                       └─> Catalog / Provider / result_ref
```

## 启动

安装依赖并配置 `.env`：

```bash
python -m pip install -r requirements-finance-api.txt

FINANCE_API_KEY='使用密码管理器生成的至少24位随机Key'
FINANCE_API_KEY_ID='internal'
FINANCE_API_DEFAULT_RUNTIME='dsh'
FINANCE_DSH_FINANCIAL_QA_ENABLED='1'
FINANCE_API_ALLOWED_HOSTS='finance-api.example.com,127.0.0.1:*'
```

服务器不要复制开发电脑的 `.env`。可以直接从聚焦的部署模板开始：

```bash
cp deploy/finance-api/.env.example .env
chmod 600 .env
```

该模板把三类模型凭证分开：金融 DSH 使用 `FINANCE_DSH_*`，Codex CRS 使用
`CODEX_CRS_*`，百炼通用/Claude 入口使用 `DASHSCOPE_*`。因此服务器不会隐式拿到开发者
个人的 DeepSeek Key 或 Codex 订阅状态。

服务器必须显式使用：

```dotenv
STOCK_AGENT_CODEX_AUTH_MODE=crs_api_key
CODEX_CRS_API_KEY=...
FINANCE_DSH_API_KEY=...
FINANCE_DSH_BASE_URL=https://api.deepseek.com
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_ANTHROPIC_BASE_URL=https://dashscope.aliyuncs.com/apps/anthropic
```

Codex adapter 会在每个隔离 session 的 `CODEX_HOME` 自动生成以下结构，不需要人工维护
服务器的 `~/.codex/config.toml`：

```toml
model_provider = "crs"
model = "gpt-5-codex"
model_reasoning_effort = "high"
disable_response_storage = true
preferred_auth_method = "apikey"

[history]
persistence = "none"

[model_providers.crs]
name = "crs"
base_url = "https://proxy.kingdomai.com/openai"
wire_api = "responses"
env_key = "CODEX_CRS_API_KEY"
```

这里使用 `env_key` 指向服务器环境变量，Key 本身不会写进 TOML。相比
`requires_openai_auth=true`，这种方式不会误用默认 OpenAI 登录凭证，更符合 CRS 独立 Key
的实际认证方式。

启动独立进程：

```bash
python -m src.finance_api.app
```

进程会读取仓库根目录的 `.env`，但已经由 systemd、容器或 Secret Manager 注入的环境变量
优先，不会被 `.env` 覆盖。

默认监听 `0.0.0.0:22100`。服务内已经有 DSH worker pool，因此单个容器/实例使用
一个 Uvicorn worker；横向扩容时增加容器或 systemd 实例，不在同一实例中复制多套
DSH worker pool。

### 8C/16GB、10 个同时请求

建议起始配置：

```dotenv
FINANCE_API_MAX_CONCURRENCY=10
FINANCE_DSH_WORKERS=10
FINANCE_DSH_QUEUE_TIMEOUT_SECONDS=300
FINANCE_DSH_TURN_TIMEOUT_SECONDS=300
FINANCE_DSH_PREWARM_ON_START=1
FINANCE_DSH_PREWARM_WORKERS=10
```

入口 semaphore 最多放行 10 个查询；新 DSH session 从空闲 worker 中租用一个，结束后归还，
不会再因 thread hash 落到同一 worker 而无效串行。多轮请求保持原 worker 亲和，以继续使用
Harness 内的 session；省略 `conversation_id` 的普通查询则生成独立 thread、DSH session、
runtime scope 和 `result_ref` 命名空间，并且不保留 session-to-worker 亲和记录，彼此不能读取
对方的 working set。`queue_wait_ms` 与
`worker_index` 写入旁路执行记录，可用于判断瓶颈是在排队还是模型/数据库阶段。
独立 API 启动时会并行预热 10 个 Harness/MCP worker，避免第一批请求分别承担冷启动；预热
失败只记录告警，worker 在实际请求到达时仍可重试初始化。

这里保留一个 Uvicorn 进程是有意的：10 个 DSH worker 已经是实际并发单元，多开 Web worker
会按进程倍增 Harness/MCP 子进程和内存。该工作负载主要等待远端 LLM 与 MySQL，8 个 CPU 核
无需与请求数一一对应。上线时观察常驻内存、模型供应商并发/限流、MySQL `max_connections`
和 `queue_wait_ms`；若内存压力明显，将 API 与 DSH 两个并发值一起降到 6–8，若队列长期增长
则增加实例，而不是继续扩大单实例队列。

不能在同一个 DSH worker 内直接并行两个金融 turn：该 worker 的 Harness/MCP 子进程、
`turn_context.json` 与 `turn_trace.json` 是一组绑定资源。worker 独占锁保证一次只装载一个
请求上下文；并发通过多 worker 实现，而不是让两个请求共享同一 context 文件。远端 MySQL
连接按 provider 调用创建并在 `finally` 关闭，因此 10 个活跃 turn 不共享 cursor/connection；
数据库连接超时和查询读取超时仍由现有连接层控制。

生产环境应由 Nginx、ALB 或 API Gateway 提供 HTTPS，并设置请求体、连接数和整体超时。
不要通过 URL query string 传递 Key，因为 URL 容易进入访问日志。

仓库提供了可直接调整路径和域名的部署模板：

- `deploy/systemd/fin-agent-finance-api.service`：单进程、自动重启、独立系统用户；
- `deploy/nginx/finance-api.conf.example`：HTTPS 反向代理与 MCP/长查询超时。

```bash
sudo cp deploy/systemd/fin-agent-finance-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fin-agent-finance-api
sudo systemctl status fin-agent-finance-api
```

模板假设代码位于 `/opt/fin_agent`，运行用户为 `fin-agent`；部署前按服务器实际情况修改。

## API Key

二选一：

```bash
# 单调用方
FINANCE_API_KEY_ID=internal
FINANCE_API_KEY=replace-with-at-least-24-random-characters

# 多调用方；该配置优先
FINANCE_API_KEYS_JSON='{"internal":"key-1-at-least-24-characters","partner-a":"key-2-at-least-24-characters"}'
```

请求优先使用：

```http
Authorization: Bearer <key>
```

也兼容：

```http
X-API-Key: <key>
```

Key 只在进程内做 SHA-256 摘要并使用常量时间比较，不进入业务 trace。缺少配置时服务
拒绝启动；缺少或错误 Key 的查询返回 `401`。

以下入口公开且不访问金融事实数据：

- `GET /health`
- `GET /data-map`
- `GET /data-map/catalog.json`
- `GET /docs`
- `GET /openapi.json`

所有 `/v1/*` API、工具发现和 MCP 入口均需要 Key。

## REST 查询

### 同时返回摘要和数据

```bash
curl -X POST 'https://finance-api.example.com/v1/finance/query' \
  -H "Authorization: Bearer $FINANCE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "贵州茅台最近五个交易日的收盘价和涨跌幅是多少？",
    "response_mode": "both",
    "runtime": "dsh",
    "research_mode": "fast",
    "conversation_id": "portfolio-session-001",
    "max_rows": 100
  }'
```

`response_mode` 的稳定含义：

| 值 | 返回行为 | 适用方 |
|---|---|---|
| `data` | 返回结构化数据；数据取齐后跳过最终自然语言生成 | 程序、量化流程、后续分析 Agent |
| `summary` | 返回基于工具证据生成的中文回答，不返回行数据 | 问答界面 |
| `both` | 同时返回中文回答和结构化数据 | 需要展示证据的应用 |

`max_rows` 是每个结果在 HTTP 响应中的最大行数，范围 `1..100`。`row_count` 保留完整
结果数量，`truncated=true` 表示当前响应只包含前一页。该参数只控制对外传输，不改变
金融查询语义。

响应主体：

```json
{
  "id": "fq_...",
  "object": "finance.query",
  "created_at": "2026-09-04T00:00:00+00:00",
  "ok": true,
  "query": "...",
  "response_mode": "both",
  "runtime": "dsh",
  "conversation_id": "portfolio-session-001",
  "summary": "...",
  "data": {
    "format": "row-dict",
    "results": [
      {
        "result_name": "r1",
        "goal": "...",
        "api": "stock.quote",
        "schema": {},
        "row_count": 5,
        "rows_returned": 5,
        "truncated": false,
        "rows": []
      }
    ]
  },
  "execution": {
    "duration_ms": 12000,
    "worker_index": 3,
    "queue_wait_ms": 0,
    "model_name": "deepseek-v4-flash",
    "reasoning_effort": "low",
    "tool_call_count": 2,
    "result_count": 1,
    "total_rows": 5,
    "returned_rows": 5,
    "truncated": false,
    "apis": ["stock.quote"]
  },
  "error": null
}
```

`POST /v1/finance/answer` 是问答应用的便捷入口。它固定生成摘要，通过
`include_data=true` 决定是否同时返回证据行。

相同 API Key 下传入相同 `conversation_id` 可延续多轮上下文；不同 Key 即使使用相同
`conversation_id`，内部 session 也完全隔离。省略该字段时每次请求都是独立会话。

## Agent 工具发现

`GET /v1/tools` 返回可直接转换为常见 function calling 格式的工具说明，包含：

- `name`、`title`、`description`；
- JSON Schema `inputSchema` 和 `outputSchema`；
- 只读、非破坏性、幂等等 annotations；
- REST 与 MCP 入口位置。

工具描述明确列出股票、指数、行业、板块、基金、债券、热点以及各类金融业务数据，
Agent 可以先阅读 description 决定是否调用，不需要预先知道内部 `subject/dataview/API DSL`。

## MCP Streamable HTTP

MCP 地址：

```text
https://finance-api.example.com/mcp
```

服务使用官方 Python SDK 的 Streamable HTTP transport，支持 `initialize`、`tools/list` 和
`tools/call`。`tools/list` 暴露一个高层工具 `finance_data_query`；工具返回同时包含
`content` JSON 文本和 `structuredContent`，兼容只识别文本结果的旧客户端与直接消费
结构化输出的新客户端。

私有部署当前按需求采用 Bearer API Key。标准 MCP HTTP 授权框架面向通用公网客户端时
推荐 OAuth 2.1；未来如果需要让任意第三方 MCP 客户端完成动态授权，可只在入口层增加
OAuth 资源服务器，不需要修改金融工具与 Provider。

MCP transport 启用了 Host/Origin 校验。生产部署必须把实际域名写入：

```bash
FINANCE_API_ALLOWED_HOSTS='finance-api.example.com,finance-api.example.com:443'
FINANCE_API_ALLOWED_ORIGINS='https://agent.example.com'
```

## 数据说明页面

访问 `/data-map`。页面实时读取只用于页面展示的 `/data-map/catalog.json`，以
“金融数据 → Subject → 业务域 → Dataview → 数据范围”的层级展示当前目录；支持点击展开、
收起、拖动和平滑缩放。新增 Dataview 后不需要复制完整页面数据，未配置的视图会自动进入
“其他”分组。

## 部署检查

```bash
curl https://finance-api.example.com/health

curl https://finance-api.example.com/v1/tools \
  -H "Authorization: Bearer $FINANCE_API_KEY"
```

上线前至少确认：

1. `KINGDOMAI_DB_URL` 指向服务器可访问的 `kingdomai` 数据库；
2. DSH binary、Python SDK、Node 路径与 DeepSeek Key 已配置；
3. `FINANCE_DSH_FINANCIAL_QA_ENABLED=1`；
4. API Key 来自服务器 Secret Manager，而不是提交到 Git；
5. 外层 HTTPS、访问日志脱敏、请求超时和并发限制已生效；
6. `/health` 的 Catalog revision 与评测时保存的 revision 一致。
