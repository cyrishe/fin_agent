# MCP 金融 Skill 与通用任务接入（2026-09-10）

## 本轮范围

用户确认：提供 MCP 指定 Skill、指定金融数据工具与通用自动选择能力；不包括工具工坊自定义工具。
本轮为本地开发，未提交、推送或部署。工作区另有前一轮多 Skill UI、预算收尾、参考数据折叠与追问推荐改动，保留未覆盖。

沿用 SkillHub 管理资产、FinancialQaCcService 执行任务、REST/MCP 适配接入的职责划分。
没有增加独立 Skill 执行器、路由 LLM、业务状态机或逐 Skill 工作流。

## 已实现

- MCP `finance_task(query, skill_ids?, ...)`：省略/null/空列表自动选择，显式列表保留首次出现的顺序；默认 standard 执行、auto 分析、both 返回。
- MCP `finance_data_query` 保留直接查数入口与既有参数。
- MCP `list_skills`：读取当前身份的已发布方法目录，返回 ID、名称、用途、版本与目录 revision；不输出方法正文、资源内容、owner 或内部文件路径。
- REST `/v1/finance/task`、`/v1/skills`；原 `/v1/finance/answer` 增加可选 `skill_ids`，原参数默认值兼容。
- `/v1/tools` 直接投影已注册 MCP Schema，避免描述与真实调用参数分别维护。
- 默认网关实例连接现有 SkillHub 发布目录。`FINANCE_API_OWNER_IDS_JSON` 是服务端授权映射；未映射身份仍为 `finance-api:<principal_id>`。
- 目录与执行沿用同一 owner 权限。会话保持 principal 隔离；有 owner 绑定时用无歧义的序列编码计算 session，重绑定不复用前一 owner 的上下文；未映射身份保留旧 session 寻址方式。
- 显式不存在/停用/未授权方法抛出现有 ValueError 的专用子类 `FinanceSkillUnavailableError`，使 REST 能返回 422，MCP 返回 `isError`，不再把用户选择错误误报为服务内部 500。
- `detail` 增加实际方法加载证据、目录 revision 和目录降级说明；正式回答继续使用既有 summary/data/data_sources。

MCP `tools/call` 仍然必须指定入口工具的 `name`。自动选择发生在 `finance_task` 调用后的金融 Agent 内部，不是 MCP 协议本身执行推理。
多个 Skill 在同一 Agent 任务中指导分析，加载证据不等于各方法的业务结论已经验证。

## 验证

协议测试入口：`tests/test_finance_api_skills.py`，覆盖两种传输、自动/显式、顺序与兼容输入、私有权限、发布可见性变化、目录降级、伪造 owner、错误结果和会话隔离。

运行命令：

```bash
.venv/bin/python -m pytest -q tests/test_finance_api_skills.py tests/test_finance_api_app.py tests/test_finance_api_gateway.py tests/test_finance_api_auth.py tests/test_request_usage_service.py tests/test_finance_skill_first_integration.py tests/test_business_skill_explicit_invocation.py tests/test_skill_hub_catalog_service.py
.venv/bin/python -m pytest -q
```

最终测试计数见本文件末尾与 `outputs/mcp_skills_20260910/verification/`。

真实调用采用 FastAPI TestClient 发送 MCP initialize、tools/call，再经真实网关与 DSH 访问金融只读 Provider。
使用随机本地 API Key、内存 SkillHub、独立 owner 和运行目录，未写系统数据库或线上用量，未启动生产服务。

| 样本 | 入口 | 结果 | 耗时 |
|---|---|---|---|
| 最近五个交易日行情 | finance_data_query / data | 返回 5 行；2 次模型响应 | 15.91 秒 |
| 盈利与估值自动分析 | finance_task / 不传 skill_ids | 自主采用数据工具，返回 3 行；4 次模型响应 | 22.86 秒 |
| 盈利与估值显式双方法 | finance_task / earnings-analysis → valuation-analysis | 实际加载两个方法，返回 4 行，综合回答；5 次模型响应 | 23.47 秒 |
| 分红可持续性自动分析 | finance_task / 不传 skill_ids | 自主加载 dividend-analysis，返回 15 行；4 次模型响应 | 45.07 秒 |

前 3 例位于 `outputs/mcp_skills_20260910/`，分红例位于 `outputs/mcp_skills_auto_20260910/`。
这些是开发过程中的接入冒烟样本，不是生产效果或性能验收；源码随后有会话绑定编码的局部调整，由最终协议测试覆盖。

## 已知效果边界与接手事项

- 自动选择允许不加载 Skill。第一个分析样本选择通用数据路径，分红样本则选择了专业方法；不承诺每个分析问题必定加载方法或语义选择必定最优。
- 人工阅读真实答案发现业务质量问题：无历史比较证据的估值判断；分红表中单位换算与正文不一致；将投资现金流净额近似资本开支后的表达过强。记录为业务 Skill/效果评测事项，本轮没有加入关键词分支、硬校验或改写金融方法。
- 真实样本均用内存目录；已发布私有资产的授权和可见性由离线真实 Registry/Snapshot 协议测试验证，未访问生产私有资产。
- 服务端绑定默认 `{}`。要让某个 API 凭证使用网页用户的私有方法，部署 Owner 需要配置明确授权的 principal → user ID 映射。
- 使用说明已更新到 `docs/finance_api_service.md`、`/mcp-guide` 与部署环境模板；现有客户端重新 tools/list 后可发现新增入口。
- 当前仅为本地实现。生产重启与部署仍按仓库人工 Gate 执行；运行维证据未完成，不能据此宣称生产放行。

## 最终检查结果

- 最终相关 Python 回归：**74 passed**。
- 全量 Python 回归：**1923 passed / 27 skipped / 7 failed**。7 项与上一轮已经在干净基线复现的失败项一致；本次没有宣称默认质量门通过。
- `git diff --check` 通过。
- 全量测试之后的会话编码局部调整及新增错误响应测试，由最终 74 项相关回归覆盖。

既有失败项：

```text
tests/test_custom_tool_service.py::test_coding_subject_asset_expands_method_contracts
tests/test_financial_qa_cc_scenario.py::test_financial_qa_prompt_keeps_business_rules_and_manual_stays_generic
tests/test_phase2_engine_loop_feedback.py::test_margin_context_exposes_base_and_kday_api
tests/test_phase2_engine_loop_feedback.py::test_finance_catalog_context_exposes_verified_field_units
tests/test_realtime_quote_provider.py::test_query_alias_is_not_registered_and_navigation_shows_real_entry
tests/test_tool_studio_service.py::test_tool_studio_service_loads_finance_data_catalog_tree
tests/test_tool_studio_service.py::test_tool_studio_service_saves_one_finance_catalog_path
```

基线对照记录：`outputs/framework_experience_20260909/verification/baseline_failures.log`。
本轮日志与最终源码 hash：`outputs/mcp_skills_20260910/verification/`。
