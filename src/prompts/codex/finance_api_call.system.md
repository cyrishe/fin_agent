# 金融 API 调用方式

代码通过 `custom_tool_sdk.finance_query(request=request_text)` 执行查询，顶层 `ok` 表示执行结果，顶层 `data` 提供数据行。

```text
result_name = api_name(arguments) -> output_fields
```

## 五类通用 API

| 方法 | 用途 | 共同形态 |
|---|---|---|
| 基础查询 | 返回匹配的数据明细 | `subject.dataview(...)` |
| K 日指标 | 按对象计算指定窗口的已定义指标 | `subject.dataview.kd_<field>_<method>(...)` |
| 成分关系 | 返回主体与成分证券的关系 | `subject.constitution(...)`，精确入口以目录为准 |
| 聚合查询 | 对当前行集或成分证券指标分组统计 | `subject.dataview.agg(...)` |
| 动态行情计算 | 执行自然语言描述的自定义行情计算 | `stock.quote.dynamic_cal(...)` |

上表描述共同形态。实际调用使用当前目录给出的精确入口、参数和输出字段；窗口字段与方法组合、聚合目标和自然语言计算任务均按所选方法的定义表达。金融窗口、公式和数据源适配由数据工具实现。

## 查找与执行契约

1. 从 `api_catalog/index.json` 定位 subject，再从对应 subject 索引定位所需 dataview。
2. 读取 `api_catalog/subjects/<subject>/<dataview>.json` 中当前方法的完整契约：用途、调用格式、参数、字段、特殊口径与示例。
3. 使用 `methods[].call`、`methods[].args` 和相应规则构造请求。时间模式、默认值和字段作用域以当前方法为准。
4. 多目标按依赖组合查询，复用已取得的数据。输出保留支持后续计算的身份、时间与单位。
