# 研报 20 题：四道失败题的协议与上下文追溯

## 结论先行

这四题不能统称为“模型不会生成 API”。需要分开看：**初次生成错误、修复是否正确、系统是否真的执行修复**。

- `.query` 出现在 **1/20 题（5%）**，占四道失败题的 **1/4（25%）**。只有阳光电源，不是四题都有，也不是本批出现了 `stock.report_metric.query`。
- 三题在第一次失败前，**没有读取出错 API 对应的执行包**；网易读过，但把受限的 Python 风格表达式扩展成了 `name.lower()`。
- 网易、科大讯飞、蔚来的后续修正，都被一个已证实的**调用次数边界错误**挡住：先计入本次，再以 `>= 上限` 拒绝，配置 3 次实际只放行前 2 次。
- 阳光电源读到正确执行包后，已经去掉 `.query`；随后外层 JSON 字段放错位置，修复预算耗尽。最终正确位置的再次提交被阶段策略拒绝。
- 之前公开报告遗漏了这些“已经生成、但尚未进入 Python 查询工具”的修正尝试。**没有执行修复，不等于没有生成修复。** 本报告修正此前过于简化的归因。

本轮只取证、阅读代码和进行离线策略回放，没有修改线上代码、配置，没有重新调用模型或数据库查询。

## 一、统计口径

范围是本次服务器 `server_report_eval_20260908` 的历史研报 20 题首跑，不混入其他批次或问答 Skill 的 3 题。

| 口径 | 数量与比例 | 含义 |
|---|---:|---|
| 最终 API 失败 | 4/20，20% | RTE009、010、017、020 |
| 执行过程中曾出现协议错误 | 7/20，35% | 另有 013、015、018；不能与最终失败混为一谈 |
| 出现非法 `.query` 的题目 | 1/20，5% | 仅 RTE020 |
| 四道失败题中出现 `.query` | 1/4，25% | 其余三题是筛选表达式或字段错误 |
| 含 `.query` 的 finance_query 调用 | 2/43，4.65% | 按 native 工具调用计，包含被守卫拦下的调用 |
| 含 `.query` 的模型提交 DSL | 3/62，4.84% | 一个 flow 可含多条 DSL；重复尝试也计入 |

三次具体出现：RTE020 第 2 轮的 `stock.basic_info.query`、`stock.business_segment.query`，以及第 3 轮重复的 `stock.basic_info.query`。第 2 轮在进入解析器前已被阶段策略阻止，因此仅看 Python 查询工具记录会漏掉前两条。

这只是这批样本的发生比例，不是生产总体错误率。统计只计模型实际提交的工具参数，不把报错回显、回答文字、`operation="query"` 算作新增错误。

## 二、四题各自发生了什么

轮次指一次 LLM 请求；同轮可以生成多个工具调用，一个 finance_query 又可包含多条 DSL。下表区分这些层级。

### RTE009：网易的净利率变化反映了什么行业趋势？

**直接错误：** `filter = "'netease' in name.lower()"`。读取过的目录支持 `'文本' in field`，并没有定义对字段调用 `.lower()`。

| 轮次 | 当时的上下文与操作 | 实际结果 |
|---|---|---|
| 1 | 读 `financial-quality-analysis`；读 `stock.basic_info/query`、`stock.financial_3_table/query` 执行包 | 基础字段、财务字段、筛选规则已进入上下文 |
| 2 | `stock.basic_info(filter="'网易' in name", limit=5)` | 合法，0 行 |
| 3 | 同一 flow 先按 `code == '9999.HK'` 查询，再按 `'netease' in name.lower()` 查询 | 第一条合法，0 行；第二条 FILTER_ERROR |
| 4 | 阶段输入明确“本阶段可修复一次” | 本轮没有工具调用；停止检查又补入要求继续的消息 |
| 5 | 提交基础表样例查询，以及财务表 `('网易' in name) or ('NETEASE' in name)` 查询 | `.lower()` 已移除；整个调用被次数限制拦截，未做静态检查或数据库执行 |
| 6 | final / query_attempt_limit | 结束 |

