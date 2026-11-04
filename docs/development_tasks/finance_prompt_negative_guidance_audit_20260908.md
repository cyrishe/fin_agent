# 金融查询提示词审阅：正向规约与反向提示清单

日期：2026-09-08

## 本次已完成

这是 SOFT 目录说明调整；API 路径、参数、解析、SQL、静态检查和执行流程均未修改。

1. `stock.report_metric` 的 guidance 改为：

   > 提供逐机构、逐公司原始指标值，用于明细对比与排序。

2. `stock.report_metric.agg` 删除整条“均值、中位数……；逐机构或逐公司……仍使用明细查询”。聚合方法已有“对匹配的研报指标事实做标准聚合；按公司分组时可扫描公司”的用途说明，继续使用即可，无需再教学。
3. “依靠基模原生理解、必要说明正向且局部、各方法说明自身职责”已写入 [项目约定](/Volumes/ext/fin_agent/AGENTS.md:40)。
4. 本地目录投影测试：`tests/test_finance_data_tool_catalog_snapshot.py`，20 项通过；新增测试确认明细指导只出现在明细执行包，聚合包继续提供正确的兄弟入口导航。

其余条目本次只 review 并列出，尚未批量修改；没有提交、推送或更新服务器，也没有重跑 MCP 模型评测。目录测试通过不能代表已经解决 `.query` 生成错误。

## 我的判断

问题不只是句子里用了“不”。目前更核心的是：普通能力被反复教学、一个方法替另一个方法指路、同一规约在多个层次复制，以及共享工具夹带了不同模式的业务策略。逐句改成正面但保持同样长度和控制粒度，仍然没有解决问题。

后续整理应以“先减掉多余教学与越界指路，再保留必要的正向定义”为主。通用聚合能力交给模型；本框架特有的 API 签名、字段含义、特殊窗口计算和恢复事实清楚提供即可。此判断来自当前源码结构；哪些条目实际导致某次错误，还需要对应请求的上下文或受控对照验证。

## 优先审阅列表

这里将同类问题合并，便于先决定方向；逐项原文、每一个重复位置和意见都在后面的完整清单。

| 项目 | 当前原文 / 现象 | 位置 | 判断 / 处理方向 |
|---|---|---|---|
| 聚合说明指导明细选择 | “逐机构或逐公司原始值的对比、排序仍使用明细查询。” | [研报指标明细](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:1642) | 已改：明细用途归明细；删除聚合侧整条重复教学。 |
| 操作类型教学过密 | “not by words such as compare…Top N”“default to query” | [operation 参数](/Volumes/ext/fin_agent/src/scenarios/financial_qa/tools.py:52) | 只定义四类操作的职责；清理关键词反例和泛化默认偏向。不是把这些否定句逐一翻成另一套长教程。 |
| 最新记录被当作错误用法教学 | “latest 不是聚合方法” | [共享类](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:203)、[report guidance](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:1473) | 两处重复；清理反例。该用法属于明细日期/排序能力。 |
| 聚合中出现虚构 API | “不新增 report.scan” | [report aggregate](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:225) | 保留按公司分组可扫描公司的正向用途；删去虚构路径。 |
| 数据层职责反复用拒绝描述 | “数据层不判断观点一致性”“不产出一致性、离散度或投资结论” | [研报统计](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:227)、[指标统计](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:278)、[视图 guidance](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:1486) | 说明提供哪些明细/统计供后续分析即可。“离散度”与投资结论混列也不准确，应以实际可用统计方法为准。 |
| 模型层出现开发裁判规则 | “不在此处做业务裁判”及保留未列出的 kd 方法名 | [融资窗口](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:1748)、[共享类](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:342) | 开发约定与调用说明分开。未注册方法文字与执行契约的张力需先核对，不能继续加反例补丁。 |
| 百分位的具体错例 | “不是 limit = 10” | [估值示例 note](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:949) | 清理错例；如有本地特性，只说明 percentile 的数值范围与方向。 |
| 日期的具体错例 | “不要把最近报告期写成 report_period = -1” | [业务构成](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:3167) | 清理错例，保留日期字段与 interchange_code 编码的正向定义。 |
| 旧语法被不断提及 | “不使用 realtime”“不要…扩大 LIKE 范围” | [行情](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:650)、[板块](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:3561) | 参数表与当前模糊匹配协议说明真实能力；清理旧名词提醒。 |
| 共享工具内部指令自相冲突 | 前段 Do not add … explanatory query；后段 continue to a genuinely different explanatory goal | [finance_query](/Volumes/ext/fin_agent/src/scenarios/financial_qa/tools.py:759) | 先统一取数与回答增强的职责范围。此处还残留“confirmation, not context; (2)”断句，需清理拼接残留。 |
| CC 与工具对额外查询的要求冲突 | CC：单点结果默认再查一份相关数据；工具/DSH：不要为丰富回答添加比较目标 | [CC](/Volumes/ext/fin_agent/src/scenarios/financial_qa/system.md:36)、[DSH](/Volumes/ext/fin_agent/src/scenarios/financial_qa/dsh_loop_policy.mjs:69) | 两者可作为不同模式策略，但共享工具不应同时夹带互斥策略。先明确模式职责，再统一最小说明。 |
| 阶段提示重复罗列禁止动作 | “不改目标、不换 API…”“不得检查、重试、翻页…” | [repair](/Volumes/ext/fin_agent/src/scenarios/financial_qa/dsh_loop_policy.mjs:71)、[fast final](/Volumes/ext/fin_agent/src/scenarios/financial_qa/dsh_loop_policy.mjs:84) | 保留真实可用动作与预算即可；流程控制由系统承担，提示正向说明本阶段贡献。 |
| 空结果 guidance 过度限制 | “不得放宽…换 API…拆分查询”“不换 API…或查询原始明细” | [零行](/Volumes/ext/fin_agent/src/scenarios/financial_qa/result_registry.py:258)、[缺值](/Volumes/ext/fin_agent/src/scenarios/financial_qa/result_registry.py:265) | 保留零行/缺值、已执行条件和可修正错误。对后续取数的判断围绕未完成目标，避免一串禁令误伤合理组合。 |
| 同一空值与重试规约多层复制 | 不补值、不换 API、不追非空、只修一次 | [常驻](/Volumes/ext/fin_agent/src/scenarios/financial_qa/dsh_system.md:32)、[阶段](/Volumes/ext/fin_agent/src/scenarios/financial_qa/dsh_loop_policy.mjs:75)、[恢复](/Volumes/ext/fin_agent/src/scenarios/financial_qa/query_recovery.py:56) | 统一维护证据与恢复规则，阶段只提供必要当前上下文；保留真实安全/恢复约束。 |
| 11 个 Skill 的排除式入口 | “只查询…时不使用” | [业务 Skill 目录](/Volumes/ext/fin_agent/src/skills/finance-business/catalog.json:8) | 全部 11 项及各自 SKILL.md 的原文在清单逐项列出；入口只说明最适合的业务任务与产出。 |
| Skill 中大量通用负向说明 | 不填零、不套框架、不重复查询、不平铺、不补篇幅等 | [个股研究示例](/Volumes/ext/fin_agent/src/skills/finance-business/skills/stock-research/SKILL.md:19) | 通用部分与主提示去重；业务方法保留正向研究用途、特殊口径和交付要求。 |
| 真正需要保留的特殊口径 | kd_pct_sum 不是每日 pct 求和；HB 累计值；实时快照/已完成 K；安全上限 | [窗口](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:76)、[财报](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:1283) | 含义保留，优先用公式、字段含义、时间范围和真实执行结果正向描述。不能因为含“不”字就删掉。 |

