# 金融数据 API 通用协议

先用本协议判断需要哪一类查询；再根据 `read_finance_catalog` 的路由索引定位 subject 和 dataview，并读取目标 dataview 的具体字段、方法、参数、规则和示例。具体目录是唯一的可执行依据；本协议不替代目录，也不提供可猜测的字段名。

所有金融数据请求都使用：

```text
rN = api_name(arguments) -> output_fields
```

`rN` 是系统保存后的正式结果编号。调用 `finance_query` 时提交一个顺序 `steps` 列表，每一步包含一个自然语言 `goal` 和一条请求，请求左侧统一写成 `result`；系统会逐步分配正式编号并把目标、数据血缘和结果摘要写入 `working_set`。同一流的后续请求通过 `step1.column`、`step2.column` 引用更早步骤，跨流才使用已经保存的 `rN.column`。模型不得自行复用已有编号。

时间模式遵循目录定义：明确的过去交易日、最近 K 个交易日、历史区间和 T-1 日级事实使用 `realtime=0`；实时、盘中和当前快照才使用对应 dataview 的实时模式。不要在历史窗口返回空值后改用实时模式重跑同一目标。

## 先选择类型，再查具体 API

一个问题可以同时包含多个数据目标。先为每个目标选择下列一种 API 类型，再到目录中取得该类型在目标 subject/dataview 下的具体定义。选择后，后续查询应服务于尚未完成的目标，不能因为某个字段为 null 而把另一类目标改写成基础查询或无关检索。

- 查询结果中的 `code`、`name`、`stock_code` 等身份列，与某个数值字段是否为 null 是独立事实。只要身份列已返回，就可作为后续 `rN.column` 的对象范围；不得为了重建同一对象范围而重复查询原 dataview。
- 只有 validation error、缺少下一步所需的身份列，或目录明确表明当前类型不能表达目标时，才重新选择或修正查询。零行和目标数值为 null 都不是改查无关 subject、证券、新闻或其他 dataview 的理由。
- 下游目标的对象范围来自上游结果时，必须用上游身份列的 `rN.column` 或同流 `stepN.column` 直接约束下游，不重新取得同一范围，也不先执行无过滤的宽查询。

## 五类通用 API

### 1. 基础查询

用于主体自身的行情、资金、估值、财务和基础信息。

```text
rN = subject.dataview(filter, order, limit, realtime) -> fields
```

输出字段必须来自当前 dataview。

研报结构化数据使用个股下的 `report` dataview：

```text
rN = stock.report(filter, order, limit) -> fields
```

该 API 只读取 `REPORT_DB_URL` 指向数据库中的 `chatbi_report_v` 和 `chatbi_metric_v` 两个视图。`code` 同时接受六位代码和 `CODE.EXCHANGE`，由 provider 统一适配为研报库的六位代码。`report_date`/`report_period` 表示研报发布日期，不使用交易日偏移；时间段用 `>=` 和 `<=` 两个条件表达。输出评级、观点、风险和目标价时返回研报级记录；输出 `metric_code`、`metric_name`、`forecast_year`、`value_type`、`metric_value` 等字段时返回指标级记录；同时需要两类字段时按研报 ID 关联。用户指定预测年份时必须把 `forecast_year` 写入 filter，问预测值时还必须加入 `value_type = forecast`。需要完整结果时使用 `limit = -1`；返回行数达到 limit 时不得声称结果是全集。目标价使用语义字段 `target_price_lower`、`target_price_upper`，不直接依据底层中文别名判断上下限。

### 2. K 日指标

用于目录明确支持的固定窗口行情或资金指标。

```text
rN = subject.dataview.kd_<field>_<method>(
  k, filter, order, limit, realtime
) -> code, name, value as alias
```

`k` 必填，`field` 和 `method` 必须来自当前 dataview 的定义。

### 3. 成分关系

用于得到指数、行业、板块、基金或热点与其成分股的对应关系。

```text
rN = subject.constitution(
  filter, order, limit, realtime
) -> subject_code, subject_name, stock_code, stock_name
```

实际主体标识字段以当前 constitution dataview 为准。

### 4. 成分股聚合

用户问的是一个主体的**成分股指标的合计、均值、中位数、最大值、最小值或数量**时，使用此类；即使该主体自身也有 `quote`，也不能把主体自身行情当作成分股统计。

```text
rN = subject.constitution.agg(
  filter, agg, group_by, order, limit, realtime
) -> subject_code, subject_name, aggregate_result
```

一次调用只计算一个聚合目标。`agg`、`group_by`、可用 stock 指标和输出格式必须以当前 dataview 目录为准。多步查询时优先用已有结果的 `rN.column` 约束聚合对象。

### 5. 动态行情计算

只有固定查询和目录已有的 K 日指标无法表达所需复杂行情计算时才使用。

```text
rN = stock.quote.dynamic_cal(
  k, filter, fields, task, order, limit, realtime
) -> code, name, calculated_fields
```

`task` 简洁描述计算目标，`fields` 只列真实所需行情字段。

## 组合查询

- 一个问题可由不同类型的 API 组成。先列全用户明确要求的业务事实；能够从目录选定 API 的事实应一次组成最小查询流。下一 step 只依赖前一步身份范围时直接用 `stepN.column`，不需要先观察真实列表。系统逐步执行并保存结果，不会因为某个展示字段为空而跳过后续已知目标。不要因为进入下一指标而重新查询已确定的主体范围。
- filter 中的每个条件必须来自用户约束、目录规定或上游身份范围。排序与 limit 只负责选择顺序和数量，不自动产生正值、非空或阈值条件。
- `working_set` 中每个条目都代表已经成功执行的一步，并保留该步的自然语言目标。决定下一步前先检查目标、列覆盖和依赖；证据已经足够时直接回答，不继续探索。
- 某一子指标为 `null`、空值或零行时，如实说明该子指标不可得；仍可执行与它独立、且目录明确支持的其他子目标。不得改查无关 subject、dataview、证券或新闻来填补缺失事实。
