# Fin Agent

独立的对话型金融 Agent。项目由 `stock_agent` 的 V2 对话、规划、工具与运行时能力迁移而来，不包含原热点生产、调度和时间线链路。

## Included

- 对话工作台与 Flask API
- 对话上下文、planner、runtime loop 和 session variables
- 金融数据查询、文件 IO、搜索和自定义工具
- Tool Studio、Skill Studio 与结构化结果渲染
- 用户 scope 内的自然语言定时任务、持久化运行记录与独立 worker
- 金融数据协议及 provider 适配层

金融数据协议中的 `hot_event` 仍作为通用查询主体保留；它不等同于旧的 `hotspot_trace` 生产链路。

## Agent Core providers

普通问答和轻量语义路由继续使用基础 LLM。自定义工具的 Design/Coding 等慢思考阶段通过统一 harness 协议选择 Codex 或 Claude Agent SDK：

```python
from src.services.agent_providers import (
    AgentCapabilityPolicy,
    WebSearchPolicy,
    build_agent_skill_harness,
)

harness = build_agent_skill_harness(
    "claude",  # 或 "codex"
    cwd=".",
    complexity="fast",
    capabilities=AgentCapabilityPolicy(web_search=WebSearchPolicy.DISABLED),
)
result = harness.run_skill(
    skill_path="src/skills/financial-tool-development/SKILL.md",
    output_schema_path="src/skills/financial-tool-development/schema.json",
    user_request="设计一个金融工具",
    stage="design",
)
```

生产主线用 `CUSTOM_TOOL_AGENT_PROVIDER=codex|claude` 切换，并可用 `fastest|fast|mid|high` 四级 profile 统一选择模型与推理强度。Web Search 与 MCP 是独立 capability 参数，不随 profile 隐式开启。两者共享同一份 Skill、上下文资料包和业务 Output Schema；provider 的事件、权限、凭据和超时只在 adapter 内处理。Claude provider 默认通过 DashScope Anthropic 入口使用 `deepseek-v4-flash`，配置见 [.env.example](.env.example)，完整映射与边界见 [Claude Agent SDK 主线接入](docs/claude_agent_sdk_integration.md)。

Codex SDK 支持本地订阅与服务端 CRS API Key 两种运行方式。默认 `STOCK_AGENT_CODEX_AUTH_MODE=auto`：当 `CODEX_CRS_API_KEY` 非空时使用 CRS，否则复用本机 ChatGPT 订阅登录。也可以显式设置 `subscription` 或 `crs_api_key`，显式模式优先于自动检测。CRS 模式为每个 Agent session 生成隔离的 `CODEX_HOME/config.toml`，Key 只通过环境变量传入，不写进配置文件；部署字段见 [.env.example](.env.example)。

## Run

```bash
python -m pytest -q
FIN_AGENT_PORT=22053 python -m src.web.flask_app
```

浏览器访问 `http://127.0.0.1:22053`。真实 LLM、数据库和搜索能力使用本机环境变量配置；源仓库的 `.env` 与运行时数据未迁移。

独立金融数据服务使用同一套 Catalog、Provider、静态校验和 CC/DSH
运行时，但不经过通用聊天路由：

```bash
FINANCE_API_KEY='replace-with-a-random-key-at-least-24-characters' \
FINANCE_DSH_FINANCIAL_QA_ENABLED=1 \
python -m src.finance_api.app
```

默认监听 `http://0.0.0.0:22100`。数据体系页面位于 `/data-map`，
OpenAPI 文档位于 `/docs`，REST 查询入口为 `/v1/finance/query`，
MCP Streamable HTTP 入口为 `/mcp`。部署和调用说明见
[独立金融数据 API](docs/finance_api_service.md)，服务器凭证可从
[部署环境模板](deploy/finance-api/.env.example) 开始填写。

迁移来源和边界见 [MIGRATION.md](MIGRATION.md)。
定时任务建表、API 与 worker 启动方式见 [docs/scheduled_task_runtime.md](docs/scheduled_task_runtime.md)。
公开主页、手机号唯一账户、阿里云短信持有权验证、可选实名增强与建表方式见 [docs/phone_account_auth.md](docs/phone_account_auth.md)。