原始错误：

```text
FILTER_ERROR: filter fields must be current dataview field names
```

**已证实：** 这是表达式范围越界，不是 `.query`，也不是“网易财务表查出来为空”。实际完成的是基础身份查询，英文 `.lower()` 那次没有执行成功。不能把它记作英文名称查询零行，更不能据此证明库中完全没有网易财务数据。

**初步原因：** 在中文名称与代码返回空后，模型尝试英文名称和大小写归一化，把熟悉的 Python 字符串操作带进了只支持部分表达式的协议。原始上下文没有 `name.lower()` 示例；这不是已经找到的错误示例污染。返回错误只说“字段必须是当前视图字段”，没有准确点出“字段表达式调用了未支持的方法”，对定位不够直接。

**修复判断：** 第 5 轮移除了已知非法表达式，但新增了全表样例探查；它不是纯粹重发原条件，不能未经执行就称作“完全正确修复”。确定的是系统挡住了继续验证的机会。

[网易：完整历史上下文与所有调用](evidence/report_protocol_failures_20260908/RTE009_context.md)

### RTE010：AI大模型技术发展对科大讯飞的业务增长有何催化作用？

**直接错误：** 以获取“研报观点”为目标调用 `stock.report_metric`，输出 `year, target_price, eps_pred`。这三个字段不在该接口中。

| 轮次 | 当时的上下文与操作 | 实际结果 |
|---|---|---|
| 1 | 读 `stock-research`、`equity-report-analysis`；读 `business_segment/query` | 已有分析方法和分部执行包 |
| 2 | 查业务分部；读 `financial_3_table/query` | 分部 30 行；财务执行包已加载 |
| 3 | 同一 flow：查财务历史，再查 `stock.report_metric` 的 year/target_price/eps_pred；同轮另读 `report/query` | 财务 4 行成功；metric 三字段被拒；report 执行包在本轮调用生成之后才返回 |
| 4 | 改用 `stock.report`，输出 title/rating/investment_highlights 等真实字段 | 查询串已换到观点入口，但第 3 次查询被次数限制拒绝，未进入执行 |
| 5 | final / query_attempt_limit | 保留已有分部和财务数据，但没有完成新增的研报取证 |

失败时实际生成：

```text
r3 = stock.report_metric(filter = "(code == '002230.SZ')", order = "report_date desc", limit = 10) -> code, name, report_date, institution, year, target_price, eps_pred
```

**已证实：** 在此之前从未读取 `report_metric` 执行包。该接口是指标事实长表，错误返回列出了 `forecast_year, metric_code, metric_value, value_type` 等真实字段；不是 `eps_pred` 这种每指标一列的想象结构。目标价属于 `report`；催化原因的观点文字也更直接对应 `report.investment_highlights`。报错前已有的常驻范围索引对此已给出基本区分。

**初步原因：** 从已读取的“分部/财务”跨到一个未加载契约的视图，模型凭金融名词猜了一个年度预测宽表。它还把“想拿观点”映射成了“预测指标”。可以确认执行包缺失和选错数据形态；不能仅凭这一题断言是 Skill 太长或推理强度低造成。

业务分部、财务数据作为催化分析的背景并非当然错误，问题在于研报部分的取证没有落到其真实契约上。第 4 轮已经切到较贴切的入口，但尚未执行，不能宣称修复后一定有数据。

[科大讯飞：完整历史上下文与所有调用](evidence/report_protocol_failures_20260908/RTE010_context.md)

### RTE017：看多蔚来汽车的机构主要理由是什么？

**直接错误：** 为确认股票身份，生成 `stock.basic_info(...) -> code, name, market, industry`；基础接口没有 `market`。

