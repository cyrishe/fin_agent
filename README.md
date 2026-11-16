# Fin Agent · 研报问答

本分支提供结构化研报问答与数据查询，保留 REST API 和 MCP Streamable HTTP。数据范围为研报明细及年度预测指标，支持按报告、机构、评级等条件查询和聚合，并保留来源及对话上下文。

## 启动

```bash
pip install -r requirements-finance-api.txt
python -m src.finance_api.app
```

参照 [研报部署说明](docs/report_only_deployment.md) 配置数据库、三张表、模型、API key 和 DSH。默认服务地址为 `http://127.0.0.1:22100`；API 文档为 `/docs`，MCP 入口为 `/mcp`。DSH 和 CC 是可选运行时，推荐按照部署环境选择并验证。

## 验证

```bash
pip install pytest
python -m pytest -q
node --test tests/dsh_finance_loop_policy.test.mjs
```

真实模型和数据库评测需要由部署者提供授权环境，测试说明见 [研报评测](docs/finance_mcp_evaluation.md)。历史运行报告不能替代当前版本和环境的验证。
