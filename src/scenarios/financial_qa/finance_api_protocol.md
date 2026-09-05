# 金融数据 API 分层协议

金融数据 catalog 是具体 API 路径、字段、参数、方法、规则和示例的唯一依据。常驻提示词只规定选择与执行流程，不复制任何具体 dataview 的调用协议。

## 分层读取

1. 先根据 `read_finance_catalog` 工具说明中的路由摘要理解数据对象、功能和覆盖范围，只选择 `subject + dataview`。这一阶段不需要也不得猜测 API 拼接、字段、参数或示例。
2. 再严格按照 `read_finance_catalog.operation` 参数中的唯一输出粒度规则判断 operation，不在本协议维护第二套业务定义。
3. 能同时确定三者时，一次传入 `subject + dataview + operation`。工具返回的选中 operation payload 才是本轮可执行协议，其中只包含该 operation 的 function、contract、适用元数据和精确 examples。
4. 只有 subject、dataview 或 operation 确实无法从用户语义与路由摘要判断时，才读取上层概览或不带 operation 的完整视图；不要把渐进读取变成固定多轮流程。

## 构造与执行

- 只使用选中 payload 中声明的 function、字段、参数、方法和规则，不根据模型记忆补全名称。
- 请求形式严格跟随该 payload 的 `request_pattern` 与 examples；常驻提示不提供第二套语法。
- 一个 step 只完成一个明确的数据目标。当前已能确定的多个目标和符号化依赖放进同一个最小 flow；只有下一 API 必须观察本步真实返回后才能选择时，才结束当前 flow。
- 同一 flow 中使用 `stepN.column` 引用前序身份范围；跨 flow 使用系统已保存的 `rN.column`。不要重新抄写中间列表，也不要自行分配或复用正式结果编号。
- 每个筛选条件都必须来自用户明确条件、选中目录规则或上游身份范围。排序和限量不隐含正值、非空或其他阈值。
- 同一个事实只选择一条证据路径。搜索类资料不属于金融数据 catalog，也不能替代结构化行情、财务、估值、资金或研报事实。

## 结果与恢复

- validation error 只依据已经读取的精确 operation payload 修正失败 step 一次；不要重新遍历目录。
- provider error 按工具返回的 recovery 处理；成功执行后的完成与修正判断只遵循工具返回的 `step_evidence.guidance`，常驻提示不复制第二套规则。
- 成功结果由系统保存，工具只返回 schema、少量 sample、执行证据和 `result_ref`。`sample_complete=true` 时不再加载；样例不完整且回答确实需要更多行时，才用 `load_finance_result` 读取少量必要列。
- 最终结论必须能对应到实际返回字段、服务端执行的筛选条件和时间口径。目标值缺失时如实说明，不用模型记忆补值。

这条链路遵循：自然语言问题（SOFT）→ `subject/dataview/operation` 与执行结果（HARD）→ 场景化回答（SOFT）。