## 完整清单与范围

[打开全部逐项原文、位置与审阅意见](/Volumes/ext/fin_agent/docs/development_tasks/finance_prompt_negative_guidance_inventory_20260908.md)。

盘点覆盖 API 总目录全部 subject/view 和共享方法类、CC/DSH 查询主链路、阶段提示、工具描述、结果与恢复 guidance，以及 11 个金融业务 Skill 的入口、正文和参考。目录中的示例 `note:` 也包括在内，因为装载时会并入模型看到的 `guidance`。

清单按当前源码条目计：API 目录 53 项、查询主链路 106 项、业务 Skill 258 项；另列内部动态计算安全提示 1 项、MCP 参数对照说明 2 项、相邻工具开发分支 19 项，共 439 项。这个数字包含正常的口径/安全/对照说明和重复位置，**不是 439 个错误，也不是一个请求的提示数量**。本次已改的那条单独记录在上方，未混入待审清单。

边界说明：这是本地项目维护的金融查询提示盘点，不包括供应商内置提示、数据库个人自定义提示，以及所有历史实验分支。相邻工具开发提示单独列示，未误当成金融问答常驻提示。作者归属不据当前混合工作区臆断。

## 来源与装载定位

- guidance 来自静态 JSON；[目录投影](/Volumes/ext/fin_agent/src/services/finance_data_tool_catalog_service.py:489)把该列表和示例 `note:` 合并给模型。本次被指出的句子是静态维护内容，不是查询时模型自己生成。
- [CC 装载入口](/Volumes/ext/fin_agent/src/scenarios/financial_qa/service.py:148)加载 financial_qa/system.md，并追加 finance_api_protocol.md 与 data_query.md。共享工具及结果 guidance 另行进入上下文。
- [DSH 装载入口](/Volumes/ext/fin_agent/src/scenarios/financial_qa/dsh_service.py:620)读取 dsh_system.md，dsh_loop_policy.mjs 按阶段注入提示。业务 Skill 不是全部默认注入 DSH 请求。

结论：本次明确的错位说明已改。其他清单先供共同审阅，优先处理操作类型过度教学、共享工具的策略冲突、以及结果 guidance 的过宽限制；真实业务口径和安全边界保持。
