# Fin Agent

独立的对话型金融 Agent。项目由 `stock_agent` 的 V2 对话、规划、工具与运行时能力迁移而来，不包含原热点生产、调度和时间线链路。

## Included

- 对话工作台与 Flask API
- 对话上下文、planner、runtime loop 和 session variables
- 金融数据查询、文件 IO、搜索和自定义工具
- Tool Studio、Skill Studio 与结构化结果渲染
- 金融数据协议及 provider 适配层

金融数据协议中的 `hot_event` 仍作为通用查询主体保留；它不等同于旧的 `hotspot_trace` 生产链路。

## Run

```bash
python -m pytest -q
FIN_AGENT_PORT=22053 python -m src.web.flask_app
```

浏览器访问 `http://127.0.0.1:22053`。真实 LLM、数据库和搜索能力使用本机环境变量配置；源仓库的 `.env` 与运行时数据未迁移。

迁移来源和边界见 [MIGRATION.md](MIGRATION.md)。
