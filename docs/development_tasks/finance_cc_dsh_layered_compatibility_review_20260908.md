# CC / DSH：金融数据分层加载与兼容性复核

日期：2026-09-08。范围：当前本地代码的只读审计、离线协议测试、真实运行器历史题回放。未修改生产代码、`.env`、服务器或数据库；新增测试与评测脚本，并更正上一份报告的表述。

## 结论

**金融取数体系属于 CC / DSH 共用能力，不属于 CC。** 上一份报告的“CC 常驻说明”只是文件装载位置的统计，用这个词概括整个体系不准确。

目前是一套数据目录、工具和执行协议，两种模型运行适配；并不是一份完全相同的常驻提示词和循环。分层改动未发现目录或执行接口断链，但真实样本暴露了取数结束方式与按需加载的差异，不能把“底层共源”表述为“两端全链路效果已经等价”。

## 1. 从入口到结果，实际共用到哪里

| 层级 | 内容 | CC / DSH 现状 |
|---|---|---|
| 场景输入 | 用户问题、会话、取数/回答模式 | 同一问答服务分发；两端具体 prompt 组装不同 |
| 常驻角色与运行方式 | 工作目标、交付方式、能力说明 | 分别装载；CC 还有业务 Skill / 角色说明，DSH 有阶段指引 |
| 全局路由 | 7 类对象、31 个数据视图的简短范围，五类方法用途 | 同源，不包含所有方法的完整参数与字段 |
| 按需执行包 | 精确 API、调用格式、参数、字段、特殊口径、示例 | 同源；按 subject / dataview / operation 组装 |
| 查询执行 | `finance_query` → 解析 → 静态检查 → provider | 同源；运行器不自行生成 SQL |
| 结果资产 | 完整数据、血缘、`rN`、`result_ref`、失败恢复 | 同源协议，各会话独立作用域 |
| 后续上下文与结束 | 样例、明细、继续查询或交付 | 两端投影与结束机制不同 |

主要代码位置：

- `src/scenarios/financial_qa/service.py:121`：CC / DSH 问答入口及实际说明文件绑定。
- `src/services/finance_claude_session_service.py:892`：CC 拼接 `system.md`、`finance_api_protocol.md`、`data_query.md`，并追加角色和 Skill 摘要。
- `src/scenarios/financial_qa/dsh_service.py:625`：DSH 装载独立 `dsh_system.md`；阶段说明来自 `dsh_loop_policy.mjs`。
- `src/scenarios/financial_qa/tools.py:619`：共用的目录工具描述和 schema；另两个共用工具为 `finance_query`、`load_finance_result`。
- `src/scenarios/financial_qa/dsh_mcp_server.py:86`：DSH MCP 复用上述工具定义与 handler；真实运行时在独立子进程实例化。
- `src/services/finance_data_tool_catalog_service.py:275`：按需目录投影与模板展开。

CC 的整套场景说明不能原样搬给 DSH：CC 暴露 Skill、回测等能力，DSH 当前只暴露三项金融数据工具。应共用的是取数核心规约，各运行器只保留自身适配与场景扩展。

## 2. 渐进加载如何工作

正常路径是：**范围摘要 → 所需执行包 → 查询 → 结果索引 → 按需补充契约或明细**。

这不是每题强制增加三次“翻目录”调用：

| 输入 | 模型取得什么 |
|---|---|
| 空参数 | 全局对象与视图概览 |
| `subject` | 该对象下的视图概览 |
| `subject + dataview` | 该视图的全部方法契约 |
| `subject + dataview + operation` | 只展开所选方法；兄弟方法保留名称导航 |

例如 `stock / report / aggregate` 直接取得 `stock.report.agg` 的完整定义；`query` 是方法分类，实际明细入口仍是 `stock.report(...)`，不是 `stock.report.query(...)`。成分关系规范分类是 `constitution`，调用仍为 `index.constitution(...)` 等；旧客户端选择 `operation=query` 仍可兼容。

