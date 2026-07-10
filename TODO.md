# TODO

当前迁移基线以功能保持和独立运行优先。以下问题留待后续分模块处理，不在本次迁移提交中展开。

## P0 - Independent Baseline

- 校准 `requirements.txt`：当前版本与实际代码存在漂移，至少包括 OpenAI 新版客户端以及 Flask、pandas、PyMySQL、FastAPI 等直接依赖。
- 补充真实环境验证：现阶段通过的是单元测试、mock 路径和 HTTP smoke test，真实 LLM、数据库、搜索 provider 尚未在新仓库配置下联调。
- 明确生产启动方式：当前使用 Flask development server，后续补充正式 WSGI 启动和环境配置说明。

## P1 - Architecture Cleanup

- 将 `src/experiments/staged_data_protocol` 中已成为正式能力的金融查询协议迁移到稳定包路径，并保持现有接口和效果不变。
- 拆分体量较大的 `src/web/flask_app.py`，按页面、API 和运行时入口组织；先补路由契约测试，再做机械拆分。
- 收敛 `src/utils/mysql_utils.py`：当前仍混合多类数据库访问和历史热点方法，逐步由各 provider 自己管理数据访问。
- 清理未公开但仍残留的 `hotspot_trace` 兼容引用，确认热点问题统一进入 `finance_data_query` 的 `hot_event` 主体后再删除。
- 核对工具注册表中的遗留工具，只保留当前三类顶层工具及仍被正式流程引用的底层实现。

## P2 - Product Verification

- 为对话主流程补端到端测试：上下文承接、工具选择、session variable、结果渲染和自定义工具 create/confirm/commit。
- 清理热点主题的演示页面和示例文案，使其变为通用金融分析展示样例。
- 复核迁移文档、环境变量和运行时目录，形成新仓库独立开发基线。
