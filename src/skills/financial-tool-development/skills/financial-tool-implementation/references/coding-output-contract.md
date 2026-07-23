# Coding output contract

返回当前动态实现、真实技术测试记录和必要说明：

- `tool_contract` 记录代码实际实现的工具标识、显示名称、说明和公开输入输出字段，供外层系统生成运行时契约。
- 首次实现时，`implementation.modules[].source_code` 返回系统保存并动态加载的源码文本。
- 修改已有模块时，直接编辑隔离工作区文件，`source_code` 可为空；外层系统从工作区回收真实源码，不要在最终 JSON 中重复整份代码。
- `implementation.entry_module` 指向包含 `run(inputs: dict) -> dict` 的入口模块。
- `implementation_explanation` 说明实际代码的核心流程和关键模块，不复述完整 Design。
- `implementation_review` 明确记录需求、Design 与 Code 的一致项和偏差；业务效果仍由用户根据真实结果判断。
- `tests` 只记录真实执行过的技术测试；没有运行使用 `not_run`，不得臆造通过。
- `tests[].actual_output_json` 保存精简后的真实输出，不复制大对象。
- `tests[].checks` 只说明是否正常执行和是否符合公开输入输出契约。
- `tests[].evidence` 记录核心中间指标、日志摘要和判断路径；零命中时也要给出能够解释零命中的必要数据。
- 合法的空列表、零命中或数据不足结果仍可记为技术测试通过；只有执行错误、契约错误或代码偏离需求与 Design 才属于失败。
- `technical_summary` 汇总已验证范围、最终技术结论和仍未解决的问题。技术完成由 Codex 在本次任务内部判断，外层主控不重新审查代码。
- `issues` 只写已经确认、阻碍当前实现的技术问题，不负责切换业务阶段。
- `sample_input_json` 提供外层真实样例运行所需的输入。