本轮检查确认：

- `api_class/api_classes` 的内部模板已在系统内展开，两端模型不必跨两块结构拼方法。
- DSH 历史复用读取真实工具返回中的 `functions[].api_name` 和目录版本，不依赖删除的 `api_classes`。
- `available_operations` 是导航，不代表已经取得兄弟方法的完整契约。
- 旧 staged context builder 已消费完整 functions；Coding 资料和编辑器仍使用各自兼容的内部模板接口，没有发现残留 consumer 断链。
- “只展开 operation”仍保留该 view 的字段字典，是方法级按需加载，不是逐字段加载。

## 3. 离线验证：覆盖整个体系

本轮合并执行 **227 项 Python 测试全部通过**，另有 **34 项 Node 循环测试通过**。Python 仅有原有 `python_multipart` 弃用提示。

新增 `tests/test_finance_cross_runtime_disclosure.py` 的 100 项覆盖：

- 7 个 subject、31 个完整 view、47 个 operation 执行包，在 CC 实际 SDK handler 与 DSH Bridge / MCP wire handler 两侧逐值比较。
- 只展开所选方法、保留正确字段与局部规则；4 个关系视图的旧 query 输入兼容。
- 五类操作进入真实解析器、静态检查、API 分发与结果存储：

| operation | 验证请求入口 | 内部执行类型保持 |
|---|---|---|
| query | `stock.basic_info(...)` | base |
| window | `stock.quote.kd_close_avg(...)` | kd |
| constitution | `plate.constitution(...)` | base |
| aggregate | `stock.quote.agg(...)` | agg |
| compute | `stock.quote.dynamic_cal(...)` | dynamic_cal |

- 同 flow 的 `step1.code`、跨 flow 的 `r1.code`、失败后保留成功步骤再修复，以及结果按需读取。

**边界：**离线执行只替换最终数据 provider，未替换解析器、校验器、路由器或结果存储；它证明适配与协议兼容，不证明真实模型选对方法，也不证明金融公式准确性。`compute` 本轮验证到 provider 分发，没有调用其内部代码生成模型。

复现：

```sh
.venv/bin/python -m pytest -q tests/test_finance_cross_runtime_disclosure.py tests/test_finance_data_tool_catalog_snapshot.py tests/test_financial_qa_cc_scenario.py tests/test_financial_qa_dsh_runtime.py tests/test_custom_tool_coding_skill.py

/Users/imac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/dsh_finance_loop_policy.test.mjs
```

## 4. 真实样本的设置

使用既有历史题，经本地 `FinancialQaCcService.answer` 调用两端实际运行器 Claude Agent SDK 与 DeepSeek Harness，不是用 DSH 模拟 CC，也不是部署后 HTTP 网关验收。全部金融目录开放，未预设正确入口。前三问在各自同一会话连续执行，检查复用与跨视图加载；其余独立执行。

- 模型：Bailian `deepseek-v4-flash-0731`，两端分别使用对应的 Anthropic / OpenAI 兼容接口。
- CC：Claude Agent SDK 0.2.123、effort=low、max_turns=12；DSH 使用现有分阶段策略（目录 low、查询 off），不是相同推理预算的纯运行器耗时对照。
- 模式：`data_only=true`、`execution_mode=standard`。`research_mode=fast` 是回答深度选项，不等于 fast 执行循环。
- 代码快照：`/tmp/fin_progressive_runtimes.2qDQka`；目录 revision：`6decc5e9e21b03865248706f63344cfc75e13591ba40f5e9aefb9fefd045369d`。
- 回放结束后重新比对，快照中的642份 `src/config` 文件与当前工作区哈希全部一致。
- 本机默认 CC 的 provider 为 deepseek，但该路由缺凭据，模型名来自 Bailian。本次只在评测进程切到现有 Bailian 配置，未改 `.env`；因此结果不能作为默认 CC 配置已经可用的证明。
- 原生调用、实际目录返回、输入、结果和用量保存在 `outputs/progressive_cc_dsh_20260908/`。未用接口 `error` 为空代替业务完成判断。

