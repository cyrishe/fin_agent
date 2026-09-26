# 金融数据方法统一：显式 query 与成分关系 dataview

日期：2026-09-08。范围：当前工作区的协议、目录及接入适配；未提交、未部署。本轮没有调用模型或生产数据库。

## 1. 决策

`constitution` 表示成分关系数据，应作为 dataview；`query`、聚合、窗口与动态计算表示对数据执行的方法。

原结构把两件事混在一起：普通明细调用省略 query，成分关系又同时作为 dataview 和 OP。导航中出现 `operations: ["query"]` 是正常的分类信息，但此前实际入口却是 `stock.basic_info(...)`，目录语义与调用形式不一致。现在正式采用显式 `.query(...)`，不再把它当作需要纠正的错误后缀。

| 数据范围 | 明细入口 | 聚合入口示例 |
|---|---|---|
| 个股研报 | `stock.report.query(...)` | `stock.report.agg(...)` |
| 研报指标 | `stock.report_metric.query(...)` | `stock.report_metric.agg(...)` |
| 行业成分关系 | `industry.constitution.query(...)` | `industry.constitution.agg(...)` |
| 板块成分关系 | `plate.constitution.query(...)` | `plate.constitution.agg(...)` |

通用调用形式：

```text
subject.dataview.query(...)
subject.dataview.agg(...)
subject.dataview.kd_<field>_<method>(...)
subject.dataview.dynamic_cal(...)
```

窗口、聚合和动态计算仍使用原有实现与参数。目录的四个方法类别为 `query / window / aggregate / compute`；分别对应上述调用形式。`constitution` 不再是模型可选的 OP。

本次是统一命名和方法分类，不是新建所有 dataview 与 OP 的笛卡尔积，也没有新增哪个 dataview “不该做哪个 OP”的业务限制或提示。当前目录继续反映实际实现；业务选取由模型依据问题完成，成分关联的复杂性仍由原 Provider 负责。

## 2. 实现范围

最终 catalog 版本：`2026-09-08-unified-operations-v5`。

内容修订：`0d5903c648964a0f601f49f15185b1773b99f23e33cdafaa9242d985a25c6321`。

实际盘点：7 个 subject，31 个 dataview，47 个方法。其中明细 31、窗口 9、聚合 6、动态计算 1。方法数量未因改名增加。

- 31 个明细方法的 `api_name`、调用模板及目录示例统一为 `.query(...)`。
- `industry/index/plate.constitution` 与 `hot_event.member` 使用普通 query 分类，关系数据的字段和 Provider 保留。
- 解析与静态检查共用同一入口匹配函数，旧两段入口是新 query 方法的兼容输入；不增加运行时正则纠错层。
- 旧调用保留：例如 `stock.report(...)`、`industry.constitution(...)` 与新入口分派到相同 Provider，参数和金融计算不变。聚合里的指标引用仍是 `stock.quote.close`，不是方法调用，无需改名。
- 目录编辑器接受旧入口名时仍保留现有 canonical 方法、示例与说明，避免改名导致说明丢失。
- 行情/研报展示按数据视图分类，兼容显式 query 后缀；正式执行记录仍保留实际 API 名称。
- 更新问答、Coding 调用说明及解析失败的通用反馈，清理省略 query 的旧协议表述。
- 恢复数据目录/执行阶段的核心指引，Skill 说明补充取证与分析用途，不再替换数据阶段职责。

旧 Python 目录调用的 `operation="constitution"` 在服务内兼容为 query；当前 MCP 工具 Schema 只公开四个规范类别。保留历史执行串，不向新模型同时展示两套分类。冻结的 CC 基准和历史评测记录未改动。

主要代码：

- `src/tools/finance_data/catalog/api_view_catalog.json`
- `src/experiments/staged_data_protocol/phase2/catalog.py`
- `src/experiments/staged_data_protocol/phase2/call_structure.py`
- `src/services/finance_data_tool_catalog_service.py`
- `src/scenarios/financial_qa/dsh_loop_policy.mjs`
- `src/scenarios/financial_qa/presentation.py`
- `src/scenarios/financial_qa/finance_api_protocol.md`
- `src/prompts/codex/finance_api_call.system.md`

## 3. 叶子契约约束：核查与建议，尚未实施新拦截

目前 Harness 注册的是通用 `read_finance_catalog` 与 `finance_query` 等工具。业务 API 放在 request 字符串中，不是每个叶子各注册一个原生工具。因此框架不会自动知道 report 的执行包不能代表 basic_info 的契约已加载。

原生能力已经具备：

- `agent.ctx.tools.restrict()`：控制原生工具可见性。
- `agent.ctx.tools.guard()`：执行前检查。
- `agent.ctx.tools.register()`：按 agent 注册工具，但把所有业务叶子改成独立原生工具，会牵动当前 flow、结果引用和工具 Schema，改动明显更大。

当前最贴合的做法是复用现有 guard 和目录历史：**检查本次请求的具体方法契约是否已在模型上下文中可用**。这只是加载前提，不判断用户是否应该选择某种金融方法，不增加 dataview × OP 能力规则表。

实施时需要保持三个边界：

1. 从实际可见且版本有效的目录返回提取契约；全 dataview 包、直接定位的单方法包和有效历史包均可使用，不强制重复翻页。
2. 以生成本次请求前可见的契约为准，不能把同一轮并行工具刚返回的目录假装成模型生成时已知的信息。
3. 接入现有阶段判定与计数路径，避免仅在末尾加拒绝，导致未取得契约的尝试误耗查询修复预算。

仅隐藏 finance_query 不能完整解决问题：加载一个 report 包后通用工具又会可见，仍须对应到请求目标。保持工具 Schema 稳定还是按阶段隐藏，应作为缓存与无效尝试的性能取舍单独评测。

本轮完成命名和阶段指引清理；没有声称上述具体契约门禁已经实现。历史现场与原生机制回放见 [Catalog 叶子契约根因核查](catalog_leaf_readiness_root_cause_20260908.md)。

## 4. 查找是否固定三阶段

不是。一个 `read_finance_catalog` 同时接受 subject、dataview、operation：

- 已明确：一次给出三者，直接获取当前方法执行包。
- 给出 subject + dataview、省略 operation：返回该视图的完整方法包。
- 尚未明确：空参数或仅 subject 浏览概览，再继续定位。

这是分层组织、按需读取，不是固定三次模型调用。

## 5. 验证边界

专项测试覆盖所有明细方法的新旧入口、实际路由和参数一致性；跨 CC SDK / DSH MCP 适配器的目录、四类方法、flow 结果引用；目录编辑、问答展示和 DSH Skill 阶段指引。

Provider 在本轮新增的全入口比较测试中被确定性替身替换，验证的是解析、校验和真实分派，不代表已测生产数据库或模型生成成功率。历史 `.query` 错误现在是正式协议入口，但这不等于历史题中的其他字段/参数错误自动消失。

最终专项回归：Python **575 passed、2 skipped**；DSH loop **41 passed**。两个跳过项是需要显式数据库凭据的 live provider 测试，本轮未注入凭据。`git diff --check` 通过。这是选定测试范围的结果，不是全仓库或生产验收结论。

新增 `tests/test_finance_explicit_query.py` 覆盖全量 31 个明细入口的新旧名称与分派一致性；跨运行时适配测试增加显式 query 和新命名的多步 flow。展示测试同时覆盖旧名与新名，并把既有 close 标签预期同步为当前目录已有的“收盘价/最新价”（HEAD 中已是该标签，本轮未改该业务字段）。
