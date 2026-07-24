# 动态实现参考

- `CONTEXT.requirement_brief` 是需求说明，`CONTEXT.design` 或 `CONTEXT.design_ref` 是设计方案；提供路径时按需读取。
- `api_catalog/task_context.json` 是系统依据当前设计整理出的优先 API 参考，包含 dataview、字段和相应请求样例；优先读取它。没有该文件或仍缺信息时，再读 `api_catalog/index.json` 和相关 subject/dataview。
- `DYNAMIC_TOOL_TEMPLATE.py` 是平台统一动态模块示例。保留它的 `run(inputs: dict) -> dict`、结果组装和 `key_process_info` 模式，内部函数与业务字段按当前设计调整。
- `custom_tool_sdk.md` 提供 `finance_query`、`info` 和 `debug` 的运行接口。
- 修改已有实现时，搜索 `CONTEXT.current_implementation` 指向的模块；根据 `coding_feedback` 和 `test_feedback` 局部修改。
- 首次实现也在 `CONTEXT.current_implementation.module_files` 指向的隔离入口文件中完成。`implementation/module_plan.json` 描述入口内需要实现和分别验证的逻辑函数组。

实现固定提供 `run(inputs: dict) -> dict`，只通过 `custom_tool_sdk.finance_query` 获取金融数据。检查查询返回的 `ok`，从顶层 `data` 读取数据。返回值必须可 JSON 序列化。

金融查询串严格沿用 API Catalog 示例的语法：`filter` 整体使用引号，filter 内的代码、日期、名称等值不再嵌套添加转义引号。完成请求构造函数后，对照真实示例检查一次生成的完整 request。

每个返回结果都包含 `key_process_info` 对象。它是面向用户的核心过程证据：只放解释当前结论所需的样本数、公式输入、中间指标、阈值和判断条件；错误或数据不足时放已经取得的数据量和失败位置。相同核心信息可通过一次 `debug("key_process_info", key_process_info)` 写入执行日志。

按 `CODING_WORKSPACE.md` 在 `scratch/` 做聚焦验证。入口真实运行少量代表性输入，优先覆盖主路径，并在适合时覆盖一个边界路径。构造数据必须遵守字段的真实格式和语义。确认代码可执行、结果可序列化、每条路径都有 `key_process_info`，并静态核对需求、设计和代码，尤其核对查询条件、排序、数量限制与时间窗口的组合。若 Design 无法用公开契约诚实表达无数据结果，应如实说明，不得发明哨兵值掩盖。

`info` 记录阶段和数量汇总；`debug` 记录影响结论的公式输入、阈值、实际值和核心中间指标。系统负责保存、再次运行、启用和发布；模型不替用户判断业务效果。