| 轮次 | 当时的上下文与操作 | 实际结果 |
|---|---|---|
| 1 | 读 `equity-report-analysis`；读 `stock.report/query` | 机构观点入口已正确加载 |
| 2 | 查包含“蔚来”、report_date >= 2026-01-01 的研报，返回评级、投资要点等 | 合法，0 行 |
| 3 | 未读 basic_info 执行包，直接查身份并要求 market 字段 | OUTPUT_ERROR |
| 4 | 移除 market，保留 code/name/industry，名称条件不变 | 已消除本次明确字段错误；次数限制阻止执行 |
| 5 | final / query_attempt_limit | 结束 |

**已证实：** 第一条研报请求的入口和协议是合理的，零行不是协议错。后续身份补查才引入新错误。`basic_info` 可用字段明确是 `code, name, industry, listed_date`。

**初步原因：** `market` 在此前读过的 `report` 字段中确实存在，放到基础身份信息也符合通用金融直觉；可能是跨视图借用了已有字段，也可能是按常识生成。日志不能区分这两种内部原因，但能确认当前目标接口的字段契约没有加载。

即使修复请求执行成功，也可能仍是零行；本次证据不能证明蔚来研报一定存在。后续应保留“研报入口查询成功但当前范围零行”和“补查错误”两个事实，不能合成“研报接口失败”。

[蔚来：完整历史上下文与所有调用](evidence/report_protocol_failures_20260908/RTE017_context.md)

### RTE020：阳光电源在光伏行业的竞争格局中处于什么位置？

这题最能说明 `.query` 是怎样进入执行，以及为什么不能仅看最终报错。

| 轮次 | 当时的上下文与操作 | 实际结果 |
|---|---|---|
| 1 | 读 stock 主体概览；读 sector-theme-analysis、stock-research | 只有视图描述与 operation 列表，没有 basic_info/business_segment 的精确调用格式 |
| 2 | 提交 basic_info.query、business_segment.query；同轮另读 report/query | 前一个调用被 catalog 阶段守卫拦截；后一个执行包正常加载 |
| 3 | flow 中再次使用 basic_info.query，再跟一条正确形式的 stock.report | 第一条 API_ERROR；第二条因前步失败未执行 |
| 4 | 读 basic_info/query 完整执行包 | 获得精确 api_name、request_pattern、4 个可用字段 |
| 5 | 改为 stock.basic_info(filter=...) 和 stock.report(filter=...)；但把 data_request_complete 放进每个 step，并写为字符串 "false" | 实际收到的错误是 step 不允许额外属性 data_request_complete；未进入 DSL 验证 |
| 6 | 保持改好的 DSL，把 data_request_complete 移到顶层并改成 boolean false | 已进入 final / query_repair_limit，阶段守卫拒绝 |
| 7 | final / disallowed_tool_after_completion | 结束，API 顶层仍保留较早的 unsupported stock.basic_info.query |

第 2 轮第一次生成的两个串：

```text
result = stock.basic_info.query(name="阳光电源")
result = stock.business_segment.query(code="300274", period="latest")
```

这不只是多一个后缀。直接 `name/code/period` 传参也不是对应执行包定义的 `filter/order/limit` 方式。因此，**不能通过去掉 `.query` 做字符串补丁来解决整类错误**。

首次生成前，原始 stock 概览对基础视图的表达为：

```json
{"name":"basic_info","desc":"股票代码、名称、行业、上市信息、公司简介与主营业务。","operations":["query"]}
```

当时上下文里没有 `stock.basic_info.query(...)` 或 `stock.business_segment.query(...)` 的现成错误示例。**最贴近证据的解释是：把目录寻址的 subject/dataview/operation 三层，误当成了调用路径三段；尚未读取执行包，又补出了常识性参数。** 这是初步机制解释，不是对模型内部原因的确定性证明。

第 4 轮真正读到的契约则明确是：

