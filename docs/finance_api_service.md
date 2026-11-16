# 研报 API 与 MCP

本分支的独立服务、认证、数据库表、运行时及启动方式请参见 [研报部署说明](report_only_deployment.md)。

REST 入口：`/v1/finance/answer`、`/v1/finance/task`、`/v1/finance/query`；MCP Streamable HTTP 入口：`/mcp`，提供 `finance_task`、`finance_data_query`、`list_skills`。调用需提供 API key。`/v1/finance/catalog` 列出当前研报数据目录，`/health` 返回服务状态。
