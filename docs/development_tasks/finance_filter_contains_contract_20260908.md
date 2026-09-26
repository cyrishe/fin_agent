# 筛选协议：有限操作集与 contains

2026-09-08。根据最新反馈，将文本匹配的推荐写法改为 `field.contains('文本')`。这取代上一轮推荐的 `'文本' in field`；旧形式仍兼容。

## 给模型的有限集合

| 类别 | 支持并推荐的写法 |
|---|---|
| 比较 | `field == value`、`!=`、`>`、`>=`、`<`、`<=` |
| 文本包含 | `field.contains('文本')` |
| 集合筛选 | `field in [...]`、`field not in [...]` |
| 条件组合 | `and`、`or`、括号 |
| 空值判断 | `field is None`、`field is not None` |

字段取自当前方法；值为带引号的文本、数字、`True/False` 或 `None`。集合还可以使用 `rN.column` / `stepN.column`。

`contains` 接收一个文本字面量，表示大小写敏感的字面包含；百分号、下划线等也按普通字符处理。这里定义的是有限查询协议，不是开放 Python 方法调用。

## 定义放在哪里

- 唯一模型说明源：`src/tools/finance_data/catalog/api_view_catalog.json` 的 `filter_syntax`，版本 `2026-09-08-filter-contains-v3`。
- 具体执行包按现有装配机制加载一次；第一层数据索引不增加完整语法。
- 明细、窗口、成分、聚合沿用相同条件树，不分别维护字符串转换规则。

实际说明原文：

```text
filter 为条件表达式字符串，支持以下写法：比较 == != > >= < <=；组合 and/or 与括号；文本包含 field.contains('文本')（单个文本字面量，大小写敏感）；集合 field in [...] / field not in [...]，列表也可用 rN.column 或 stepN.column；空值 field is None / field is not None。字段取自当前方法，值使用带引号的文本、数字、True/False 或 None。
```

## 执行实现

解析器识别确定的 `field.contains('文本')` AST 形状，生成现有节点：

```json
{"field":"name","operator":"contains","value":"中金"}
```

该节点沿用已有参数化 SQL 与内存筛选实现。没有 `eval`、方法动态调用或正则改写；SQL 字段取自 provider 映射，文本作为参数传入。未开放 `.lower()`、前后缀匹配或其他函数。

原 `'文本' in field` 兼容输入生成相同节点。条件树序列化及引用绑定采用新的 contains 规范形式。旧 `=`、LIKE 等原有兼容边界不变；未新增另一套 contains 拼写。

## 与前一轮改动的关系

保留已完成的研报核心观点说明、operation/执行入口区分、基础信息范围收敛和预算最后一次调用的边界修复。没有按具体公司增加关键词规则，也没有强行消除首次生成错误。

历史提示词和原始评测未改写；本次修改不能反向解释为历史运行时已支持 contains。

## 验证边界

测试覆盖规范写法、历史输入兼容、未定义调用拒绝、字段合法性、引号与 SQL 特殊字符、SQL/内存结果一致性、窗口筛选、研报聚合、成分聚合及跨步骤集合引用。

结果：上述 Python 定向回归 206 项通过，DSH loop 41 项通过；共 247 项。`git diff --check` 通过。

这是本地协议和实现回归，不是模型成功率评测，也没有新做服务器数据库取数。本次未提交、部署或重启。前一轮扩展组的并发时序断言问题仍单列在原报告，本轮没有顺带修改并发机制。