```text
api_name: stock.basic_info
operation: query
request_pattern: r{id} = stock.basic_info(filter=..., order=..., limit=..., realtime=...) -> field1, field2
available_operations: {"query": "stock.basic_info"}
fields: code, name, industry, listed_date
```

随后第 5、6 轮的 DSL 已符合这个调用形态。这是“执行包能纠正本次 `.query`”的直接观察；并非意味着实际取数目标已验证成功。

另有一项确定的目录不一致：**basic_info 描述宣称包含公司简介与主营业务，实际列定义却只有 4 个身份字段。** 第 3 轮恰好请求了 `main_business`。描述超出实际能力是可证实的问题；它是否直接导致该字段生成仍属推断。

[阳光电源：完整历史上下文与所有调用](evidence/report_protocol_failures_20260908/RTE020_context.md)

## 三、共同根因与证据强度

### 1. 目录层级存在，但进入执行时没有始终落实到对应契约

常驻提示已经有“明确的数据请求一次读取对应执行包，按其中的精确 API 入口、调用格式、参数和字段构造请求”，并不是完全没有分层定义。

真正发生的是：010、017、020 在未加载相应执行包的情况下跨视图构造请求。当前循环对“已有 dataview 可执行”的判定是阶段级的；读取 report 执行包后，query 阶段并不等于 basic_info 的契约也已加载。020 首次在 catalog 阶段被拦，之后读到 report 包便能把错误 basic_info 串送进 Python 解析器，就是可观察的例子。

这解释的是契约未落到具体调用，不意味着必须新增一层业务 validator。下一步应先明确上层索引只负责定位、执行包负责精确调用，并检验上下文传递和已有加载机制；不是补“禁止 .query/market/eps_pred”这样的词表。

### 2. 已证实的次数边界错误，使三题的修正无法执行

实际顺序如下：

1. Harness 先发 `tool/call` 事件。
2. 本项目事件监听器立即 `state.queryAttempts += 1`。
3. Harness 随后进入工具 prepare/guard。
4. guard 使用 `state.queryAttempts >= maxQueryAttempts` 拒绝。

因此配置 3 次时，第 3 次在执行前已变成 3，并被拒绝。第 2 次失败后，阶段更新检查的还是 `2 < 3`，又会向模型明确承诺“可修复一次”。**提示允许修复、实际挡住修复，两边边界不一致。** 这与模型推理能力无关。

定位：

- `src/scenarios/financial_qa/dsh_loop_policy.mjs:681`：失败后决定允许 repair。
- `src/scenarios/financial_qa/dsh_loop_policy.mjs:902`：guard 使用 `>=`。
- `src/scenarios/financial_qa/dsh_loop_policy.mjs:1053`：tool/call 时计数加一。
- Harness `packages/core/agent-loop/src/tool-calls.ts:167`：先 appendToolCall，再 prepare。
- Harness `packages/core/tools/src/index.ts:1486`：prepare 中运行 guard。

已用真实历史事件离线回放当前策略，重现 009/010/017 的拒绝；仅在离线回放中将上限改为 4，同一修正调用不再被次数 guard 拦截。这用于证明边界，不是已执行的生产修复，也不是建议简单放大上限。没有模拟数据库成功，更没有重跑模型。

本项目 policy SHA-256 与历史记录一致：`4e69b698a291a78df9ff452597e4fa10306e4b360c5b8a96b53158954f8fb8ba`。Harness 本地与服务器上述文件哈希一致，版本 `cd5ef8148158c3a752a658978873241fdf8e2bbc`。

### 3. 阳光电源的第二次失败来自外层 Schema，不是 `.query` 未修好

当时实际提供给模型的 finance_query Schema 已把 `data_request_complete` 定义在顶层，类型 boolean，step 只允许 goal/request。因此这里不是 Schema 没有定义，而是模型把完成标记放错层。

本次模型可见基础 Schema 的 required 是 steps；不是因为漏填顶层字段报错，实际报错是 step 的额外字段。repair 阶段将此次 Schema 错误也作为失败计入一次修复预算，随后进入 final。这里与三题的第 3 次边界错误不是同一个直接拦截原因，不能合并归因。

