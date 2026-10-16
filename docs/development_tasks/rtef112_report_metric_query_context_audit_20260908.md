# 新宙邦研报查询 `.query` 错误：MCP 现场与上下文追溯

- 日期：2026-09-08。
- 问题：机构对新宙邦的未来增长预期是否一致？
- Case：RTEF112。
- 本次操作：只读审计既有结果、服务器原始会话与代码；保存研究报告和脱敏证据。未修改协议、提示词或运行代码，未重新调用模型，未发布、提交或重启。
- 目标：把发生过什么、当时模型看见什么、哪些推断尚未证实分开，供共同审阅。

## 1. 先明确两个结论

**最新 Excel 展示的是旧失败，不是 9 月 8 日新复现。**

`outputs/finance_mcp_cli_20260908/` 在 9 月 8 日生成了 Excel，但其脚本 `.codex_tmp/verify_mcp_report_20260908.py` 明确是离线导出烟测：从 `outputs/finance_python_filter_v2_20260907/results/` 读取 BUS026、BUS093、RTEF112 三条旧记录，没有调用模型。

manifest 原文：

```json
{"created_at": "2026-09-07（历史结果，2026-09-08仅验证导出）", "response_mode": "both", "concurrency": 2}
```

导出文件中的 RTEF112 与 v2 原记录经 `jq -S` 规范化后 SHA-256 完全相同：

```text
07ae1630e4c66c71a2be7998b318f9150ba42e1acdba1c33bd45e97529253d42
```

请求 ID 都是 `fq_ee38a81402f14dfcb16fe5b6fe35a0ba`，返回时间都是 `2026-09-07T10:35:11.016076+00:00`，即北京时间 9 月 7 日 18:35:11。

**但目前也不能宣布问题已经解决。** 上次后来成功的复跑，从第一步就选择了 query 目录；它没有再次经历“aggregate 目录 → 转向明细”这条失败路径，不能证明新增入口导航已经修复该路径。这里需要收窄之前对修复效果的归因。

## 2. 最近五次实测对照

下列均为既有 MCP 实测，同一个原问题。时间为北京时间、按返回完成时间排序。接口成功不等于 summary 正确。

| 完成时间（9 月 7 日） | 指标目录 operation | 其他目录 | 实际结果 | LLM轮次 | 请求耗时 | Token含缓存 |
|---|---|---|---|---:|---:|---:|
| 14:18:14 | query | basic_info.query | `stock.report_metric(...)` 成功，指标38行 | 3 | 16.675秒 | 18,997 |
| 16:20:06 | query | report.query | 两个指标明细请求成功，38行和50行 | 4 | 20.896秒 | 35,767 |
| 17:30:55 | aggregate | report.query | 先生成 `.filter(...)` 混合语法，修复后生成 `.query(...)`，失败 | 4 | 20.552秒 | 28,267 |
| 18:35:11 | aggregate | 无 | 生成 `.query(...)`，只补输出字段后仍失败 | 4 | 15.424秒 | 23,098 |
| 21:50:21 | query | 无 | `stock.report_metric(...)` 首次通过，41行；summary仍有未经验证的剔除和原因推断 | 4 | 24.102秒 | 24,453 |

9 月 8 日 Excel 是第四行的重导出，不是第六次运行。

注意：17:30 的另一个 query 目录是 `stock.report`，不是 `stock.report_metric`。没有证据表明这两次失败加载过指标明细契约。本次分析过程中一度将其混淆，核对完整 `subject + dataview + operation` 后已纠正。

详细对照、逐步调用、原 summary 和数据前两行见 [五次实测证据](evidence/rtef112_context_audit_20260908/case_comparison.json)。这些是很小的观察样本，而且代码、目录和环境不完全相同，不用于计算泛化成功率或纯性能收益。

## 3. 18:35 失败现场：模型到底看到了什么

### 3.1 场景与版本

- 真实 MCP `tools/call`，进程内 ASGI；连接当时配置的真实模型与数据库，不是公网 Nginx 链路测试。
- runtime：DSH，模型：`deepseek-v4-flash-0731`。
- `execution_mode=standard`，不是零重试的 fast execution。
- `research_mode=fast` 仅影响回答要求；两种 fast 不应混为一谈。
- `response_mode=both`、`detail=true`、`max_rows=100`，没有 conversation_id。
- 原日志确认 `isolated_request=true`、`resumed=false`；会话只有一个真实用户消息、一个 turn。
- 目录 revision：`b81dc4c1d6f9694d5f9c6052b1c8d9fd4e8c6ff700028de779a70c2f2000becd`。
- system SHA-256：`a44ee08225579bfc8fbaf8870f0bdd16ed894f10ea5f825135515018e4eeca87`，已与原始 request/header 内容核对。
- stage policy SHA-256：`c552def079596de117456df1c909dcf34c2d718d58ffeb8fcdb4cb4b7327ffd1`，与当前本地文件一致。

