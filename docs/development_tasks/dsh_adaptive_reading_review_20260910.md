# DSH 按任务选择数据读取范围：调研、实验与方案

日期：2026-09-10。Fin 基线 d830d45bb49816f4dc0c9e0c1d0376dbb75fb4b2；相邻 DSH 为 0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4。本轮只调研和实验，未修改运行代码或生产配置。DSH 既有 translator 未提交改动未触碰。

后续实施已记录于 [明细读取保真与按需选列](dsh_detail_reading_implementation_20260910.md)：保留完整明细页、成功分页不占失败额度、完善选列说明，并完成原生回放和真实模型合成案例验证。以下是实施前的调研基线。

## 结论

正确方向是让 Agent 在当前推理循环中根据目标和证据缺口选择数据，并使读取可恢复、计算可追溯。不是“数据不给模型”，也不是“全部数据都进模型”。需要分别讨论四件事：完整结果保存、程序覆盖哪些数据、模型阅读哪些数据、界面展示哪些数据。

**全量统计不等于全文阅读；全文阅读不等于所有列；覆盖全部相关内容不等于每一轮都携带全部历史。**

DSH 有所需的工具执行、结果投影、PTC、spill 和压缩扩展点，但在已核实实现中没有自动理解金融目标、可靠判断“哪些列需要全读”的现成控制器。语义判断仍由模型/Skill 负责；框架应提供范围选择、全量程序计算、真实读取覆盖及可恢复引用。前一轮 d830d45 只解决了为界面展示而重复读取的问题，不等于完整的数据分析上下文方案。

## 当前实现到底具备什么

| 能力 | 已核实状态 | 实际边界 |
|---|---|---|
| 完整结果外置 | SessionVariableStoreService 保存完整数据，模型拿 schema、row_count、样本和result_ref | 默认是前3行，不是统计学上的随机或分层抽样 |
| 按列读取 | load_finance_result.columns 已实现，投影在模型返回前执行 | 参数缺少用途说明；模型可以省略，从而返回本页所有列 |
| 按范围读取 | 同工具支持filter/order/offset/limit，先对完整已存表筛选排序再分页 | 不是通用的聚合接口；没有随机/分层抽样参数 |
| 全量统计 | finance_query 可调用目录已有的.agg方法，agg协议支持sum/avg/min/max/median/count | 领域聚合不等于针对任意result_ref快照的通用计算；需保持对象范围和时点一致 |
| 数据覆盖事实 | schema、row_count、sample_complete、page.returned/has_more、shortened_fields及调用日志 | 行覆盖和字段文本覆盖是两件事；不能用has_more=false证明长字段已读完 |
| 列非空情况 | registry内部已有逐列populated_count；模型步骤摘要只给有值/无值列分类 | “有值列”只说明至少有值，不表示所有行都有值；不应把它当完整缺失率 |
| 结果体积控制 | Fin的post-execute投影按行数、字符数、单元格长度裁剪 | 是确定性的资源保护，不会理解哪一列对当前结论重要 |
| DSH原生PTC | tools支持native/ptc/both，run_code经程序调用工具，只有打印/返回结果进入主模型上下文 | 当前Fin组合仍为native，没有挂载codeRuntime |
| DSH原生spill | 可将超长文本外置，模型拿预览与可检索位置 | 是文本长度控制，不是按表格列语义选取；当前Fin未挂载，普通文件读取工具也被禁用 |
| DSH原生pruner/compaction | 前者在上下文压力时裁头尾，后者可经模型摘要压缩历史 | 不能替代首次取证选择；摘要有额外模型消耗和信息损失可能；当前最小组合未挂载 |

源码依据：Fin的tools.py/result_view.py/result_registry.py、SessionVariableStoreService、dsh_loop_policy.mjs、config/deepseek_harness/finance_query.patch.yml；DSH的tools/src/ptc.ts、mcp-client/src/tools.ts、sdk-minimal/cordis.patch.yml、agent-spine-demo/src/index.ts，以及spill-policy、compaction-tool-result-pruner、token-meter源码与说明。

## 什么时候看多少：由目标产生读取动作

以下是语义判断的示例，不建议编码成枚举状态、关键词分支或业务validator。