同样选择的实际目录返回也做了哈希核对：股票行情/query、基金基础/query、指数成分全视图、研报全视图、股票基础全视图，共5组两端全部一致。这是原生 tool-result 的比较，不是用服务端目录重新生成后自我比较。逐步调用、用量与哈希见[机器汇总](../../outputs/progressive_cc_dsh_20260908/summary.json)，实际输入与目录包见[上下文证据](../../outputs/progressive_cc_dsh_20260908/catalog_and_input_evidence.json)。

正式 **6 题 × 2 个运行器 = 12 次请求**，另有一次 CC 连通性 smoke，不计入下表。CC 板块题失败后暂停，检查记录后只补完尚未执行的最后一题，没有重跑失败题或挑选最好结果。

| 历史题 | CC 实际过程 | DSH 实际过程 | CC / DSH 轮次；总秒数 |
|---|---|---|---|
| 宁德时代最近10个交易日收盘价、涨跌幅 | quote/query → 明细10行 → 补读结果 | quote/window → 改用明细10行 | 4 / 2；17.73 / 17.14 |
| 茅台最近一个交易日开盘价、收盘价 | 复用 quote/query → 1行 | 明细调用被拒 → 补读 query → 1行 | 2 / 3；4.20 / 8.04 |
| 名称包含债券的基金代码、名称 | fund.basic_info/query → 50行 → 补读结果 | 新加载同入口 → 50行 | 4 / 2；15.86 / 4.39 |
| 沪深300当前成分股、权重 | constitution → 600行；又追加统计、2次静态失败后修复 | constitution → 600行后交付 | 8 / 2；47.81 / 4.94 |
| 近5日主力净流入合计最高的10个板块 | moneyflow/window → 0行；继续探查至轮次上限 | 同窗口入口 → 0行后交付 | 13 / 2；97.88 / 5.68 |
| 指定截至日的天齐锂业近三个月最高目标价 | report明细按目标价降序 → 0行；继续扩大日期/范围、追加聚合 | 读对 report，但只查公司代码即结束，漏查目标价 | 9 / 2；46.63 / 6.35 |

轮次分别取 CC SDK `num_turns` 与 DSH 的模型请求数，是各运行器原生统计；不要把 CC 日志的 `llm_usage.call_count=1` 误读为只思考一轮。CC 最后一题使用降序明细取得最高目标价，在数据层是可行方案，不因没选 aggregate 就判错。

两端各有数据范围限制：基金均只返回50行，不代表全部匹配基金；成分表返回600行且权重缺值，也不代表成分和权重数据质量已通过。板块窗口零行不单独算入口错误，DSH 目标价仅查身份则是真正漏取目标数据。

总耗时 CC 230.11 秒、DSH 46.53 秒；总 token（含缓存）分别 398,165 / 69,983。这不是公平的性能 A/B：CC 有大量额外查询和回答，DSH 有一题提前结束漏取数，不能据此宣称稳定提速或某个运行器更准确。

## 5. 已追到原生过程的差异

### A. 同会话按需加载：不是历史丢失

“宁德时代最近10个交易日收盘价和涨跌幅” → “贵州茅台最近一个交易日开盘价和收盘价” → “名称包含债券的基金代码和名称”。

CC 第一问加载 `stock.quote/query`，第二问直接复用，第三问补读 `fund.basic_info/query`。这是期望的渐进路径。

DSH 第一问加载 `stock.quote/window`，却执行了 `stock.quote(...)` 明细。第二问继续执行明细时被阶段 guard 拒绝，随后补读 query 才成功。离线回放其原生历史后，复用函数得到：

```text
第一问后的完整契约：stock.quote.kd_<field>_<method>
第二问 stock.quote 可复用：false
补读 query 后：新增 stock.quote
同一请求可复用：true
```