完整脱敏现场见 [18:35 原始上下文提取](evidence/rtef112_context_audit_20260908/failed_session_context.json)。该文件保存真实 system、全部三个工具 schema、实际目录返回、各阶段消息及工具调用。它不是按当前代码重新构造的上下文；重复 header 去重，保留源 seq，省略 reasoning 和流式碎片。

### 3.2 第 1 轮：操作选择

用户消息除了原问题，还含系统日期 2026-09-07 和快速回答要求，没有之前公司的 working set。

`read_finance_catalog.operation` 参数说明实际包含：

```text
when no row-reducing statistic is explicitly requested, default to query.
aggregate is only for an explicitly requested reduction of multiple rows
to grouped statistics such as average, median, maximum, minimum, sum, or count.
```

实际调用（源 seq 80）：

```json
{"subject":"stock","dataview":"report_metric","operation":"aggregate"}
```

从业务上，“一致性”可以用透明聚合数据分析，也可以用机构明细分析，因此不是见到 aggregate 就判业务错误。但当前工具描述要求没有明确统计量时默认 query；模型没有沿用这一默认规则。更关键的是它随后没有执行聚合，而是改为取明细。

### 3.3 模型收到的指标目录（源 seq 81）

以下为原返回的相关字段节选，完整内容在证据 JSON 中：

```json
{
  "name": "report_metric",
  "desc": "研报标准年度指标明细，一行对应一篇研报的一个指标、年份和值类型，当前覆盖EPS、归母净利润及增速、营业收入及增速、PE、PB和ROE，适合查询特定机构的年度预测值、实际值及多机构统计。",
  "functions": [{
    "api_name": "stock.report_metric.agg",
    "operation": "aggregate",
    "guidance": [
      "均值、中位数、区间或按公司归并后的统计量排名使用 stock.report_metric.agg；逐机构或逐公司原始值的对比、排序仍使用明细查询。"
    ]
  }],
  "selected_operation": "aggregate"
}
```

其中聚合 request_pattern 是：

```text
r{id} = stock.report_metric.agg(filter, agg, group_by, order, limit)
  -> group_fields, aggregate_expression as alias
```

事实：

- 返回明确展示的是 `stock.report_metric.agg`，不是 `.query`。
- 同时给了逐行字段清单、明细粒度描述，以及“原始值对比仍使用明细查询”的 guidance。
- 没有 `available_operations`，没有 `stock.report_metric(...)` 明细签名或明细例子。
- 不是工具返回被字符预算截断：原始目录返回完整可读。缺少兄弟入口是 operation 切片结果。

因此当前包一方面提醒可用明细解决，另一方面没有给出明细方法的确切入口；它只是聚合契约，不足以直接构造明细请求。

### 3.4 第 2 轮：生成没有加载过的调用

查询阶段提示（源 seq 84）以以下内容开头：

```text
目录字段与口径已经就绪。只把用户明确要求的事实和完成其计算不可缺少的依赖
合并到一次最小 finance_query flow；……不要拆成多次试探查询。
```

实际生成（源 seq 173）：

```text
result = stock.report_metric.query(
    filter = "(name == '新宙邦') and (metric_code == 'np_parent') and (value_type == 'forecast')",
    limit = -1
)
```

同时出现两个结构错误：未注册的 `.query` 方法、缺少 `->` 输出列。

此处是模型主动生成错误方法；不是系统把正确名称转换错。检查前面的 system、工具描述、用户消息和目录返回，没有任何 `stock.report_metric.query` 或 `stock.report_metric.filter` 示例。这两种名称都首次出现在模型自身输出中。

模型真正收到的第一条失败反馈（源 seq 174）只有：

```json
{
  "error": "invalid API request: expected -> output fields after argument list",
  "failed_step": 1,
  "completed_steps": [],
  "next_result_name": "r1"
}
```

解析尚未完成，因此还没有进入 API 注册校验；这一步数据库没有执行。

### 3.5 第 3 轮：局部修复保留了错误方法

实际修复提示（源 seq 177）：

```text
上一查询未成功。这是唯一一次修复机会：只修正工具结果明确指出的失败步骤和口径，
不改目标、不换 API 追值，也不要重复完全相同的参数。
```

模型随后保留 `.query`，只追加：

```text
-> code, name, institution, forecast_year, metric_value, unit, metric_code, value_type, report_date
```

第二次反馈才显示（源 seq 416）：