| 目标示例 | 合理覆盖 | 给模型什么 |
|---|---|---|
| 展示成员列表 | 系统保存并展示完整已返回结果 | schema、计数、范围、少量样本足以确认格式和大致对象；无需抄表 |
| 评级分布、均值、Top N | 程序在完整目标集合上计数/聚合/排序 | 聚合值、样本数/有效值数、来源、少量复核记录；不是让模型逐行心算 |
| 大致了解有哪些观点 | 根据目标选择初步样本，必要时扩展机构、时期、评级等范围 | 明确样本选择范围；前3行不能代表总体，也不能据此证明没有反例 |
| 对比明确的两家公司或两篇报告 | 完整读取相关对象的必要字段 | 身份、期间/单位/来源与实际比较指标或文本，其他列按需补充 |
| 逐篇研报找共识/分歧 | 所有相关报告的观点、风险等必要语义内容 | 可遍历所有行但只看相关文本和来源列；长字段要能继续读完 |
| 找出全部例外或证明没有遗漏 | 对完整相关范围进行可执行检查；纯语义条件可能仍需完整阅读 | 命中与未覆盖范围、可回查依据，不能把关键词过滤或抽样当语义穷尽证明 |

选择列应先看**完整列目录/含义与少量行样例**，不是只看“前几列”再假定其余列不重要。对需要引用的结果，保留能定位来源的字段；期间、单位、标识是否必要仍取决于任务。遇到新证据可以增列、扩行或读取全文，原始数据不因首次选择而丢失。

这件事不必新增一个“读取规划Agent”或额外的LLM分类请求：当前Agent本来就在决定下一次工具调用，可直接用columns/filter/order/offset/limit表达选择。自然语言目标和Skill负责语义，执行参数承载最小稳定契约，日志记录实际结果。无需让模型重复输出已由系统保存的整份数据计划。

## 实验一：选列与当前裁剪的真实关系

采用190行合成宽表，调用现有Python查询/存储/读取工具；再直接调用当前DSH策略的projectDetailPayload函数，未改源码。没有金融数据库或LLM调用。

| 同样请求前50行 | Python返回的payload字符数 | 经过DSH投影后实际可见行数 | 投影后payload字符数 |
|---|---:|---:|---:|
| 所有列，含无关长原文 | 262,786 | 4 | 14,894 |
| 代码、评级 | 2,330 | 50 | 2,274 |
| 代码、日期、投资要点 | 60,359 | 10 | 12,304 |

这是合成数据的字符计量，不是线上token或提速比例；Python与Node包装略不同，两个payload字符数不应直接当成同一编码的压缩率。结果证明列选择已经能明显改变上下文量，而且会改变每页实际可读的行数。

另一个单行长字段含24,013字符，当前投影只给2,401字符，并列入shortened_fields。重复读取同一行仍拿不到末尾证据；此时page.has_more=false，仅表示没有下一行。因此现有“保留原文引用”不等于“模型具备读取完整字段的路径”。

还有相关问题：当模型提前并行提交offset=0/50/100/150，而实际每次只投影4或10行时，中间行会未读。后续分页应依据实际page.returned；对未知大小的文本页应可继续读取，而不是提前假定每次都能看到请求的50行。普通模式2次load、Skill模式8次load也可能与真实全量语义阅读冲突，不能把达到调用上限等同完成。

证据：[Python读取](evidence/dsh_adaptive_reading_20260910/projection_probe.json)、[原生策略投影](evidence/dsh_adaptive_reading_20260910/native_projection_probe.json)。

## 实验二：模型能否自主选列

使用deepseek-v4-flash-0731和当前系统/工具说明，以合成190篇研报元数据进行取数后单步决策探针。不是完整DSH流程，未执行探针生成的后续调用。

- 当前说明下，“逐篇读投资要点和风险”选择了4次分页读取，但全部省略columns。这证明现成参数不等于模型会稳定使用。
- “评级分布统计”选择了聚合方向，但生成了不符合当前目录的stock.report.aggregate调用。探针未预载方法契约，也未运行守卫；它只能说明模型倾向程序聚合，不能证明实际调用成功。
- 仅在探针的columns Schema中增加通用用途说明：按schema/样本选择参与分析的列、全行遍历仍可选列、省略时为全列。两次模型响应都开始选列，但分别出现limit=200/190超过上限50；其中一次还尝试了不存在的aggregate操作。这不是可发布效果证据。