模式是 standard、会话复用成功、版本未变化。根因不是模板字段删除或目录丢失，而是两处放行规则不对称：query 阶段允许调用合法 API，catalog 历史复用阶段则只认已读的精确方法。第一问跨方法调用先放行，第二问才要求补契约；被拒的那一步约 2.09 秒。

这里不应仓促增加“每次必须读过方法”的新 validator。应先统一目录究竟是按需知识，还是执行准入条件；当前两处采用了不同标准。

### B. DSH 目标价：读对目录，完成标记却误填

原题：“截至2026年8月28日，近三个月发布天齐锂业目标价研报中最高目标价是多少？”

实际先并行读取 `stock.report` 全视图和 `stock.basic_info`。report 的明细、聚合、目标价上下限全部可见。随后只提交了公司代码查询，却把 `data_request_complete` 设为 true，系统遂结束。

原生普通文本仍表示先确认代码、再查研报，说明后续目标尚在表达中，但机器消费的完成标记已误报完成。这不是没有目标价数据、目录缺失、预算耗尽或工具受限；不能计为完成用户取数目标。也不能仅凭本例就归因于推理强度。

### C. CC 取数完成后继续查询

- 成分题先正确返回 600 行，但权重缺值。CC 随后读明细、读了不适用的 `index.pricevalue/aggregate`，补做计数时又发生两次静态失败，之后才修复。这些并不是用户要求的新目标。
- 板块近5日资金题第一次调用 `plate.moneyflow.kd_main_net_sum` 正确、返回零行。CC 继续查身份、换条件、探查日期，最终尝试用单日数据替代5日窗口，达到 SDK 轮次上限；DSH 原题则在第一次零行后交付。

CC 这里的额外动作与“仅取数”缺少确定的宿主收尾有关：CC 接受完成标志后，SDK 循环仍可继续，而 DSH 有宿主结束机制。这个差异解释了额外动作为何能够继续，并不证明某一句提示词是模型生成额外查询的唯一原因。不能靠再加一条业务提示词把两端当成等价。

另一个确定的日志问题：该次 SDK 原始结束是 `error_max_turns / is_error=true / result=null`，宿主却记为 `empty_response`。因此本例不是“SDK 成功但无正文被误判”，而是实际达到上限、原始失败原因被后续兜底覆盖。

## 6. 后续应如何收拢

后续方向已按用户要求更新：**DSH优先，CC仅作为评测基准，不要求为了两端等价增加复杂度。** 下列“共享/统一”不再意味着必须同步改造CC；以[DSH主运行器评估](dsh_primary_runtime_readiness_20260908.md)的能力范围与根因修复优先级为准。

1. **共享核心说明、保留运行器薄适配。** 将取数协议和结果证据保持单一装载来源；CC 的角色/Skill 和 DSH 的阶段控制分别叠加。当前两份文字基本同义，但仍重复维护，不应继续各写一套。
2. **先统一仅取数的生命周期。** 模型理解完整目标，输出本轮 flow 与完成声明；系统按统一契约交付结果并保留原生失败原因。既不能把“任意一个工具成功”当全部完成，也不能零行后无限追非空。完成语义不能通过 `.basic_info` 等 API 黑名单猜测。
3. **统一目录复用的定位。** 保留按需加载与版本追溯，明确导航/完整契约/合法执行的关系；不要为每个 query 追加单独例外或强制目录轮次。

另需明确更新边界：CC 每轮重新组装说明并比较 fingerprint；DSH 的系统 Markdown 在 service 初始化时读取，改文件后需要新 service 才生效。两端都处理 catalog revision 更新，但这不等于所有提示词自动热更新。跨 CC / DSH 切换也不等于会话和 `rN` 自动互通，两者的运行会话及结果作用域分别独立。

本轮结论是“目录与执行协议兼容已验证，运行器的取数上下文和结束方式尚未完全对齐”，不是全量准确率、稳定提速或生产就绪声明。上述差异有些是既有运行器行为；本次不是完整的新旧版本 A/B，不能一概归因为本轮分层改动。