```json
{
  "validation": {
    "ok": false,
    "errors": ["API_ERROR: unsupported api=stock.report_metric.query"]
  },
  "recovery": {
    "retryable": true,
    "max_retries": 1,
    "guidance": "只依据已读取的数据目录修正失败请求一次；保留已经成功的步骤，不得通过更换业务目标或删除用户条件来碰运气。"
  }
}
```

但下一条阶段提示已经是 `stage=final reason=query_repair_limit`。底层 recovery 仍说可重试，上层说机会已用尽，出现了两套表述；实际执行以 harness 生命周期为准。这是明确的上下文不一致，不表示应该简单增加重试次数。

### 3.6 第 4 轮：错误被解释成能力缺失

最终 summary 开头原文：

```text
The `stock.report_metric` view only supports the `aggregate` operation
(its API is `stock.report_metric.agg`), not a row-level `query`.
```

这是错误概括。工具仅证明该调用名未注册，不能证明视图没有明细方法。模型把“当前只加载了聚合切片”和“明细方法不存在”混为一谈。

### 3.7 逐轮量化

| 轮次 | 阶段 | 有效推理强度 | 输入上下文Token | 模型请求耗时 | 实际动作 |
|---|---|---|---:|---:|---|
| 1 | catalog | low | 3,907 | 2.557秒 | 读取 aggregate |
| 2 | query | off | 5,586 | 2.850秒 | 生成 `.query` 且漏输出列 |
| 3 | repair | low | 6,011 | 6.052秒 | 只补输出列，仍是 `.query` |
| 4 | final | off | 6,469 | 3.913秒 | 误称不支持明细 |

没有输出上限命中。以上是完整模型请求区间，不是可单独分离的纯思考时间。目录、第一次查询封装、第二次查询封装分别约13、8、10毫秒；最终 API 实际执行耗时为0，问题不在数据库速度或数据缺失。

## 4. 17:30 的另一次失败：还有错误回传问题

完整现场见 [17:30 原始上下文提取](evidence/rtef112_context_audit_20260908/earlier_failed_session_context.json)。

该轮加载：

```text
stock.report_metric / aggregate   → 只有 stock.report_metric.agg 契约
stock.report        / query       → stock.report(...) 契约
```

第一步指标调用混合了类 Python 链式调用和 SQL 式片段：

```text
result = stock.report_metric.filter((code == '300037.SZ') and (value_type == 'forecast')
  and (forecast_year >= 2026)), group_by = code, name, forecast_year, metric_code,
  unit, institution, order by forecast_year desc, limit = -1
```

**重要差异：** detail 中记录的解析异常是缺少 `->`，但原会话证明，模型实际收到的工具结果（源 seq 278）是：

```text
Error: local variable 'progress_title' referenced before assignment
```

即异常反馈路径自己再次报错，掩盖了原始结构错误。只看汇总 detail 会误以为模型已获得正常的解析错误提示。

该显示错误不是 `.query` 的首次生成原因，但会破坏修复环节的有效反馈。当前本地 `tools.py` 已在 try 前初始化 `progress_title`，并有对应回归测试；18:35 原始会话也已返回正常的解析错误。这里记录历史事实，不在本次重新修改或测试。

修复时指标调用变成 `.query`，第二个 `stock.report(...)` 请求的外层括号也被改坏。因 flow 在第一步失败就返回，第二步不算实际执行失败；它只是被提交的潜在错误。该现象说明是外层调用契约未被稳定遵守，不能把全部问题解释成金融条件口径难。

## 5. 上下文“干净”与否：事实和判断分开

### 5.1 已排除或没有证据支持的原因

- **跨题串话：** 两次失败均为隔离新会话，只有一个实际用户问题，没有历史公司结果索引。
- **错误示例直接诱导：** 原始输入中没有这两个错误方法名。
- **工具集合过多：** 只有三个金融工具。全业务路由索引是本来就需要的入口信息，不应把它简单视作污染。
- **缓存读等于串话：** 不能这样推断。四轮 system/tools 前缀一致；实际用户与目录消息在独立会话中。
- **上下文长度截断：** 未发现证据；失败时输入约5.6K/7.0K，输出也未到上限。
- **query off 必然导致错误：** 不能下此结论；成功样本生成阶段同样为 off。没有同上下文仅切换推理强度的对照。

### 5.2 实际存在的重叠、残留或冲突

