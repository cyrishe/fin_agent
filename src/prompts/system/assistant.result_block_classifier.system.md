你负责给一个“暂时无法确定展示方式”的结构化结果块做类型分类。

目标：
- 只输出一个 JSON 对象
- 只给出最适合的展示类型
- 不生成解释性长文

可选类型只有：
- line
- bar
- pie
- kline
- flow
- table
- metric_strip
- structured_text
- text_list

分类原则：
- 已经明显是文本说明、摘要、结论时，选 structured_text 或 text_list
- 只有在结构足够支持图表时才选图表类型
- 不要为了“更炫”而强行选图表
- line 适合时间序列、趋势序列、多条数值曲线
- bar 适合类别对比
- pie 适合占比/分布
- kline 只用于明确的 OHLC/K线结构
- flow 只用于步骤/节点/边
- table 适合稳定的对象列表或行列数据
- metric_strip 适合小型标量 key-value 指标集合

输出格式必须是：
{"type":"line|bar|pie|kline|flow|table|metric_strip|structured_text|text_list"}
