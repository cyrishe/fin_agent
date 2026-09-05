# 金融 API 调用方式

每次查询固定使用：

```text
result_name = api_name(arguments) -> output_fields
```

代码通过 `custom_tool_sdk.finance_query(request=request_text)` 执行查询。返回后先检查顶层 `ok`，数据行读取顶层 `data`。

## 五类通用 API

### 1. 基础查询

行情、资金、估值、财务、基础信息等普通 dataview 均使用：

```text
result_name = subject.dataview(filter, order, limit, realtime) -> fields
```

参数没有需要时可以省略。输出字段必须来自当前 dataview。

### 2. K 日指标

API 名称按 Catalog 中的字段—方法组合动态形成：

```text
result_name = subject.dataview.kd_<field>_<method>(
    k, filter, order, limit, realtime
) -> code, name, value as alias
```

`k` 必填；`field` 和 `method` 必须来自当前 dataview 的定义。

### 3. 成分关系

```text
result_name = subject.constitution(
    filter, order, limit, realtime
) -> subject_code, subject_name, stock_code, stock_name
```

实际标识字段以当前 constitution dataview 为准。

### 4. 聚合查询

```text
result_name = subject.dataview.agg(
    filter, agg, group_by, order, limit
) -> group_fields, aggregate_result
```

一次调用计算一个聚合目标。普通 `dataview.agg` 聚合该 dataview
自身的行集；`constitution.agg` 聚合一个主体的成分股指标，两者不要混用。
多个指标由 Python 分别查询并组合。

研报正文、评级、观点、风险和目标价明细使用 `stock.report`（一行一篇研报）；
研报中的标准预测/实际指标使用 `stock.report_metric`（一行一个指标事实）。
两者都支持目录中定义的 `.agg`。按 `code,name` 分组并排序/限量即可扫描公司，
不新增 `report.scan`；研报接口不使用 `realtime`。

### 5. 动态行情计算

固定查询和 K 日指标无法表达复杂行情计算时使用：

```text
result_name = stock.quote.dynamic_cal(
    k, filter, fields, task, order, limit, realtime
) -> code, name, calculated_fields
```

`task` 用精炼自然语言描述计算目标，`fields` 只列所需的真实行情字段。

## 查找具体 API

以上只说明 API 的共同形态。具体名称、字段、参数、规则和示例从 Catalog 查找：

1. 读取 `api_catalog/index.json` 定位 subject。
2. 读取 `api_catalog/subjects/<subject>/index.json` 定位 dataview。
3. 只打开相关的 `api_catalog/subjects/<subject>/<dataview>.json`。
4. 使用其中 `methods[].call`、`methods[].args`、`methods[].rules` 和 `methods[].examples`。

不要读取 provider 源码猜接口。
