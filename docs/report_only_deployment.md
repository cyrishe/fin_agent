# 研报专用 API / MCP 部署

此分支的客户部署入口是独立服务 `python -m src.finance_api.app`。保留原有 REST 问答、结构化数据查询和 MCP Streamable HTTP：`POST /v1/finance/answer`、`POST /v1/finance/task`、`POST /v1/finance/query`，以及 `/mcp` 中的 `finance_task`、`finance_data_query`、`list_skills`。`conversation_id` 和原有认证协议保持不变。API key 通过 Bearer 或 `X-API-Key` 提供，MCP 允许的 Host/Origin 需配置为实际客户端使用的地址。

数据目录仅开放 `stock.report`、`stock.report_metric` 的明细和聚合，方法目录仅开放 `equity-report-analysis`。问答不再暴露行情、证券身份表、搜索、看图、回测或后台任务工具；无法由研报支持的内容应说明证据不足。本分支以独立研报服务作为部署入口。

## 数据库配置

以 [环境变量样例](../deploy/finance-api/report-only.env.example) 为起点设置独立 MySQL 连接和表名：

- `FINANCE_REPORT_DB_URL`：`mysql+pymysql://用户:URL编码密码@主机:端口/库名`，用于研报连接。没有设置时沿用 `KINGDOMAI_DB_*` 的连接方式，数据库名为 `FINANCE_REPORT_DB_NAME`（默认 `kingdomai`）。建议现场显式设置专用 URL，使用只读账号。
- `FINANCE_REPORT_TABLE`：研报主表，默认 `reports`。
- `FINANCE_REPORT_METRIC_FACT_TABLE`：预测指标事实表，默认 `metric_fact`。
- `FINANCE_REPORT_METRIC_DEF_TABLE`：指标定义表，默认 `metric_def`。

表名仅接受简单 MySQL 标识符；库名通过连接指定。改表名不会自动适配字段或迁移数据：客户三表的列、关联关系及 MySQL 功能必须与 [report_provider.py](../src/experiments/staged_data_protocol/phase2/report_provider.py) 的字段、`report_id` 和 `metric_id` 关联一致。预测指标查询连接三表；研报明细只读取主表。运行服务的本地 `data/`、`outputs/` 保存会话和结果，需按客户数据要求设置权限、保留期限和备份。

默认不运行原市场数据状态监控，也不写系统库用量统计；如确需用量统计，须另行准备系统库和 `aiia_request_usage` 表并设置 `FINANCE_API_USAGE_ENABLED=1`。托管访问令牌依赖系统库；单独配置 `FINANCE_API_KEY` 或 `FINANCE_API_KEYS_JSON` 可使用静态 API key，不要求托管令牌库。模型服务及 DSH/CC 运行时是独立依赖；表和连接配置不包含模型凭据或运行时安装。

DSH 标准问答可设置 `FINANCE_DSH_REPORT_CATALOG_PRELOAD=1`，从当前目录生成并在本轮预装研报明细及指标执行包，减少首次目录选择的模型往返；目录版本不一致时停止本轮，查询仍经过原有验证和修复。默认关闭，设置 `0` 可回退。仅数据模式、快速模式及 CC 不使用此优化；预装会增加首次模型输入，效果与耗时需在目标环境复测。

## 启动与验证

安装 `requirements-finance-api.txt`、配置环境及选用的 DSH 或 CC 运行时后，在仓库根目录启动：

```bash
python -m src.finance_api.app
```

用 `/health` 检查进程，用带认证的 `/v1/finance/catalog` 和 MCP `tools/list` 检查入口，再以客户授权的脱敏样本验证研报明细、预测指标、聚合、零结果及多轮对话。完整模型链路、客户库的字段映射、并发和恢复能力必须在目标环境独立验证；本地模拟测试不代表客户现场已就绪。参考通用运行参数见 [独立金融数据 API](finance_api_service.md)，与本页冲突时以本页的研报范围为准。