这4次真实模型调用合计16,964 tokens，所有业务数据均为合成样本。证据：[当前契约探针](evidence/dsh_adaptive_reading_20260910/decision_probe.json)、[选列说明候选探针](evidence/dsh_adaptive_reading_20260910/candidate_columns_probe.json)。候选说明没有写入运行代码。结论是**需完善可用能力和完整循环验证，不能仅加一句prompt就宣称智能选列已解决**。

## DSH原生能力如何落地

PTC是值得利用的方向：模型写一个小程序，程序访问工具/数据、遍历需要的范围、执行筛选和统计，只返回它选择的证据和结果。DSH的canonical value与model-facing content可分离；中间调用保留追踪，主模型不必逐次接收全部中间结果。[DSH工具说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md)、[工具协议](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/tools.md)。

但不是打开mode=ptc即可：

1. 当前Fin的MCP查询响应本身只包含预览和result_ref。DSH程序拿到canonical MCP value也不会凭空得到190行；仍需访问受授权的存储读取/计算能力。
2. 当前Fin限制了shell和编辑工具，未挂载codeRuntime。DSH现有worker-thread代码运行具有Node宿主级能力，官方明确它不构成安全隔离边界；不能把它当多用户金融数据的现成沙箱。[代码执行后端](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/code-runtime/code-runtime-worker-thread/README.md)。
3. Fin阶段推进与计数主要监听tool/call、step/end；PTC子调用走另一条记录通道。推断需要验证目录先读、预算、owner scope、取消和错误恢复在子调用上仍成立，不能宣称当前策略可直接兼容。
4. PTC解决程序计算和中间数据搬运；如果任务需要语义理解每篇长文本，仍需有模型实际读这些内容。把语义阅读转交其他模型也有token成本，并非零成本压缩。

spill适合可检索长文本的外置和回读，pruner/compaction适合后续历史压力管理；它们都不自动承担“哪些金融字段重要”的语义判断。[DSH spill](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/spill.md)、[DSH compaction](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/README.md)。

外部经验与此一致：保留轻量引用，让Agent按需获取信息；将筛选和计算留在执行环境，模型只接收所需结果，而不是每次把全部原始数据送入上下文。这是架构方向佐证，不是本项目收益数据。[按需上下文](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[MCP程序执行](https://www.anthropic.com/engineering/code-execution-with-mcp)。

## 推荐实施顺序

**第一步，补好现有读取能力而非再建一套控制器。** 为columns集中说明用途，保留模型自主决定；将已有列类型、覆盖和截断事实有效提供给它。列长度、缺失数等信息优先复用已算元数据，必要时按需计算，避免每轮默认生成一大份数据画像。初始样本标明是头部预览；需要探索时模型自行选范围，不把固定前3行当代表性抽样。

**第二步，消除“要求完整但工具读不到”的矛盾。** 投影预算在模型选列后按实际体积生效；尽量保持所选单元格完整并返回连续行的实际数量。超大单字段提供可定位的分段回读路径，复用result_ref及现有截断事实，不以永久截前缀代替完整读取。调用次数只作资源边界，完整性来自真实覆盖。范围与列选择记在已有调用/结果日志，不另建一套read_mode状态机。

**第三步，全量统计优先走现有聚合；确需已存快照组合计算时再接程序能力。** 已有领域.agg足够就复用；针对同一result_ref快照的反复统计、去重、分组等，可评估最小的已存结果计算入口，并复用当前聚合语法/执行器。若真实需求已超出这些运算，优先评估DSH原生PTC配套受约束运行环境，不逐个追加金融业务函数，也不直接放开宿主执行。

**第四步，用同一任务的不同规模和内容分布验证语义选择。** 至少覆盖：只展示、精确统计、少量对比、全篇语义、头部样本偏斜、后半段隐藏关键信息、长字段、稀疏列、必要新增列、缓存快照不变及引用权限。测最终答案/证据覆盖、实际选择列、模型读取字节和累计token、程序扫描量、重复读取与全过程耗时。不能只用无工具错误或“已完成”作通过标准。

本轮建议优先实施前两步并做完整DSH回放，再决定存储聚合/PTC的最小落点。不上额外分类模型，不按CPO/研报关键词分支，不把所有结果预先交给另一个LLM压缩，不以全局少读或关闭推理替代证据充分性。