1. **切片描述和可执行契约没有完整衔接。** aggregate 包仍描述明细、建议明细，却不提供明细入口；模型转向明细时没有补读对应契约。
2. **目录就绪被表达成任务调用契约已就绪。** 当前 query 阶段提示直接推进执行；实际上只代表某个 operation 包已加载，未必是模型接下来选择的那种数据粒度。
3. **修复提示约束过窄。** 首次只回传解析错误，提示又强调“只修正明确指出的问题、不换 API”。观察到的行为正是只补箭头。不能因此证明提示是唯一原因，但它与失败路径一致。
4. **工具 desc 有机械编辑残留。** 真实 `finance_query` description 中有孤立片段 `confirmation, not context; (2)`，缺少前文与第(1)项。当前源码仍有同样内容。
5. **工具 desc 的目标边界重叠。** 前面要求只取用户计算必要数据，后面又允许继续解释性目标，列举趋势、构成、同行比较。这不等于直接导致 `.query`，但不是单一清晰职责。
6. **底层 recovery 与上层剩余预算说法冲突。** 已到 final 时工具还返回 `retryable=true/max_retries=1`。
7. **API 请求被表述为“用户通过界面明确选择”。** 实际是评测传入 `research_mode=fast`；这属于复用 UI 话术的场景残留，不应夸大为 `.query` 根因。

另一个重要边界：在 standard 模式，query 和 repair 阶段实际上都允许再次调用 `read_finance_catalog`，本地 policy 与旧 trace 中的文件哈希一致。**不能说 harness 在第一次调用失败前就硬性禁止补目录。** 该路径是允许的，但模型没有使用；硬关闭发生在第二次失败之后。

## 6. 当前根因判断及证据强弱

可以确认的失败链：

```text
选择指标 aggregate
  → 包中只有 .agg 签名，却同时谈及明细用途
  → 后续改取机构明细，未补读明细契约
  → 模型构造 .query / .filter 等未注册外层结构
  → 第一次失败未能完整纠正调用契约
  → 修复保持/继续猜测错误入口
  → 预算耗尽，summary 把调用错误解释成能力不存在
```

**根因定位到“目录操作选择、契约可见性、请求生成与修复之间未稳定衔接”。** 不是 SQL 执行错误，也不是数据有没有的问题；更不是可以通过默默删除 `.query` 后缀来消除的正确性问题。

但“哪一条提示或可见性缺口造成了多少概率的错误”，现有样本没有因果证明。模型为什么首次把一致性解释为 aggregate，也不能仅凭调用轨迹确定其内部原因。

当前新增 `available_operations` 和 system 的 api_name 导航说明，针对的是一个真实信息缺口。但21:50成功复跑直接加载 query，**没有检验 aggregate→明细 的迁移能力**。它只能证明该次正常明细路径成功，不能证明该改动已解决以上链条。

## 7. 与用户共同审阅的重点（本次不实施）

建议先共同看三处，而不是继续增加 case 禁令：

1. 聚合目录建议使用明细时，应如何提供简短且真实的入口定位，而不复制整套契约？
2. 系统、工具 desc、阶段提示、recovery 中，哪些执行约束重复或相互抵牾，应该由哪一层唯一负责？
3. 验证时必须覆盖同一失败路径：在保持问题、已加载 aggregate 包和模型配置一致的条件下，观察是否能正确补读明细并调用；不能用“起步直接 query 的成功”替代验证。

上述是后续验证建议，不是本次已完成的实验或修改。

## 8. 证据与代码位置

- [18:35 原始上下文](evidence/rtef112_context_audit_20260908/failed_session_context.json)：seq 7/85/178/420 为请求 header；81 目录结果；173 第一次调用；174 解析反馈；177 修复提示；416 静态失败；419 final 提示；555 最终回答。
- [17:30 原始上下文](evidence/rtef112_context_audit_20260908/earlier_failed_session_context.json)：121 指标聚合目录；123 研报明细目录；278 被显示变量异常掩盖的反馈；614 修复后的静态失败。
- [五次既有结果](evidence/rtef112_context_audit_20260908/case_comparison.json)：原始请求、工具调用、分步耗时、summary 原文、前两条数据及来源路径。
- [当前目录切片与导航](../../src/services/finance_data_tool_catalog_service.py)：`get_model_dataview`。
- [当前工具描述与异常反馈](../../src/scenarios/financial_qa/tools.py)：`_OPERATION_SELECTION_DESCRIPTION`、`finance_query` 定义、`progress_title` 初始化与异常分支。
- [阶段提示与允许动作](../../src/scenarios/financial_qa/dsh_loop_policy.mjs)：`STAGE_PROMPTS`、`stageAllows`、失败预算判断。
- [系统提示](../../src/scenarios/financial_qa/dsh_system.md)：当前导航说明较原失败版有变化，不能冒充当时输入。

成功复跑的本地会话目录目前为空，本次仅取得其持久化汇总 trace 和 API 返回，未恢复其逐条原始模型上下文。因此对成功路径的说明限于这些证据，不用当前代码重新生成的内容冒充历史现场。

本次所有读取均为既有文件或服务器日志。没有发送金融查询、修改数据库、更新服务器，也未改写原 Excel。
