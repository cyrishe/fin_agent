# 金融数据 API 分层协议

金融数据 catalog 是具体 API 路径、字段、参数、方法、规则和示例的唯一依据。

## 分层读取

- `subject` 定义对象，`dataview` 定义数据，`operation` 选择方法。五类方法的用途由 `read_finance_catalog` 统一说明。
- 根据路由摘要定位，明确时一次提交 `subject + dataview + operation`，取得当前方法的完整执行包；定位有歧义时读取对应概览。
- 执行包将用途、精确入口、调用格式、参数、字段、特殊口径和示例组装在一起。请求使用其中的 `api_name` 与 `request_pattern`，字段和参数以本包声明为准。

## 组合执行

- 用一个 `finance_query` flow 提交当前可确定的目标及其依赖，每个 step 对应一个数据目标和一条请求。需要观察结果后才能确定的后续目标留给下一 flow。
- 同一 flow 用 `field in stepN.column` 引用前序身份范围；跨 flow 用 `field in rN.column`。结果列表示集合，标量比较使用已取得的单值；正式结果编号由系统分配。
- 条件表达用户要求、目录约定和上游对象范围。金融公式、窗口与数据源适配由工具实现。
- `data_request_complete=true` 表示本 flow 已覆盖全部取数目标；尚有依赖结果的后续目标时为 false。

## 执行衔接

工具返回成功结果、执行证据或 recovery。按当前返回处理已完成步骤与可恢复的失败步骤；结果读取和证据使用见金融数据结果与证据说明。

自然语言目标（SOFT）→ 分层目录、执行请求与结果（HARD）→ 场景化回答（SOFT）。