### 4. 公开 detail 的工具记录没有完整反映原生失败链

`dsh_service.py:1204` 优先采用 Python tracker 的 calls，仅当整个 tracker 为空才退回 native 调用事件。被 Harness guard 或 MCP 输入 Schema 提前拒绝的调用没有进入 Python tracker，因此有已有 tracker 时，这些后续失败不会合并进 tool_calls。

`dsh_service.py:1250` 再从上述记录倒序取最后一次 finance_query 错误。于是阳光电源虽然经历“修好 DSL → 外层 Schema 失败 → 再修正但阶段拒绝”，顶层仍显示较早的 `.query` 错误。部分轮次概览可看到调用名称，却不能靠这份记录看全拒绝原因。

这是此前结论不完整的原因：我之前没有下钻到完整 native tool/result。这次已补齐，不再把顶层最终错误当成全部过程。

### 5. 目前不能证明的原因

- 四题第一次出错前的系统/工具/用户输入里，没有找到 `.query(...)`、`name.lower()`、`eps_pred` 的错误示例原文。不能归因成“某个旧错误例子直接教坏模型”。
- 四题的逐轮记录都没有 max_token_hit；不是已经证实的输出截断。
- 出错轮次有 low 也有 off；没有同题受控对比，不能认定推理强度是根因。
- Skill 确实使本轮包含业务分析和额外取证，但不能直接断言“Skill 长导致错误”。两题是在空结果后补查身份，扩展了发生错误的机会；是否该继续补查，需要按问题判断。
- 修复未执行的题，不能宣称“修完准确率就从 80% 变成 100%”。模型能改掉已知结构问题，不等于数据一定有、最终答案一定正确。

## 四、一起复盘时建议先看什么

先看阳光电源第 1—5 轮：目录索引 → 未读契约猜调用 → 读到契约后修正，是最清楚的对照。

再看蔚来第 2—4 轮：合法零行 → 跨视图猜字段 → 删除错误字段却被系统拒绝。它将数据缺口、模型错误、系统错误分得最清楚。

最后核对计数边界和 detail 取证缺口。这两项是已经证实的框架问题，应与生成协议的 SOFT 效果分别处理；不需要靠添加反向提示、扩大业务规则或给某个公司写补丁解决。

## 五、证据文件

每题上下文文档均包含：原始问题与系统日期、授权 Skill 目录、当时的系统提示和五个工具 Schema、实际 Skill 正文、实际目录/字段/示例、逐轮阶段输入、全部调用参数、完整工具返回与公开答案。以 seq 标注顺序，并附 request_id、session_id、服务器原始路径和源文件哈希。

上下文来自本次运行保存的历史日志，**没有用其他任务后来修改的本地 Skill 正文替换**。供应商底层 HTTP 序列化未抓包，故不将 native 事件冒充 HTTP 请求逐字节快照。文档不包含内部推理文本。

- [网易上下文](evidence/report_protocol_failures_20260908/RTE009_context.md)
- [科大讯飞上下文](evidence/report_protocol_failures_20260908/RTE010_context.md)
- [蔚来上下文](evidence/report_protocol_failures_20260908/RTE017_context.md)
- [阳光电源上下文](evidence/report_protocol_failures_20260908/RTE020_context.md)
- [比例统计原始数据](evidence/report_protocol_failures_20260908/statistics.json)
- [离线 guard 回放结果](evidence/report_protocol_failures_20260908/guard_replay.json)

同目录还有逐题 `*_observable_events.json`，可机器复核工具消息。完整原始取证保存在 `outputs/server_report_eval_20260908/native_forensics.json`；导出脚本及离线回放脚本位于同一 outputs 目录。服务器源码版本为 `4fdda49c0a04891ab9d74c19bef24abefda31369`。
