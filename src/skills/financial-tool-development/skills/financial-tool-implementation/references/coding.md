# 动态实现约定

- `CONTEXT.design` 或 `CONTEXT.design_ref` 是权威设计；提供路径时按需读取。
- `api_catalog/task_context.json` 是系统依据当前设计整理出的优先 API 参考，包含 dataview、字段和相应请求样例；优先读取它。没有该文件或仍缺信息时，再读 `api_catalog/index.json` 和相关 subject/dataview。
- `custom_tool_sdk.md` 提供 `finance_query`、`info` 和 `debug` 的运行接口。
- 修改已有实现时，搜索 `CONTEXT.current_implementation` 指向的模块；根据 `coding_feedback` 和 `test_feedback` 局部修改。
- 首次实现也在 `CONTEXT.current_implementation.module_files` 指向的隔离入口文件中完成。`implementation/module_plan.json` 描述入口内需要实现和分别验证的逻辑函数组。

实现固定提供 `run(inputs: dict) -> dict`，只通过 `custom_tool_sdk.finance_query` 获取金融数据。检查查询返回的 `ok`，从顶层 `data` 读取数据。返回值必须可 JSON 序列化。

每完成一个逻辑函数组，按 `CODING_WORKSPACE.md` 在 `scratch/` 做聚焦验证，并简短报告真实结果。入口至少验证一个正常数据样例和一个输入错误、空数据或缺字段样例，确认所有返回路径的字段及类型符合 Design。若 Design 无法用声明类型诚实表达无数据结果，应报告该冲突，不得发明哨兵值掩盖。`info` 记录阶段和数量汇总；`debug` 记录影响结论的公式输入、阈值、实际值和核心中间指标。系统负责保存、真实测试、启用和发布，模型不得自行宣称业务逻辑正确。

输出字段示例见 [coding-output-contract.md](coding-output-contract.md)。
